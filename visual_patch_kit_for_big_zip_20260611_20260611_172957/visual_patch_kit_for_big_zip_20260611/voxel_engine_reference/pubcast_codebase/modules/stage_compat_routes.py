"""
modules/stage_compat_routes.py — compatibility surface for stage_panoramic.html.

Purpose:
- Keep the panoramic/control-room frontend from hard-crashing on missing routes.
- Expose small, stable REST/WS endpoints that delegate to existing managers where
  available and return explicit {status: "unavailable"} payloads otherwise.
- Do not own camera/switcher/media/audio logic. This is a bridge until the full
  control-surface reconciliation lands one subsystem at a time.

It supports either direct injection or late lookup from app.state / the loaded
main module. That makes it useful even before main.py receives a surgical mount
patch.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect


async def _json_dict(request: Request) -> Dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def _unavailable(name: str) -> Dict[str, Any]:
    return {"status": "unavailable", "available": False, "subsystem": name}


def _runtime_from_main(name: str) -> Optional[Any]:
    main_mod = sys.modules.get("main") or sys.modules.get("__main__")
    if main_mod is None:
        return None
    return getattr(main_mod, name, None)


def _resolve_runtime_from_app(app: Any, injected: Optional[Any], name: str) -> Optional[Any]:
    if injected is not None:
        return injected
    state_value = getattr(app.state, name, None)
    if state_value is not None:
        return state_value
    return _runtime_from_main(name)


def _resolve_runtime(request: Request, injected: Optional[Any], name: str) -> Optional[Any]:
    return _resolve_runtime_from_app(request.app, injected, name)


def create_stage_compat_router(
    *,
    hub: Optional[Any] = None,
    cameras: Optional[Any] = None,
    recording: Optional[Any] = None,
) -> APIRouter:
    router = APIRouter(tags=["Stage Compatibility"])

    @router.get("/api/state/production")
    async def get_production_state(request: Request):
        current_hub = _resolve_runtime(request, hub, "hub")
        if current_hub is not None and hasattr(current_hub, "get_production_state"):
            return {"status": "ok", "available": True, "state": current_hub.get_production_state()}
        return _unavailable("production_state")

    @router.post("/api/state/production")
    async def update_production_state(request: Request):
        current_hub = _resolve_runtime(request, hub, "hub")
        if current_hub is None or not hasattr(current_hub, "update_production_state"):
            return _unavailable("production_state")
        patch = await _json_dict(request)
        updated = current_hub.update_production_state(patch)
        if hasattr(current_hub, "broadcast_system_event"):
            await current_hub.broadcast_system_event({"type": "production_state", "payload": updated})
        return {"status": "ok", "available": True, "state": updated}

    @router.get("/api/state/user")
    async def get_user_state(request: Request):
        # This route is deliberately privacy-thin. It gives the frontend a stable
        # user/session shape without inventing identity data or exposing private state.
        user_id = request.headers.get("X-Client-Id") or "anon"
        display_name = request.headers.get("X-Display-Name") or user_id
        return {
            "status": "ok",
            "available": True,
            "user": {
                "user_id": user_id,
                "display_name": display_name,
                "roles": [],
                "authenticated": user_id != "anon",
            },
        }

    @router.get("/api/switcher")
    async def get_switcher_state(request: Request):
        current_cameras = _resolve_runtime(request, cameras, "cameras")
        if current_cameras is None:
            return _unavailable("switcher")
        program = current_cameras.get_program_source() if hasattr(current_cameras, "get_program_source") else None
        preview = current_cameras.get_preview_source() if hasattr(current_cameras, "get_preview_source") else None
        return {
            "status": "ok",
            "available": True,
            "switcher": {
                "program": getattr(program, "source_id", None),
                "preview": getattr(preview, "source_id", None),
                "transition": "cut",
                "updated_at": time.time(),
            },
        }

    @router.post("/api/switcher")
    async def set_switcher_state(request: Request):
        current_cameras = _resolve_runtime(request, cameras, "cameras")
        current_hub = _resolve_runtime(request, hub, "hub")
        if current_cameras is None:
            return _unavailable("switcher")
        body = await _json_dict(request)
        changed: Dict[str, Any] = {}
        program = str(body.get("program") or body.get("program_source") or "").strip()
        preview = str(body.get("preview") or body.get("preview_source") or "").strip()

        if program and hasattr(current_cameras, "set_program_source"):
            changed["program"] = program
            current_cameras.set_program_source(program)
        if preview and hasattr(current_cameras, "set_preview_source"):
            changed["preview"] = preview
            current_cameras.set_preview_source(preview)

        if current_hub is not None and hasattr(current_hub, "broadcast_system_event"):
            await current_hub.broadcast_system_event({"type": "switcher_state", "payload": changed})

        return {"status": "ok", "available": True, "changed": changed}

    @router.websocket("/ws/control")
    async def control_websocket(ws: WebSocket):
        room = ws.query_params.get("room") or "control"
        current_hub = _resolve_runtime_from_app(ws.app, hub, "hub")
        if current_hub is None or not hasattr(current_hub, "connect"):
            await ws.accept()
            await ws.send_text(json.dumps(_unavailable("control_websocket")))
            await ws.close()
            return

        await current_hub.connect(ws, room)
        try:
            await ws.send_text(json.dumps({"type": "control_ready", "payload": {"room": room}}))
            while True:
                raw = await ws.receive_text()
                if hasattr(current_hub, "handle_message"):
                    await current_hub.handle_message(ws, room, raw)
        except WebSocketDisconnect:
            pass
        finally:
            if hasattr(current_hub, "disconnect"):
                await current_hub.disconnect(ws, room)

    return router


__all__ = ["create_stage_compat_router"]
