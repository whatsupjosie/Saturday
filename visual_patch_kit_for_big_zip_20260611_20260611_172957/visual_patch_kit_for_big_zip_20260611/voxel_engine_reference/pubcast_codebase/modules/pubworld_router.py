# PubCast AI — pubworld_router.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
modules/pubworld_router.py — FastAPI router for PubWorld map clients.

Mounts WebSocket endpoint at /ws/pubworld that receives real-time
production state updates and distributes them to connected map viewers.

Exports:
    router                          — APIRouter to include in main app
    push_production_state_to_pubworld(state: dict) — broadcast to all connected clients
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pubworld", tags=["pubworld"])

# ---------------------------------------------------------------------------
# In-process connection registry — lightweight, no Redis needed for v1
# ---------------------------------------------------------------------------
_connected: Set[WebSocket] = set()
_lock = asyncio.Lock()


async def _register(ws: WebSocket) -> None:
    async with _lock:
        _connected.add(ws)
    logger.debug("PubWorld client connected. Total: %d", len(_connected))


async def _unregister(ws: WebSocket) -> None:
    async with _lock:
        _connected.discard(ws)
    logger.debug("PubWorld client disconnected. Total: %d", len(_connected))


async def push_production_state_to_pubworld(state: Dict[str, Any]) -> None:
    """
    Broadcast the current production state to all connected PubWorld
    WebSocket clients. Dead connections are pruned automatically.
    """
    if not _connected:
        return

    payload = {"type": "production_state", "payload": state}
    dead: list[WebSocket] = []

    async with _lock:
        clients = list(_connected)

    for ws in clients:
        try:
            if ws.application_state != WebSocketState.CONNECTED:
                dead.append(ws)
                continue
            await ws.send_json(payload)
        except Exception as exc:
            logger.debug("PubWorld broadcast failed to client: %s", exc)
            dead.append(ws)

    if dead:
        async with _lock:
            for ws in dead:
                _connected.discard(ws)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@router.websocket("/ws")
async def pubworld_ws(websocket: WebSocket) -> None:
    """
    PubWorld map clients connect here to receive live production state.
    The client may also send messages (e.g. ping) but the primary flow
    is server → client pushes via push_production_state_to_pubworld().
    """
    await websocket.accept()
    await _register(websocket)
    try:
        while True:
            # Keep the connection alive; accept any client-sent text/ping
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo ping so client can measure latency
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive so the client knows we're still here
                try:
                    await websocket.send_json({"type": "keepalive"})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.warning("PubWorld WS error: %s", exc)
    finally:
        await _unregister(websocket)


# ---------------------------------------------------------------------------
# REST — current state snapshot for late-joining clients
# ---------------------------------------------------------------------------

@router.get("/state")
async def pubworld_state(request_: Any = None) -> Dict[str, Any]:
    """
    Returns a stub state snapshot. In practice, the main app's hub
    holds the live ProductionState; this endpoint is a convenience
    polling target for clients that can't hold a WebSocket.
    """
    return {
        "connected_clients": len(_connected),
        "note": "Use /pubworld/ws for real-time state.",
    }


__all__ = ["router", "push_production_state_to_pubworld"]
