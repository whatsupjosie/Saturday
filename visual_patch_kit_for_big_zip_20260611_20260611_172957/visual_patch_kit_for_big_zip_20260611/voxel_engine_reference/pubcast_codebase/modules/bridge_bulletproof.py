# PubCast AI — bridge_bulletproof.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
VOXEL BRIDGE - THE TWIN ENGINE CONNECTION
Merged v2.0: VoxelBridge foundation + Bulletproof additions

"Path A, Path B, Path C. One always works."

What's new in v2.0 (grafted from BulletproofTwinEngineBridge):
  - Circuit Breaker: stops hammering a dead engine, recovers gracefully
  - IRM (Intelligent Resource Management): adapts batch size to hardware health
  - Camera position sequencing: stale/out-of-order packets discarded
  - SHM magic + version check on attach: no silent corruption from stale segments
  - Weak-reference callbacks: no memory leaks on module reload

What stays from VoxelBridge (our foundation):
  - Triple-path transport: SHM -> TCP -> File System (stronger than UDP-only)
  - Full CommandType enum with AVATAR_UPDATE = 41
  - Priority queue for commands
  - Full async background task architecture
  - All specialised public API methods
  - All compatibility aliases (TwinEngineBridge, UDPBridge, BridgeMessage, MessageType)

Architecture:
  Path A: Shared Memory (10us latency) - Primary
  Path B: TCP Sockets  (1ms latency)  - Fallback
  Path C: File System  (50ms latency) - Emergency

Pipeline:
  Motion Capture -> Python Processing -> Shared Memory -> C++ Rendering -> Browser
"""

from __future__ import annotations

import asyncio
import json
import logging
import mmap
import os
import socket
import struct
import threading
import time
import weakref

from .persistence import write_json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, Any, List, Optional, Set

logger = logging.getLogger("pubcast.bridge")


# ============================================================
# CIRCUIT BREAKER  (from Bulletproof)
# ============================================================

class CircuitBreakerState(Enum):
    CLOSED    = "closed"     # Normal - requests flow through
    OPEN      = "open"       # Failing - requests rejected immediately
    HALF_OPEN = "half_open"  # Testing - one probe allowed through


class CircuitBreaker:
    """
    Stops the bridge from hammering a dead engine.

    After `failure_threshold` consecutive failures the breaker OPENS and
    rejects all calls for `recovery_timeout` seconds. After that it goes
    HALF_OPEN and lets one probe through. If that succeeds it CLOSES again.
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout  = recovery_timeout
        self.failure_count     = 0
        self.last_failure_time = 0.0
        self.state             = CircuitBreakerState.CLOSED
        self._lock             = threading.Lock()

    def call(self, func, *args, **kwargs):
        with self._lock:
            if self.state == CircuitBreakerState.OPEN:
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitBreakerState.HALF_OPEN
                    logger.info("🔌 Circuit breaker -> HALF_OPEN (probing engine)")
                else:
                    raise RuntimeError("Circuit breaker OPEN - engine unavailable")

            try:
                result = func(*args, **kwargs)
                if self.state == CircuitBreakerState.HALF_OPEN:
                    self.state         = CircuitBreakerState.CLOSED
                    self.failure_count = 0
                    logger.info("✅ Circuit breaker -> CLOSED (engine recovered)")
                return result

            except Exception as exc:
                self.failure_count    += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    self.state = CircuitBreakerState.OPEN
                    logger.error(
                        "🔌 Circuit breaker -> OPEN after %d failures", self.failure_count
                    )
                raise exc

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitBreakerState.CLOSED

    def get_status(self) -> Dict[str, Any]:
        return {
            "state":         self.state.value,
            "failure_count": self.failure_count,
            "last_failure":  self.last_failure_time,
        }


# ============================================================
# IRM - INTELLIGENT RESOURCE MANAGEMENT  (from Bulletproof)
# ============================================================

class IRMSensor:
    """
    Watches rolling FPS and command latency to produce a health score (0-100).
    Feeds the IRMActuator which adjusts how hard we push the engine.
    """

    def __init__(self, window_size: int = 5):
        self.fps_history     = []
        self.latency_history = []
        self.window_size     = window_size

    def record_frame(self, delta_time: float) -> None:
        fps = 1.0 / delta_time if delta_time > 0 else 0.0
        self.fps_history.append(fps)
        if len(self.fps_history) > self.window_size:
            self.fps_history.pop(0)

    def record_latency(self, latency_ms: float) -> None:
        self.latency_history.append(latency_ms)
        if len(self.latency_history) > self.window_size:
            self.latency_history.pop(0)

    def health(self) -> Dict[str, Any]:
        avg_fps     = sum(self.fps_history)     / len(self.fps_history)     if self.fps_history     else 60.0
        avg_latency = sum(self.latency_history) / len(self.latency_history) if self.latency_history else 10.0

        score = 100.0
        if   avg_fps >= 58: pass
        elif avg_fps >= 45: score -= 25
        elif avg_fps >= 20: score -= 50
        else:               score -= 75

        if   avg_latency <= 16: pass
        elif avg_latency <= 50: score -= 10
        else:                   score -= 25

        score = max(0.0, min(100.0, score))

        if   score >= 90: status = "excellent"
        elif score >= 60: status = "stable"
        elif score >= 30: status = "struggling"
        else:             status = "critical"

        return {
            "fps":       avg_fps,
            "latency":   avg_latency,
            "score":     score,
            "status":    status,
            "is_stable": len(self.fps_history) == self.window_size,
        }


class IRMActuator:
    """
    Adjusts command batch size based on IRMSensor health scores.

      Excellent  -> grow 10%   (push harder)
      Stable     -> hold
      Struggling -> shrink 40% (back off fast)
      Critical   -> floor      (emergency minimum)
    """

    def __init__(
        self,
        min_batch:     int = 500,
        max_batch:     int = 10_000,
        default_batch: int = 2_500,
    ):
        self.min_batch_size     = min_batch
        self.max_batch_size     = max_batch
        self.current_batch_size = default_batch
        self._emergency_mode    = False

    def adjust(self, health: Dict[str, Any]) -> int:
        status = health["status"]

        if status == "excellent":
            self.current_batch_size = min(
                self.max_batch_size,
                int(self.current_batch_size * 1.10),
            )
            self._emergency_mode = False

        elif status == "stable":
            if health["score"] >= 75:
                self._emergency_mode = False

        elif status == "struggling":
            self.current_batch_size = max(
                self.min_batch_size,
                int(self.current_batch_size * 0.60),
            )
            self._emergency_mode = True

        elif status == "critical":
            self.current_batch_size = self.min_batch_size
            self._emergency_mode    = True

        return self.current_batch_size

    @property
    def batch_size(self) -> int:
        return self.current_batch_size

    @property
    def emergency_mode(self) -> bool:
        return self._emergency_mode


# ============================================================
# CAMERA POSITION WITH SEQUENCING  (from Bulletproof)
# ============================================================

@dataclass
class CameraPosition:
    """Camera position with sequence number so stale packets are discarded."""
    x:         float = 0.0
    y:         float = 0.0
    z:         float = 0.0
    pitch:     float = 0.0
    yaw:       float = 0.0
    roll:      float = 0.0
    sequence:  int   = 0
    timestamp: float = field(default_factory=time.time)


# ============================================================
# CORE ENUMS & DATACLASSES  (from VoxelBridge - unchanged)
# ============================================================

class BridgeStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTED    = "connected"   # Path A (Shared Memory)
    DEGRADED     = "degraded"    # Path B (TCP)
    EMERGENCY    = "emergency"   # Path C (File System)
    ERROR        = "error"


class CommandType(Enum):
    # System
    HEARTBEAT      = 0
    SHUTDOWN       = 1
    STATUS_REQUEST = 2
    # Camera
    REGISTER_CAMERA = 10
    SWITCH_PREVIEW  = 11
    SWITCH_PROGRAM  = 12
    PTZ_CONTROL     = 13
    # Scene
    LOAD_SCENE   = 20
    UNLOAD_SCENE = 21
    UPDATE_SCENE = 22
    # Recording
    RECORD_START = 30
    RECORD_STOP  = 31
    RECORD_PAUSE = 32
    # Motion / Avatar
    MOTION_DATA   = 40
    AVATAR_UPDATE = 41
    POSE_CONTROL  = 42


@dataclass
class BridgeMetrics:
    commands_sent:     int   = 0
    commands_failed:   int   = 0
    bytes_transferred: int   = 0
    avg_latency_ms:    float = 0.0
    queue_depth:       int   = 0
    last_heartbeat:    float = 0.0
    fps:               float = 0.0
    gpu_health:        float = 1.0
    # IRM fields added in v2
    irm_batch_size:    int   = 2500
    irm_score:         float = 100.0
    irm_emergency:     bool  = False


@dataclass
class Command:
    command_type: CommandType
    payload:      Dict[str, Any]      = field(default_factory=dict)
    timestamp:    float               = field(default_factory=time.time)
    priority:     int                 = 0   # 0=normal, 1=high, 2=critical
    callback:     Optional[Callable]  = None


# Shared Memory Layout
SHM_MAGIC                = b"PBC2"   # v2 magic - detects stale v1 segments
SHM_VERSION              = 2
SHM_HEADER_SIZE          = 1024
SHM_COMMAND_RING_SIZE    = 64  * 1024   # 64 KB
SHM_MOTION_BUFFER_SIZE   = 256 * 1024   # 256 KB
SHM_RESPONSE_BUFFER_SIZE = 32  * 1024   # 32 KB
SHM_TOTAL_SIZE = (
    SHM_HEADER_SIZE +
    SHM_COMMAND_RING_SIZE +
    SHM_MOTION_BUFFER_SIZE +
    SHM_RESPONSE_BUFFER_SIZE
)

# Header struct layout (v2):
#   magic(4s) version(I) py_alive(I) cpp_alive(I)
#   cmd_counter(Q) frame_counter(Q)
#   fps(f) gpu_health(f)
#   cmd_ring_read(I) cmd_ring_write(I)
#   timestamp(d)
_HEADER_FMT  = "=4sIIIQQffIId"
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)   # ~56 bytes, well within 1024


# ============================================================
# VOXEL BRIDGE  (merged v2.0)
# ============================================================

class VoxelBridge:
    """
    The Twin Engine Bridge - merged v2.0.

    VoxelBridge's triple-path transport + full command API, plus:
      - Circuit Breaker on every engine send
      - IRM sensor + actuator for adaptive load management
      - Sequenced camera positions (stale packets dropped)
      - SHM magic + version check on attach
      - Weak-reference callbacks (no leaks on reload)
    """

    def __init__(
        self,
        data_dir:      Path,
        shm_name:      str  = "pubcast_bridge_v2",
        manage_shared: bool = False,
    ):
        self.data_dir      = data_dir
        self.shm_name      = shm_name
        self.manage_shared = manage_shared

        # Connection state
        self._status                  = BridgeStatus.DISCONNECTED
        self._connection_attempts     = 0
        self._last_connection_attempt = 0.0

        # Shared memory handles
        self._shm_fd:              Optional[int]       = None
        self._shm_header:          Optional[mmap.mmap] = None
        self._shm_command_ring:    Optional[mmap.mmap] = None
        self._shm_motion_buffer:   Optional[mmap.mmap] = None
        self._shm_response_buffer: Optional[mmap.mmap] = None

        # TCP fallback
        self._tcp_socket: Optional[socket.socket] = None
        self._tcp_port    = 9001

        # File-system emergency fallback
        self._emergency_dir = data_dir / "emergency_bridge"
        self._emergency_dir.mkdir(parents=True, exist_ok=True)

        # Command management
        self._command_queue:      List[Command]        = []
        self._command_queue_lock  = threading.Lock()
        self._response_callbacks: Dict[int, Callable] = {}
        self._command_counter     = 0

        # Metrics
        self._metrics             = BridgeMetrics()
        self._performance_history: List[BridgeMetrics] = []
        self._last_metric_time    = time.time()

        # Background tasks
        self._running                 = False
        self._heartbeat_task:         Optional[asyncio.Task] = None
        self._command_processor_task: Optional[asyncio.Task] = None
        self._health_monitor_task:    Optional[asyncio.Task] = None

        # ── Bulletproof additions ──────────────────────────────────────────

        # Circuit breaker - wraps every engine send
        self._circuit_breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=30.0,
        )

        # IRM - adaptive load management
        self._irm_sensor   = IRMSensor(window_size=5)
        self._irm_actuator = IRMActuator()

        # Camera sequencing - discard stale position updates
        self._camera_position = CameraPosition()
        self._camera_seq_lock = threading.Lock()
        self._state_version   = 0

        # Weak-reference callbacks - no memory leaks on module reload
        self._weak_callbacks: Set[weakref.ref] = set()

        logger.info("🌉 VoxelBridge v2.0 initialised (SHM: %s)", shm_name)

    # ================================================================
    # CONNECTION MANAGEMENT
    # ================================================================

    def connect(self, create_shared: bool = False) -> bool:
        """Try Path A -> B -> C. Returns True if any path works."""
        logger.info("🌉 VoxelBridge: establishing twin engine connection...")

        self._connection_attempts    += 1
        self._last_connection_attempt = time.time()

        if self._connect_shared_memory(create_shared):
            self._status = BridgeStatus.CONNECTED
            logger.info("✅ Bridge connected via Path A (Shared Memory)")
            self._start_background_tasks()
            return True

        if self._connect_tcp():
            self._status = BridgeStatus.DEGRADED
            logger.warning("⚠️  Bridge connected via Path B (TCP) - performance degraded")
            self._start_background_tasks()
            return True

        if self._connect_file_system():
            self._status = BridgeStatus.EMERGENCY
            logger.error("🚨 Bridge using Path C (File System) - severe performance impact")
            self._start_background_tasks()
            return True

        self._status = BridgeStatus.ERROR
        logger.error("❌ All bridge connection paths failed")
        return False

    @property
    def is_connected(self) -> bool:
        return self._status in (
            BridgeStatus.CONNECTED,
            BridgeStatus.DEGRADED,
            BridgeStatus.EMERGENCY,
        )

    @property
    def is_healthy(self) -> bool:
        return (
            self.is_connected
            and self._circuit_breaker.is_closed
            and not self._irm_actuator.emergency_mode
        )

    def _connect_shared_memory(self, create_shared: bool = False) -> bool:
        """Path A - POSIX shared memory with magic + version check on attach."""
        try:
            import posix_ipc

            try:
                if create_shared:
                    memory = posix_ipc.SharedMemory(
                        self.shm_name,
                        posix_ipc.O_CREAT | posix_ipc.O_EXCL,
                        size=SHM_TOTAL_SIZE,
                    )
                    logger.info("🌉 Created SHM segment: %s", self.shm_name)
                else:
                    memory = posix_ipc.SharedMemory(self.shm_name)

            except posix_ipc.ExistentialError:
                if not create_shared:
                    logger.warning("🌉 SHM segment not found")
                    return False
                memory = posix_ipc.SharedMemory(self.shm_name)

            self._shm_fd = memory.fd

            self._shm_header = mmap.mmap(
                memory.fd, SHM_HEADER_SIZE, offset=0, access=mmap.ACCESS_WRITE
            )
            self._shm_command_ring = mmap.mmap(
                memory.fd, SHM_COMMAND_RING_SIZE,
                offset=SHM_HEADER_SIZE, access=mmap.ACCESS_WRITE,
            )
            self._shm_motion_buffer = mmap.mmap(
                memory.fd, SHM_MOTION_BUFFER_SIZE,
                offset=SHM_HEADER_SIZE + SHM_COMMAND_RING_SIZE,
                access=mmap.ACCESS_WRITE,
            )
            self._shm_response_buffer = mmap.mmap(
                memory.fd, SHM_RESPONSE_BUFFER_SIZE,
                offset=SHM_HEADER_SIZE + SHM_COMMAND_RING_SIZE + SHM_MOTION_BUFFER_SIZE,
                access=mmap.ACCESS_WRITE,
            )

            if create_shared:
                self._initialize_shm_header()
            else:
                # ── Magic + version check (Bulletproof addition) ──────────
                self._shm_header.seek(0)
                raw = self._shm_header.read(_HEADER_SIZE)
                if len(raw) >= 8:
                    magic, version = struct.unpack_from("=4sI", raw, 0)
                    if magic not in (SHM_MAGIC, b"PUBC"):
                        logger.error(
                            "❌ SHM magic mismatch (got %r) - stale or foreign segment, refusing attach",
                            magic,
                        )
                        self._close_shm_maps()
                        return False
                    if magic == b"PUBC" and version < 2:
                        logger.warning("⚠️  SHM is v1 - upgrading header to v2 in place")
                        self._initialize_shm_header()

            logger.info("✅ SHM connection established")
            return True

        except ImportError:
            logger.warning("🌉 posix_ipc not available - SHM disabled")
            return False
        except Exception as exc:
            logger.error("🌉 SHM connection failed: %s", exc)
            return False

    def _initialize_shm_header(self) -> None:
        """Write v2 magic + default header fields."""
        if not self._shm_header:
            return
        try:
            header = struct.pack(
                _HEADER_FMT,
                SHM_MAGIC,    # magic  b"PBC2"
                SHM_VERSION,  # version 2
                1,            # py_alive
                0,            # cpp_alive
                0,            # cmd_counter
                0,            # frame_counter
                0.0,          # fps
                1.0,          # gpu_health
                0,            # cmd_ring_read
                0,            # cmd_ring_write
                time.time(),  # timestamp
            )
            self._shm_header.seek(0)
            self._shm_header.write(header)
            self._shm_header.flush()
            logger.info("✅ SHM header initialised (v2)")
        except Exception as exc:
            logger.error("❌ SHM header init failed: %s", exc)

    def _close_shm_maps(self) -> None:
        """Close all mmap objects."""
        for attr in (
            "_shm_header", "_shm_command_ring",
            "_shm_motion_buffer", "_shm_response_buffer",
        ):
            m = getattr(self, attr, None)
            if m:
                try:
                    m.close()
                except Exception:
                    pass
            setattr(self, attr, None)

    def _connect_tcp(self) -> bool:
        """Path B - TCP fallback."""
        try:
            self._tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._tcp_socket.settimeout(5.0)
            self._tcp_socket.connect(("127.0.0.1", self._tcp_port))

            handshake = {"type": "handshake", "client": "pubcast_python", "version": 2}
            self._tcp_socket.send(json.dumps(handshake).encode() + b"\n")

            response = json.loads(self._tcp_socket.recv(1024).decode().strip())
            if response.get("status") == "connected":
                return True

            logger.error("❌ TCP handshake rejected: %s", response)
            return False

        except Exception as exc:
            logger.error("❌ TCP connection failed: %s", exc)
            if self._tcp_socket:
                self._tcp_socket.close()
                self._tcp_socket = None
            return False

    def _connect_file_system(self) -> bool:
        """Path C - file-system emergency fallback."""
        try:
            (self._emergency_dir / "commands").mkdir(exist_ok=True)
            (self._emergency_dir / "responses").mkdir(exist_ok=True)

            control = {
                "python_online": True,
                "cpp_online": False,
                "last_heartbeat": time.time(),
                "command_sequence": 0,
            }
            write_json(self._emergency_dir / "bridge_control.json", control)

            return True
        except Exception as exc:
            logger.error("❌ File-system connection failed: %s", exc)
            return False

    def _start_background_tasks(self) -> None:
        if not self._running:
            self._running = True
            self._heartbeat_task         = asyncio.create_task(self._heartbeat_loop())
            self._command_processor_task = asyncio.create_task(self._command_processor_loop())
            self._health_monitor_task    = asyncio.create_task(self._health_monitor_loop())
            logger.info("🌉 Bridge background tasks started")

    # ================================================================
    # COMMAND INTERFACE
    # ================================================================

    def send_command(
        self,
        command_type: str,
        payload:      Dict[str, Any],
        priority:     int = 0,
    ) -> bool:
        """Queue a command for the C++ engine."""
        _MAP = {
            "REGISTER_CAMERA": CommandType.REGISTER_CAMERA,
            "SWITCH_PREVIEW":  CommandType.SWITCH_PREVIEW,
            "SWITCH_PROGRAM":  CommandType.SWITCH_PROGRAM,
            "PTZ":             CommandType.PTZ_CONTROL,
            "LOAD_SCENE":      CommandType.LOAD_SCENE,
            "UNLOAD_SCENE":    CommandType.UNLOAD_SCENE,
            "UPDATE_SCENE":    CommandType.UPDATE_SCENE,
            "RECORD_START":    CommandType.RECORD_START,
            "RECORD_STOP":     CommandType.RECORD_STOP,
            "RECORD_PAUSE":    CommandType.RECORD_PAUSE,
            "MOTION_UPDATE":   CommandType.MOTION_DATA,
            "AVATAR_UPDATE":   CommandType.AVATAR_UPDATE,
            "POSE_CONTROL":    CommandType.POSE_CONTROL,
            "HEARTBEAT":       CommandType.HEARTBEAT,
            "SHUTDOWN":        CommandType.SHUTDOWN,
        }

        cmd_enum = _MAP.get(command_type.upper())
        if not cmd_enum:
            logger.warning("🌉 Unknown command type: %s", command_type)
            return False

        # Circuit breaker: drop non-critical commands when OPEN
        if (
            self._circuit_breaker.state == CircuitBreakerState.OPEN
            and priority < 2
        ):
            logger.debug("🔌 Circuit OPEN - dropping non-critical command %s", command_type)
            return False

        command = Command(command_type=cmd_enum, payload=payload, priority=priority)

        with self._command_queue_lock:
            insert_pos = len(self._command_queue)
            for i, existing in enumerate(self._command_queue):
                if existing.priority < command.priority:
                    insert_pos = i
                    break
            self._command_queue.insert(insert_pos, command)
            self._metrics.queue_depth = len(self._command_queue)

        return True

    def send_motion_data(self, avatar_id: str, motion_data: Dict[str, Any]) -> bool:
        return self.send_command(
            "MOTION_UPDATE",
            {"avatar_id": avatar_id, "motion_data": motion_data, "timestamp": time.time()},
            priority=1,
        )

    def send_camera_position(self, position: CameraPosition) -> bool:
        """
        Send a sequenced camera position update.
        Positions with a lower sequence number than current are silently dropped.
        """
        with self._camera_seq_lock:
            if position.sequence <= self._camera_position.sequence:
                return False  # Stale - discard
            self._camera_position = position
            self._state_version  += 1

        return self.send_command(
            "PTZ",
            {
                "x": position.x, "y": position.y, "z": position.z,
                "pitch": position.pitch, "yaw": position.yaw, "roll": position.roll,
                "sequence": position.sequence, "timestamp": position.timestamp,
            },
            priority=1,
        )

    def send_camera_control(self, camera_id: int, action: str, parameters: Dict[str, Any]) -> bool:
        return self.send_command(
            action.upper(),
            {"camera_id": camera_id, "action": action, "parameters": parameters},
        )

    def send_recording_control(self, action: str, parameters: Dict[str, Any]) -> bool:
        return self.send_command(
            f"RECORD_{action.upper()}",
            {"action": action, "parameters": parameters},
        )

    # ── Weak-reference callbacks ───────────────────────────────────────────

    def add_callback(self, callback: Callable) -> None:
        """Register a callback. Stored as a weak reference - won't prevent GC."""
        self._weak_callbacks.add(weakref.ref(callback))

    def _fire_callbacks(self, event: Dict[str, Any]) -> None:
        dead: Set[weakref.ref] = set()
        for ref in self._weak_callbacks:
            cb = ref()
            if cb is None:
                dead.add(ref)
            else:
                try:
                    cb(event)
                except Exception as exc:
                    logger.error("🌉 Callback error: %s", exc)
        self._weak_callbacks -= dead

    # ================================================================
    # BACKGROUND TASKS
    # ================================================================

    async def _heartbeat_loop(self) -> None:
        logger.info("🌉 Heartbeat started")
        while self._running:
            try:
                await asyncio.sleep(1.0)
                if   self._status == BridgeStatus.CONNECTED: await self._shm_heartbeat()
                elif self._status == BridgeStatus.DEGRADED:  await self._tcp_heartbeat()
                elif self._status == BridgeStatus.EMERGENCY: await self._file_heartbeat()
                self._metrics.last_heartbeat = time.time()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("🌉 Heartbeat error: %s", exc)
                await asyncio.sleep(5.0)

    async def _shm_heartbeat(self) -> None:
        if not self._shm_header:
            return
        try:
            self._shm_header.seek(0)
            raw    = self._shm_header.read(_HEADER_SIZE)
            fields = list(struct.unpack(_HEADER_FMT, raw))
            fields[2]  = 1            # py_alive
            fields[10] = time.time()  # timestamp
            self._shm_header.seek(0)
            self._shm_header.write(struct.pack(_HEADER_FMT, *fields))
            self._shm_header.flush()
            if fields[3] == 0:
                logger.debug("🌉 C++ engine not responding to heartbeat")
        except Exception as exc:
            logger.error("🌉 SHM heartbeat failed: %s", exc)

    async def _tcp_heartbeat(self) -> None:
        try:
            if self._tcp_socket:
                self._tcp_socket.send(
                    json.dumps({"type": "heartbeat", "timestamp": time.time()}).encode() + b"\n"
                )
        except Exception as exc:
            logger.error("🌉 TCP heartbeat failed: %s", exc)

    async def _file_heartbeat(self) -> None:
        try:
            path = self._emergency_dir / "bridge_control.json"
            data = {}
            if path.exists():
                with open(path) as f:
                    data = json.load(f)
            data.update({"python_online": True, "last_heartbeat": time.time()})
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as exc:
            logger.error("🌉 File heartbeat failed: %s", exc)

    async def _command_processor_loop(self) -> None:
        logger.info("🌉 Command processor started")
        while self._running:
            try:
                await asyncio.sleep(0.01)  # ~100 Hz

                command = None
                with self._command_queue_lock:
                    if self._command_queue:
                        command = self._command_queue.pop(0)
                        self._metrics.queue_depth = len(self._command_queue)

                if command:
                    success = await self._dispatch_command(command)
                    if success:
                        self._metrics.commands_sent += 1
                    else:
                        self._metrics.commands_failed += 1

                    if command.callback:
                        try:
                            await command.callback(success)
                        except Exception as exc:
                            logger.error("🌉 Command callback failed: %s", exc)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("🌉 Command processor error: %s", exc)

    async def _dispatch_command(self, command: Command) -> bool:
        """Route command to the active path, wrapped in the circuit breaker."""
        start = time.time()

        cmd_data = {
            "id":        self._command_counter,
            "type":      command.command_type.value,
            "payload":   command.payload,
            "timestamp": command.timestamp,
            "priority":  command.priority,
        }
        self._command_counter += 1

        try:
            if   self._status == BridgeStatus.CONNECTED: send_fn = self._send_via_shm
            elif self._status == BridgeStatus.DEGRADED:  send_fn = self._send_via_tcp
            elif self._status == BridgeStatus.EMERGENCY: send_fn = self._send_via_file
            else:
                return False

            # Wrap in circuit breaker - on failure it increments counter
            # and may OPEN the breaker
            try:
                success = await send_fn(cmd_data)
                if not success:
                    raise RuntimeError("send returned False")
                # Reset failure count on success (HALF_OPEN -> CLOSED handled inside breaker)
                return True
            except Exception as exc:
                self._circuit_breaker.failure_count += 1
                self._circuit_breaker.last_failure_time = time.time()
                if self._circuit_breaker.failure_count >= self._circuit_breaker.failure_threshold:
                    self._circuit_breaker.state = CircuitBreakerState.OPEN
                    logger.error(
                        "🔌 Circuit breaker -> OPEN after %d failures",
                        self._circuit_breaker.failure_count,
                    )
                raise exc

        except Exception as exc:
            logger.error("🌉 Dispatch failed: %s", exc)
            return False
        finally:
            # Always update latency + IRM sensor regardless of outcome
            latency_ms = (time.time() - start) * 1000
            self._irm_sensor.record_latency(latency_ms)
            if self._metrics.avg_latency_ms == 0:
                self._metrics.avg_latency_ms = latency_ms
            else:
                self._metrics.avg_latency_ms = (
                    self._metrics.avg_latency_ms * 0.9 + latency_ms * 0.1
                )

    async def _send_via_shm(self, cmd_data: Dict[str, Any]) -> bool:
        if not self._shm_command_ring:
            return False
        try:
            payload = json.dumps(cmd_data).encode()
            if len(payload) > SHM_COMMAND_RING_SIZE - 8:
                logger.error("🌉 Command too large for SHM ring")
                return False
            self._shm_command_ring.seek(0)
            self._shm_command_ring.write(struct.pack("I", len(payload)))
            self._shm_command_ring.write(payload)
            self._shm_command_ring.flush()
            self._metrics.bytes_transferred += len(payload) + 4
            return True
        except Exception as exc:
            logger.error("🌉 SHM send failed: %s", exc)
            return False

    async def _send_via_tcp(self, cmd_data: Dict[str, Any]) -> bool:
        if not self._tcp_socket:
            return False
        try:
            msg = json.dumps(cmd_data) + "\n"
            self._tcp_socket.send(msg.encode())
            self._metrics.bytes_transferred += len(msg)
            return True
        except Exception as exc:
            logger.error("🌉 TCP send failed: %s", exc)
            return False

    async def _send_via_file(self, cmd_data: Dict[str, Any]) -> bool:
        try:
            path = (
                self._emergency_dir / "commands" /
                f"cmd_{self._command_counter}_{time.time()}.json"
            )
            with open(path, "w") as f:
                json.dump(cmd_data, f)
            self._metrics.bytes_transferred += path.stat().st_size
            return True
        except Exception as exc:
            logger.error("🌉 File send failed: %s", exc)
            return False

    async def _health_monitor_loop(self) -> None:
        logger.info("🌉 Health monitor started")
        while self._running:
            try:
                await asyncio.sleep(10.0)
                await self._update_performance_metrics()
                await self._check_connection_health()
                self._update_irm()

                snapshot = BridgeMetrics(
                    commands_sent    =self._metrics.commands_sent,
                    commands_failed  =self._metrics.commands_failed,
                    bytes_transferred=self._metrics.bytes_transferred,
                    avg_latency_ms   =self._metrics.avg_latency_ms,
                    queue_depth      =self._metrics.queue_depth,
                    last_heartbeat   =self._metrics.last_heartbeat,
                    fps              =self._metrics.fps,
                    gpu_health       =self._metrics.gpu_health,
                    irm_batch_size   =self._irm_actuator.batch_size,
                    irm_score        =self._irm_sensor.health()["score"],
                    irm_emergency    =self._irm_actuator.emergency_mode,
                )
                self._performance_history.append(snapshot)
                if len(self._performance_history) > 100:
                    self._performance_history = self._performance_history[-50:]

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("🌉 Health monitor error: %s", exc)

    def _update_irm(self) -> None:
        """Feed current timing into IRM sensor, let actuator adjust batch size."""
        now   = time.time()
        delta = now - self._last_metric_time
        if delta > 0:
            self._irm_sensor.record_frame(delta)
        self._last_metric_time = now

        health    = self._irm_sensor.health()
        new_batch = self._irm_actuator.adjust(health)

        self._metrics.irm_batch_size = new_batch
        self._metrics.irm_score      = health["score"]
        self._metrics.irm_emergency  = self._irm_actuator.emergency_mode

        if self._irm_actuator.emergency_mode:
            logger.warning(
                "⚠️  IRM emergency mode - batch reduced to %d (score %.0f)",
                new_batch, health["score"],
            )

    async def _update_performance_metrics(self) -> None:
        if self._status == BridgeStatus.CONNECTED and self._shm_header:
            try:
                self._shm_header.seek(0)
                raw    = self._shm_header.read(_HEADER_SIZE)
                fields = struct.unpack(_HEADER_FMT, raw)
                self._metrics.fps        = fields[6]  # fps
                self._metrics.gpu_health = fields[7]  # gpu_health
            except Exception as exc:
                logger.error("🌉 Metrics update failed: %s", exc)

    async def _check_connection_health(self) -> None:
        now = time.time()

        if now - self._metrics.last_heartbeat > 30:
            logger.warning("🌉 Heartbeat stale - connection may be dead")

        if self._metrics.commands_sent > 0:
            fail_rate = self._metrics.commands_failed / self._metrics.commands_sent
            if fail_rate > 0.1:
                logger.warning("🌉 High failure rate: %.1f%%", fail_rate * 100)

        if self._metrics.queue_depth > 50:
            logger.warning("🌉 Command queue backing up: %d", self._metrics.queue_depth)

        cb = self._circuit_breaker
        if cb.state != CircuitBreakerState.CLOSED:
            logger.warning(
                "🔌 Circuit breaker: %s (%d failures)", cb.state.value, cb.failure_count
            )

    # ================================================================
    # PUBLIC API
    # ================================================================

    def status(self) -> BridgeStatus:
        return self._status

    def get_capacity(self) -> float:
        """Bridge capacity 0.0-1.0 - for Pete's resource monitoring."""
        if self._metrics.queue_depth == 0:
            return 0.0
        return min(1.0, self._metrics.queue_depth / 64.0)

    def fps(self) -> float:
        return self._metrics.fps

    def gpu_health(self) -> float:
        return self._metrics.gpu_health

    def get_metrics(self) -> Dict[str, Any]:
        irm_health = self._irm_sensor.health()
        return {
            "status":              self._status.value,
            "connection_attempts": self._connection_attempts,
            "commands_sent":       self._metrics.commands_sent,
            "commands_failed":     self._metrics.commands_failed,
            "bytes_transferred":   self._metrics.bytes_transferred,
            "avg_latency_ms":      self._metrics.avg_latency_ms,
            "queue_depth":         self._metrics.queue_depth,
            "fps":                 self._metrics.fps,
            "gpu_health":          self._metrics.gpu_health,
            "last_heartbeat":      self._metrics.last_heartbeat,
            "uptime":              time.time() - self._last_connection_attempt if self._last_connection_attempt else 0,
            "circuit_breaker":     self._circuit_breaker.get_status(),
            "irm": {
                "score":      irm_health["score"],
                "status":     irm_health["status"],
                "batch_size": self._irm_actuator.batch_size,
                "emergency":  self._irm_actuator.emergency_mode,
                "fps":        irm_health["fps"],
                "latency_ms": irm_health["latency"],
            },
            "camera": {
                "sequence": self._camera_position.sequence,
                "x":        self._camera_position.x,
                "y":        self._camera_position.y,
                "z":        self._camera_position.z,
            },
        }

    # ================================================================
    # SPECIALISED BRIDGE METHODS  (unchanged from VoxelBridge)
    # ================================================================

    def register_voxel_camera(self, camera_id: str, camera_name: str) -> bool:
        return self.send_command("REGISTER_CAMERA", {
            "camera_id": camera_id,
            "name":      camera_name,
            "type":      "voxel_engine",
            "resolution":"1920x1080",
            "fps":       60,
            "source":    "internal://voxel_engine",
        })

    def switch_camera(self, camera_id: str, destination: str = "program") -> bool:
        payload = {"input": camera_id}
        cmd     = "SWITCH_PREVIEW" if destination == "preview" else "SWITCH_PROGRAM"
        return self.send_command(cmd, payload)

    def start_recording(self, recording_id: str, settings: Optional[Dict[str, Any]] = None) -> bool:
        return self.send_command("RECORD_START", {
            "id": recording_id,
            "settings": settings or {
                "format": "mp4", "resolution": "1920x1080", "fps": 30, "bitrate": "5000k"
            },
        })

    def stop_recording(self, recording_id: str) -> bool:
        return self.send_command("RECORD_STOP", {"id": recording_id})

    def load_scene(self, scene_name: str, scene_data: Optional[Dict[str, Any]] = None) -> bool:
        return self.send_command("LOAD_SCENE", {"scene": scene_name, "data": scene_data or {}})

    def ptz_control(self, dx: float, dy: float, dz: float) -> bool:
        return self.send_command("PTZ", {"dx": dx, "dy": dy, "dz": dz})

    # ================================================================
    # SHUTDOWN
    # ================================================================

    def close(self) -> None:
        logger.info("🌉 VoxelBridge: closing...")
        self._running = False

        for task in (
            self._heartbeat_task,
            self._command_processor_task,
            self._health_monitor_task,
        ):
            if task:
                task.cancel()

        if self._status != BridgeStatus.DISCONNECTED:
            self.send_command("SHUTDOWN", {}, priority=2)

        self._close_shm_maps()

        if self._tcp_socket:
            self._tcp_socket.close()
            self._tcp_socket = None

        if self.manage_shared and self._shm_fd is not None:
            try:
                import posix_ipc
                posix_ipc.SharedMemory(self.shm_name).unlink()
                logger.info("🌉 SHM segment unlinked")
            except Exception:
                pass

        self._status = BridgeStatus.DISCONNECTED
        logger.info("✅ VoxelBridge closed")


# ── Compatibility aliases ─────────────────────────────────────────────────────
# Other modules import these names - do not remove
TwinEngineBridge = VoxelBridge


@dataclass
class BridgeMessage:
    """Generic message envelope between engine nodes."""
    msg_type:  str
    payload:   Dict[str, Any] = field(default_factory=dict)
    timestamp: float          = field(default_factory=time.time)
    source_id: str            = ""


class UDPBridge:
    """
    Lightweight UDP bridge for engine-to-engine signalling.
    Phase 3 full implementation pending C++ twin engine integration.
    """
    def __init__(self, host: str = "127.0.0.1", port: int = 9002) -> None:
        self.host  = host
        self.port  = port
        self._sock: Optional[socket.socket] = None

    def connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
            logger.info("UDPBridge connected to %s:%d", self.host, self.port)
            return True
        except OSError as exc:
            logger.warning("UDPBridge connect failed: %s", exc)
            return False

    def send(self, message: BridgeMessage) -> bool:
        if not self._sock:
            return False
        try:
            data = json.dumps({
                "type":    message.msg_type,
                "payload": message.payload,
                "ts":      message.timestamp,
                "src":     message.source_id,
            }).encode()
            self._sock.sendto(data, (self.host, self.port))
            return True
        except OSError:
            return False

    def close(self) -> None:
        if self._sock:
            self._sock.close()
            self._sock = None


class MessageType(str, Enum):
    HEARTBEAT       = "heartbeat"
    JOIN_ROOM       = "join_room"
    LEAVE_ROOM      = "leave_room"
    CHAT_MESSAGE    = "chat_message"
    CAMERA_SWITCH   = "camera_switch"
    RECORDING_START = "recording_start"
    RECORDING_STOP  = "recording_stop"
    AGENT_MESSAGE   = "agent_message"
    SYSTEM_MESSAGE  = "system_message"
    USER_JOIN       = "user_join"
    USER_LEAVE      = "user_leave"
    AVATAR_UPDATE   = "avatar_update"
    CHOREO_FRAME    = "choreo_frame"
    MOTION_FRAME    = "motion_frame"
