# PubCast AI — bridge_raw.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
modules/bridge.py — Twin Engine Communication Bridge
=====================================================
Provides the inter-engine messaging layer used by DistributedEngineNode.

Two transports are implemented:
  UDPBridge         — low-latency fire-and-forget datagrams (heartbeat, metrics)
  TwinEngineBridge  — combines shared-memory state with UDP messaging and
                      provides the high-level API consumed by the engine.

Public API (consumed by distributed_engine.py):
    TwinEngineBridge
    UDPBridge
    MessageType
    BridgeMessage
"""
from __future__ import annotations

import json
import logging
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Maximum UDP datagram payload we will send (well under MTU for LAN)
_MAX_DGRAM = 8192


# ── Message types ─────────────────────────────────────────────────────────────

class MessageType(str, Enum):
    HEARTBEAT      = "heartbeat"
    METRICS        = "metrics"
    SET_ROLE       = "set_role"
    REDUCE_QUALITY = "reduce_quality"
    PROCESS_WORK   = "process_work"
    WORK_RESULT    = "work_result"
    HEALTH_CHECK   = "health_check"
    HEALTH_REPORT  = "health_report"
    SHUTDOWN       = "shutdown"
    ACK            = "ack"


# ── Message dataclass ─────────────────────────────────────────────────────────

@dataclass
class BridgeMessage:
    type: MessageType
    sender_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)

    def to_bytes(self) -> bytes:
        return json.dumps({
            "type":      self.type.value,
            "sender_id": self.sender_id,
            "payload":   self.payload,
            "msg_id":    self.msg_id,
            "timestamp": self.timestamp,
        }, ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "BridgeMessage":
        obj = json.loads(data.decode("utf-8"))
        return cls(
            type=MessageType(obj["type"]),
            sender_id=obj["sender_id"],
            payload=obj.get("payload", {}),
            msg_id=obj.get("msg_id", ""),
            timestamp=obj.get("timestamp", time.time()),
        )


# ── UDP Transport ─────────────────────────────────────────────────────────────

class UDPBridge:
    """
    Bidirectional UDP bridge between two engine processes on the same host
    (or LAN).  Uses two sockets: one bound for receive, one for send.

    Parameters
    ----------
    local_port
        UDP port this instance listens on.
    remote_host / remote_port
        Destination for outbound messages.
    on_message
        Callback invoked (in the receiver thread) for each valid BridgeMessage.
    """

    def __init__(
        self,
        local_port: int,
        remote_host: str,
        remote_port: int,
        on_message: Optional[Callable[[BridgeMessage], None]] = None,
    ) -> None:
        self.local_port  = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.on_message  = on_message

        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._send_lock = threading.Lock()

        # Stats
        self._sent     = 0
        self._received = 0
        self._errors   = 0

    def start(self) -> None:
        """Bind the socket and start the receive thread."""
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("0.0.0.0", self.local_port))
        self._sock.settimeout(1.0)
        self._running = True
        self._thread = threading.Thread(
            target=self._receive_loop,
            daemon=True,
            name=f"udp-bridge-{self.local_port}",
        )
        self._thread.start()
        logger.info("UDPBridge listening on :%d → %s:%d",
                    self.local_port, self.remote_host, self.remote_port)

    def stop(self) -> None:
        """Signal the receive thread to stop and close the socket."""
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3.0)
        logger.info("UDPBridge :%d stopped", self.local_port)

    def send(self, msg: BridgeMessage) -> bool:
        """
        Send a BridgeMessage to the remote endpoint.
        Returns True on success, False on error.
        """
        if not self._sock:
            logger.warning("UDPBridge.send(): socket not started")
            return False
        data = msg.to_bytes()
        if len(data) > _MAX_DGRAM:
            logger.error("UDPBridge: message too large (%d bytes), dropping", len(data))
            return False
        try:
            with self._send_lock:
                self._sock.sendto(data, (self.remote_host, self.remote_port))
            self._sent += 1
            return True
        except OSError as e:
            logger.warning("UDPBridge send error: %s", e)
            self._errors += 1
            return False

    @property
    def stats(self) -> Dict[str, int]:
        return {"sent": self._sent, "received": self._received, "errors": self._errors}

    # ── Receive loop (background thread) ──────────────────────────────────────

    def _receive_loop(self) -> None:
        while self._running:
            try:
                data, _addr = self._sock.recvfrom(_MAX_DGRAM)  # type: ignore[union-attr]
                msg = BridgeMessage.from_bytes(data)
                self._received += 1
                if self.on_message:
                    try:
                        self.on_message(msg)
                    except Exception as exc:
                        logger.debug("UDPBridge on_message error: %s", exc)
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    logger.debug("UDPBridge recv socket closed")
                break
            except Exception as exc:
                self._errors += 1
                logger.debug("UDPBridge recv parse error: %s", exc)


# ── Twin Engine Bridge ────────────────────────────────────────────────────────

class TwinEngineBridge:
    """
    High-level bridge used by DistributedEngineNode.

    Wraps UDPBridge and adds:
      - Shared in-process state dictionary (for same-host twin engines that
        communicate via a Queue or dict rather than network)
      - Convenience send_* helpers matching the API called by distributed_engine.py
      - Peer liveness tracking (last_seen per sender_id)

    Parameters
    ----------
    engine_id
        This engine's identifier (used as sender_id in outbound messages).
    local_port
        UDP port to listen on.  Set to 0 to disable UDP (in-process mode).
    remote_host / remote_port
        Destination engine's UDP endpoint.
    """

    def __init__(
        self,
        engine_id: str,
        *,
        local_port:  int = 0,
        remote_host: str = "127.0.0.1",
        remote_port: int = 0,
        on_message: Optional[Callable[[BridgeMessage], None]] = None,
    ) -> None:
        self.engine_id   = engine_id
        self.on_message  = on_message

        self._udp: Optional[UDPBridge] = None
        self._use_udp = local_port > 0 and remote_port > 0

        if self._use_udp:
            self._udp = UDPBridge(
                local_port=local_port,
                remote_host=remote_host,
                remote_port=remote_port,
                on_message=self._handle_incoming,
            )

        # Shared in-process state (used when both engines live in same process)
        self._shared: Dict[str, Any] = {}
        self._shared_lock = threading.Lock()

        # Peer liveness: sender_id → last_seen timestamp
        self._peers: Dict[str, float] = {}
        self._peers_lock = threading.Lock()

        # Registered message handlers: MessageType → list[callable]
        self._handlers: Dict[MessageType, List[Callable[[BridgeMessage], None]]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._udp:
            self._udp.start()

    def stop(self) -> None:
        if self._udp:
            self._udp.stop()

    # ── Handler registration ──────────────────────────────────────────────────

    def on(self, msg_type: MessageType, handler: Callable[[BridgeMessage], None]) -> None:
        """Register a callback for a specific MessageType."""
        self._handlers.setdefault(msg_type, []).append(handler)

    # ── Send helpers ──────────────────────────────────────────────────────────

    def send(self, msg_type: MessageType, payload: Dict[str, Any] = None) -> bool:
        msg = BridgeMessage(
            type=msg_type,
            sender_id=self.engine_id,
            payload=payload or {},
        )
        if self._udp:
            return self._udp.send(msg)
        # In-process fallback: deliver directly
        self._handle_incoming(msg)
        return True

    def send_heartbeat(self, metrics: Dict[str, Any]) -> bool:
        return self.send(MessageType.HEARTBEAT, {"metrics": metrics})

    def send_metrics(self, metrics: Dict[str, Any]) -> bool:
        return self.send(MessageType.METRICS, metrics)

    def send_set_role(self, role: str, target_engine_id: Optional[str] = None) -> bool:
        return self.send(MessageType.SET_ROLE, {
            "role": role, "target": target_engine_id or "",
        })

    def send_reduce_quality(self, factor: float = 0.75) -> bool:
        return self.send(MessageType.REDUCE_QUALITY, {"factor": factor})

    def send_process_work(self, work_unit: Dict[str, Any]) -> bool:
        return self.send(MessageType.PROCESS_WORK, {"work_unit": work_unit})

    def send_health_check(self) -> bool:
        return self.send(MessageType.HEALTH_CHECK, {})

    def send_shutdown(self) -> bool:
        return self.send(MessageType.SHUTDOWN, {})

    # ── Shared state helpers ──────────────────────────────────────────────────

    def set_shared(self, key: str, value: Any) -> None:
        with self._shared_lock:
            self._shared[key] = value

    def get_shared(self, key: str, default: Any = None) -> Any:
        with self._shared_lock:
            return self._shared.get(key, default)

    # ── Peer liveness ─────────────────────────────────────────────────────────

    def peer_alive(self, sender_id: str, timeout: float = 5.0) -> bool:
        with self._peers_lock:
            last = self._peers.get(sender_id, 0.0)
            return (time.time() - last) < timeout

    def known_peers(self) -> Dict[str, float]:
        with self._peers_lock:
            return dict(self._peers)

    @property
    def health(self) -> Dict[str, Any]:
        udp_stats = self._udp.stats if self._udp else {}
        return {
            "engine_id": self.engine_id,
            "use_udp":   self._use_udp,
            "udp_stats": udp_stats,
            "peers":     self.known_peers(),
        }

    # ── Internal dispatch ─────────────────────────────────────────────────────

    def _handle_incoming(self, msg: BridgeMessage) -> None:
        # Update liveness
        with self._peers_lock:
            self._peers[msg.sender_id] = time.time()

        # Dispatch to registered handlers
        for handler in self._handlers.get(msg.type, []):
            try:
                handler(msg)
            except Exception as exc:
                logger.debug("TwinEngineBridge handler error for %s: %s", msg.type, exc)

        # Forward to external on_message if set
        if self.on_message:
            try:
                self.on_message(msg)
            except Exception as exc:
                logger.debug("TwinEngineBridge on_message error: %s", exc)
