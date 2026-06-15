# PubCast AI — unity_bridge.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
modules/unity_bridge.py — PubCast ↔ Unity WebSocket Bridge

Connects a running Unity instance to PubCast AI's event hub.

Architecture:
  Unity (C#) ←WebSocket→ unity_bridge.py ←→ hub.py
                              ↕
                         WorldBrainStore  (mirrors WorldBrain.cs state)
                              ↕
                         EventRouter      (maps EventBus.Trigger() ↔ hub events)

Protocol — all frames are JSON text:

  Unity → Python:
    {"type": "event",      "name": "DogBarked"}
    {"type": "state_set",  "key": "DogBarked",  "dtype": "bool",   "value": true}
    {"type": "state_set",  "key": "TrackIndex",  "dtype": "int",    "value": 3}
    {"type": "state_set",  "key": "Volume",      "dtype": "float",  "value": 0.75}
    {"type": "state_set",  "key": "RoomName",    "dtype": "string", "value": "Bar"}
    {"type": "ping"}
    {"type": "hello",      "client_id": "unity-1", "scene": "PubCastWorld"}

  Python → Unity:
    {"type": "event",      "name": "ShowStarted"}
    {"type": "event",      "name": "ShowEnded"}
    {"type": "state_set",  "key": "IsOnAir",    "dtype": "bool",   "value": true}
    {"type": "state_sync", "state": { ... full WorldBrain snapshot ... }}
    {"type": "pong"}
    {"type": "ack",        "ok": true}
    {"type": "error",      "message": "..."}

PubCast → Unity event mappings (auto-triggered):
  production_state change    → "ProductionStateChanged"
  recording_start            → "ShowStarted"
  recording_stop             → "ShowEnded"
  camera_cut                 → "CameraChanged"  + state_set CameraId
  lighting_update            → "LightingChanged" + state_set LightingPreset
  avatar_update              → "AvatarChanged"   + state_set AvatarPreset

Unity → PubCast hub event mappings:
  MusicStarted               → hub broadcast {type: "unity_event", name: "MusicStarted"}
  MusicStopped               → hub broadcast {type: "unity_event", name: "MusicStopped"}
  DogBarked                  → hub broadcast {type: "unity_event", name: "DogBarked"}
  PlayerEnteredRoom          → hub broadcast {type: "room_enter",  room: state["CurrentRoom"]}
  Any EventBus.Trigger()     → hub broadcast {type: "unity_event", name: <event_name>}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger("pubcast.unity_bridge")


# ── WorldBrain mirror ─────────────────────────────────────────────────────────

class WorldBrainStore:
    """
    Server-side mirror of Unity's WorldBrain.cs key-value state store.
    Thread-safe via asyncio.Lock. Keeps dtype metadata so round-trips
    deserialise to the correct C# type on the Unity side.
    """

    _VALID_DTYPES = frozenset({"bool", "int", "float", "string"})

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}  # key → {value, dtype}
        self._lock = asyncio.Lock()

    async def set(self, key: str, value: Any, dtype: str = "string") -> None:
        if dtype not in self._VALID_DTYPES:
            raise ValueError(f"Invalid dtype {dtype!r}")
        # Coerce value to match dtype
        coerced: Any
        if dtype == "bool":
            coerced = bool(value)
        elif dtype == "int":
            coerced = int(value)
        elif dtype == "float":
            coerced = float(value)
        else:
            coerced = str(value)
        async with self._lock:
            self._state[key] = {"value": coerced, "dtype": dtype}

    async def get(self, key: str) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return self._state.get(key)

    async def snapshot(self) -> Dict[str, Dict[str, Any]]:
        async with self._lock:
            return dict(self._state)

    async def apply_production_event(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Convert a PubCast hub event into zero or more state_set + event frames
        to push to Unity.
        """
        frames: List[Dict[str, Any]] = []
        etype = event.get("type", "")

        if etype == "recording_start":
            await self.set("IsRecording", True, "bool")
            await self.set("IsOnAir",    True, "bool")
            frames.append({"type": "event",     "name": "ShowStarted"})
            frames.append({"type": "state_set", "key": "IsOnAir",    "dtype": "bool", "value": True})
            frames.append({"type": "state_set", "key": "IsRecording","dtype": "bool", "value": True})

        elif etype == "recording_stop":
            await self.set("IsRecording", False, "bool")
            await self.set("IsOnAir",    False, "bool")
            frames.append({"type": "event",     "name": "ShowEnded"})
            frames.append({"type": "state_set", "key": "IsOnAir",    "dtype": "bool", "value": False})
            frames.append({"type": "state_set", "key": "IsRecording","dtype": "bool", "value": False})

        elif etype == "camera_cut":
            cam_id = str(event.get("payload", {}).get("new_program", ""))
            await self.set("ActiveCameraId", cam_id, "string")
            frames.append({"type": "event",     "name": "CameraChanged"})
            frames.append({"type": "state_set", "key": "ActiveCameraId", "dtype": "string", "value": cam_id})

        elif etype == "lighting_update":
            preset = str(event.get("preset_id", event.get("preset", {}).get("preset_id", "")))
            await self.set("LightingPreset", preset, "string")
            frames.append({"type": "event",     "name": "LightingChanged"})
            frames.append({"type": "state_set", "key": "LightingPreset", "dtype": "string", "value": preset})

        elif etype == "avatar_update":
            payload = event.get("payload", {})
            preset = str(payload.get("preset_id", ""))
            uid    = str(payload.get("user_id",   ""))
            await self.set("AvatarPreset", preset, "string")
            frames.append({"type": "event",     "name": "AvatarChanged"})
            frames.append({"type": "state_set", "key": "AvatarPreset", "dtype": "string", "value": preset})
            frames.append({"type": "state_set", "key": "AvatarUserId", "dtype": "string", "value": uid})

        elif etype == "production_state":
            frames.append({"type": "event", "name": "ProductionStateChanged"})

        return frames


# ── Connection registry ───────────────────────────────────────────────────────

class UnityConnectionRegistry:
    """
    Tracks all live Unity WebSocket connections.
    On PubCast hub events, broadcasts the translated frames to all Unity clients.
    """

    def __init__(self, world_brain: WorldBrainStore) -> None:
        self._world_brain = world_brain
        self._clients: Dict[str, WebSocket] = {}   # client_id → websocket
        self._lock = asyncio.Lock()
        self._total_connected = 0
        self._total_events_routed = 0

    async def register(self, ws: WebSocket, client_id: str) -> None:
        async with self._lock:
            self._clients[client_id] = ws
            self._total_connected += 1
        logger.info("Unity client connected: %s  (total: %d)", client_id, len(self._clients))

    async def unregister(self, client_id: str) -> None:
        async with self._lock:
            self._clients.pop(client_id, None)
        logger.info("Unity client disconnected: %s  (remaining: %d)", client_id, len(self._clients))

    async def broadcast(self, frames: List[Dict[str, Any]]) -> None:
        if not self._clients or not frames:
            return
        dead: List[str] = []
        async with self._lock:
            clients = dict(self._clients)
        for client_id, ws in clients.items():
            try:
                if ws.application_state != WebSocketState.CONNECTED:
                    dead.append(client_id)
                    continue
                for frame in frames:
                    await ws.send_json(frame)
                self._total_events_routed += len(frames)
            except Exception as exc:
                logger.debug("Unity broadcast failed to %s: %s", client_id, exc)
                dead.append(client_id)
        if dead:
            async with self._lock:
                for cid in dead:
                    self._clients.pop(cid, None)

    async def send_to(self, client_id: str, frame: Dict[str, Any]) -> bool:
        async with self._lock:
            ws = self._clients.get(client_id)
        if ws is None:
            return False
        try:
            await ws.send_json(frame)
            return True
        except Exception:
            return False

    def stats(self) -> Dict[str, Any]:
        return {
            "connected":       len(self._clients),
            "client_ids":      list(self._clients.keys()),
            "total_connected": self._total_connected,
            "events_routed":   self._total_events_routed,
        }


# ── Main bridge class ─────────────────────────────────────────────────────────

class UnityBridge:
    """
    Top-level Unity bridge.  One instance lives in main.py as a singleton.

    Usage:
        bridge = UnityBridge(hub)
        # In main.py after hub is created — hub broadcasts will auto-forward to Unity.

        # In websocket endpoint:
        await bridge.handle_connection(websocket)

        # To push a PubCast event to all Unity clients:
        await bridge.on_pubcast_event({"type": "recording_start", ...})
    """

    def __init__(self, hub: Any) -> None:
        self._hub = hub
        self._world_brain = WorldBrainStore()
        self._registry    = UnityConnectionRegistry(self._world_brain)
        self._started_at  = time.time()
        logger.info("UnityBridge initialised")

    # ── Called by main.py when hub events fire ────────────────────────────────

    async def on_pubcast_event(self, event: Dict[str, Any]) -> None:
        """
        Receive a PubCast hub event, translate it to Unity frames,
        broadcast to all connected Unity clients.
        """
        frames = await self._world_brain.apply_production_event(event)
        if frames:
            await self._registry.broadcast(frames)

    # ── WebSocket connection handler ──────────────────────────────────────────

    async def handle_connection(self, websocket: WebSocket) -> None:
        """Handle one Unity WebSocket connection for its full lifetime."""
        await websocket.accept()

        # Assign a client ID (Unity will send one in hello, or we generate)
        client_id = f"unity-{int(time.time() * 1000) % 100000}"

        try:
            # Send initial state sync so Unity WorldBrain is consistent on connect
            snapshot = await self._world_brain.snapshot()
            await websocket.send_json({
                "type":    "state_sync",
                "state":   snapshot,
                "server":  "pubcast_ai_v2",
            })
            logger.debug("Sent state_sync to %s (%d keys)", client_id, len(snapshot))

            await self._registry.register(websocket, client_id)

            while True:
                try:
                    raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                except asyncio.TimeoutError:
                    # Keepalive
                    await websocket.send_json({"type": "ping"})
                    continue

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError as exc:
                    await websocket.send_json({"type": "error", "message": f"Bad JSON: {exc}"})
                    continue

                await self._handle_message(websocket, client_id, msg)

        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.warning("Unity WS error for %s: %s", client_id, exc)
            if websocket.application_state == WebSocketState.CONNECTED:
                try:
                    await websocket.close(code=1011)
                except Exception:
                    pass
        finally:
            await self._registry.unregister(client_id)

    async def _handle_message(
        self,
        ws: WebSocket,
        client_id: str,
        msg: Dict[str, Any],
    ) -> None:
        mtype = msg.get("type", "")

        if mtype == "ping":
            await ws.send_json({"type": "pong"})
            return

        if mtype == "pong":
            return

        if mtype == "hello":
            # Unity client introducing itself — adopt its client_id if provided
            new_id = str(msg.get("client_id", client_id))
            if new_id != client_id:
                await self._registry.unregister(client_id)
                await self._registry.register(ws, new_id)
            scene = msg.get("scene", "unknown")
            await self._world_brain.set("UnityScene",    scene,  "string")
            await self._world_brain.set("UnityConnected", True,  "bool")
            await ws.send_json({"type": "ack", "ok": True, "client_id": new_id})
            logger.info("Unity hello from %s (scene=%s)", new_id, scene)
            return

        if mtype == "state_set":
            key   = str(msg.get("key",   ""))
            dtype = str(msg.get("dtype", "string"))
            value =     msg.get("value")
            if not key:
                await ws.send_json({"type": "error", "message": "state_set requires key"})
                return
            try:
                await self._world_brain.set(key, value, dtype)
                await ws.send_json({"type": "ack", "ok": True})
            except ValueError as exc:
                await ws.send_json({"type": "error", "message": str(exc)})
            return

        if mtype == "state_get":
            key = str(msg.get("key", ""))
            entry = await self._world_brain.get(key)
            if entry:
                await ws.send_json({"type": "state_set", "key": key, **entry})
            else:
                await ws.send_json({"type": "error", "message": f"Key not found: {key}"})
            return

        if mtype == "state_sync_request":
            snapshot = await self._world_brain.snapshot()
            await ws.send_json({"type": "state_sync", "state": snapshot})
            return

        if mtype == "event":
            # Unity EventBus.Trigger() forwarded to PubCast hub
            name = str(msg.get("name", ""))
            if not name:
                await ws.send_json({"type": "error", "message": "event requires name"})
                return
            # Route into hub so all PubCast systems see it
            if self._hub is not None:
                try:
                    await self._hub.broadcast_system_event({
                        "type":      "unity_event",
                        "name":      name,
                        "client_id": client_id,
                        "payload":   msg.get("payload", {}),
                        "timestamp": time.time(),
                    })
                except Exception as exc:
                    logger.warning("Failed to route Unity event to hub: %s", exc)
            await ws.send_json({"type": "ack", "ok": True})
            logger.info("Unity event routed to hub: %s from %s", name, client_id)
            return

        # Unknown message type — ack so Unity doesn't hang
        await ws.send_json({"type": "error", "message": f"Unknown message type: {mtype!r}"})

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        return {
            "uptime_s":   round(time.time() - self._started_at, 1),
            "connections": self._registry.stats(),
        }


__all__ = ["UnityBridge", "WorldBrainStore", "UnityConnectionRegistry"]
