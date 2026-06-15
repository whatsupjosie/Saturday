"""
modules/pubworld_hotspots.py — PubWorld Hotspot API
════════════════════════════════════════════════════════════════════════════════
Backend handler for the /pubworld/api/hotspot endpoint that world.html POSTs to.

Loads annotated room hotspot JSONs from data/global/ and dispatches
action calls from the frontend USE_HANDLERS (typewriter, phone, camera_remote,
tv, wardrobe, scripts, avatar_foundry, makeup, message_board, sit).

Each action returns a result dict that the frontend renders in showNookModal().

Rear View Foresight LLC — Feic Mo Chroí — 2026
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from . import session_runtime, memory_engine
from .route_security import current_identity

logger = logging.getLogger("pubcast.hotspots")

# ─── Hotspot data loaded from Level Annotator JSON exports ────────────────────

_HOTSPOT_CACHE: Dict[str, Dict[str, Any]] = {}
_DATA_DIR: Optional[Path] = None


def _load_hotspot_file(path: Path) -> List[Dict[str, Any]]:
    """Load a Level Annotator JSON and return the hotspot list."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        hotspots = data.get("hotspots", [])
        meta = data.get("meta", {})
        image_size = meta.get("imageSize", {})
        # Normalise positions to percentage coordinates
        w = image_size.get("width", 1)
        h = image_size.get("height", 1)
        for hs in hotspots:
            pos = hs.get("position", {})
            hs["pct_x"] = round(pos.get("x", 0) / w * 100, 1)
            hs["pct_y"] = round(pos.get("y", 0) / h * 100, 1)
            hs["pct_radius"] = round(hs.get("radius", 40) / w * 100, 1)
        return hotspots
    except Exception as exc:
        logger.warning("Failed to load hotspot file %s: %s", path, exc)
        return []


def load_all_hotspots(data_dir: Path) -> Dict[str, List[Dict[str, Any]]]:
    """Scan data/global/ for *_hotspots.json and load them keyed by room."""
    global _HOTSPOT_CACHE
    hotspot_dir = data_dir / "global"
    result: Dict[str, List[Dict[str, Any]]] = {}
    if not hotspot_dir.exists():
        return result
    for fp in hotspot_dir.glob("*_hotspots.json"):
        room_id = fp.stem.replace("_hotspots", "")
        hotspots = _load_hotspot_file(fp)
        if hotspots:
            result[room_id] = hotspots
            logger.info("Loaded %d hotspots for room '%s'", len(hotspots), room_id)
    _HOTSPOT_CACHE = result
    return result


def get_hotspots(room_id: str) -> List[Dict[str, Any]]:
    """Return hotspot list for a room."""
    return _HOTSPOT_CACHE.get(room_id, [])


# ─── Action Handlers ─────────────────────────────────────────────────────────
# Each handler receives (client_id, room, data) and returns a result dict.
# These are the backend sides of the USE_HANDLERS in world.html.

# In-memory state for lightweight interactive features
_script_drafts: Dict[str, Dict[str, Any]] = {}  # client_id -> draft
_tv_state: Dict[str, str] = {}  # room -> channel
DEFAULT_TV_CHANNELS = ['off', 'stage_feed', 'crew_chatter', 'uploaded_dailies', 'previs', 'teaser_trailer', 'pitch_presentation', 'director_inspiration', 'my_media', 'my_music', 'pubcast_ai_channel']


def _handle_typewriter(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create or resume a script draft for this client."""
    draft = _script_drafts.get(client_id)
    if not draft:
        draft = {
            "draft_id": str(uuid.uuid4())[:8],
            "created": time.time(),
            "room": room,
            "lines": [],
        }
        _script_drafts[client_id] = draft
    # If text was provided, append it
    text = data.get("text", "")
    if text:
        draft["lines"].append({"t": time.time(), "text": text})
    return {"status": "ok", "result": {"draft_id": draft["draft_id"], "line_count": len(draft["lines"])}}


def _handle_scripts(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """List script drafts for this client."""
    draft = _script_drafts.get(client_id)
    if draft:
        return {"status": "ok", "result": {"scripts": [f"Draft {draft['draft_id']} ({len(draft['lines'])} lines)"]}}
    return {"status": "ok", "result": {"scripts": []}}


def _handle_phone(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Return session-scoped call menu and a generated session token."""
    session_id = data.get('session_id', 'default')
    project_id = data.get('project_id', 'default')
    display_name = data.get('display_name', client_id)
    participant = session_runtime.register_participant(
        _DATA_DIR or Path('data'),
        session_id=session_id,
        user_id=client_id,
        display_name=display_name,
        project_id=project_id,
    )
    session_token = str(uuid.uuid4())[:12]
    contacts = session_runtime.call_menu(_DATA_DIR or Path('data'), session_id)
    return {
        "status": "ok",
        "result": {
            "session_token": session_token,
            "join_url": f"/session/{session_token}",
            "room": room,
            "contacts": contacts,
            "call_handle": participant.get('call_handle'),
            "dressing_room_id": participant.get('dressing_room_id'),
        },
    }


def _handle_camera_remote(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """List available cameras."""
    # Pull from actual CameraManager if wired, otherwise return defaults
    return {
        "status": "ok",
        "result": {
            "cameras": ["cam_wide", "cam_closeup_a", "cam_closeup_b", "cam_overhead", "cam_audience", "cam_director"],
        },
    }


def _handle_select_camera(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Select a camera."""
    camera = data.get("camera", "cam_wide")
    return {"status": "ok", "result": {"selected": camera}}


def _handle_tv(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Toggle TV / flip channel through the dressing-room media console list."""
    current = _tv_state.get(room, 'off')
    channels = list(data.get('channels') or DEFAULT_TV_CHANNELS)
    idx = channels.index(current) if current in channels else 0
    next_channel = channels[(idx + 1) % len(channels)]
    _tv_state[room] = next_channel
    return {
        "status": "ok",
        "result": {
            "channel": next_channel,
            "room": room,
            "available_channels": channels,
        },
    }


def _handle_wardrobe(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Open wardrobe / clothing menu."""
    return {
        "status": "ok",
        "result": {
            "wardrobe_items": ["default_outfit", "formal_suit", "casual_wear", "stage_costume"],
            "current": data.get("current", "default_outfit"),
        },
    }


def _handle_avatar_foundry(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Open the avatar creation/customisation platform."""
    return {
        "status": "ok",
        "result": {
            "foundry_url": "/static/avatar_studio_demo.html",
            "available_colors": ["teal", "blue", "purple", "gold", "green", "red", "white"],
        },
    }


def _handle_makeup(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Hair and makeup station."""
    return {
        "status": "ok",
        "result": {
            "styles": ["natural", "stage_ready", "dramatic", "minimal"],
            "current": data.get("current", "natural"),
        },
    }


def _handle_message_board(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Check message board — returns recent system messages."""
    return {
        "status": "ok",
        "result": {
            "messages": [
                {"from": "Stage Manager", "text": "Rehearsal at 3pm", "t": time.time() - 3600},
                {"from": "System", "text": "Welcome to PubCast AI v5.2", "t": time.time()},
            ],
        },
    }


def _handle_sit(client_id: str, room: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Sit on couch / chair — triggers animation state."""
    return {
        "status": "ok",
        "result": {
            "animation": "sit_idle",
            "location": data.get("location", "couch"),
        },
    }


# ─── Action dispatch table ───────────────────────────────────────────────────

ACTION_HANDLERS = {
    "typewriter": _handle_typewriter,
    "scripts": _handle_scripts,
    "phone": _handle_phone,
    "camera_remote": _handle_camera_remote,
    "select_camera": _handle_select_camera,
    "tv": _handle_tv,
    "wardrobe": _handle_wardrobe,
    "avatar_foundry": _handle_avatar_foundry,
    "makeup": _handle_makeup,
    "message_board": _handle_message_board,
    "sit": _handle_sit,
    "couch": _handle_sit,
}


# ─── Router ──────────────────────────────────────────────────────────────────

def create_hotspot_router(data_dir: Path) -> APIRouter:
    """Create the /pubworld/api/* router."""
    router = APIRouter(prefix="/pubworld/api", tags=["pubworld"])

    global _DATA_DIR
    _DATA_DIR = data_dir
    # Load hotspot data at router creation time
    load_all_hotspots(data_dir)

    @router.post("/hotspot")
    async def hotspot_action(request: Request, identity: Dict[str, Any] = Depends(current_identity)):
        """
        POST /pubworld/api/hotspot
        Body: { client_id, room, action, data }
        Returns: { status, result }
        """
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

        client_id = body.get("client_id", "anon")
        room = body.get("room", "")
        action = body.get("action", "")
        action_data = body.get("data", {})

        handler = ACTION_HANDLERS.get(action)
        if not handler:
            return JSONResponse({
                "status": "unknown_action",
                "action": action,
                "available": list(ACTION_HANDLERS.keys()),
            })

        try:
            result = handler(client_id, room, action_data)
            try:
                memory_engine.record_event(
                    _DATA_DIR or data_dir,
                    session_id=action_data.get('session_id') or 'default',
                    project_id=action_data.get('project_id') or 'default',
                    user_id=client_id,
                    room_id=room,
                    feature_id=action,
                    event_type=action,
                    summary=f"hotspot:{action}",
                    mood_trace=action_data.get('mood_trace') or '',
                    speaking_style=action_data.get('speaking_style') or '',
                    payload={'result_status': result.get('status'), 'room': room},
                )
            except Exception:
                pass
            return JSONResponse(result)
        except Exception as exc:
            logger.error("Hotspot action '%s' failed: %s", action, exc, exc_info=True)
            return JSONResponse({"status": "error", "message": str(exc)}, status_code=500)

    @router.get("/hotspots/{room_id}")
    async def get_room_hotspots(room_id: str):
        """
        GET /pubworld/api/hotspots/{room_id}
        Returns the Level Annotator hotspot data for a room.
        """
        hotspots = get_hotspots(room_id)
        if not hotspots:
            # Try with underscores/hyphens normalised
            normalised = room_id.replace("-", "_").replace(" ", "_").lower()
            hotspots = get_hotspots(normalised)
        return {
            "room_id": room_id,
            "hotspots": hotspots,
            "count": len(hotspots),
        }

    @router.get("/hotspots")
    async def list_all_hotspots():
        """GET /pubworld/api/hotspots — list all rooms with hotspot data."""
        return {
            "rooms": {
                room_id: len(hs_list)
                for room_id, hs_list in _HOTSPOT_CACHE.items()
            },
        }

    @router.get("/rooms")
    async def pubworld_rooms():
        """GET /pubworld/api/rooms — room graph for world navigation."""
        from .pubcast_room_layout import ROOMS as LAYOUT_ROOMS
        return {
            "rooms": {
                rid: {
                    "display_name": rdef.display_name,
                    "connects_to": rdef.connects_to,
                    "spawn_point": rdef.spawn_point,
                    "lighting_mood": rdef.lighting_mood,
                }
                for rid, rdef in LAYOUT_ROOMS.items()
            },
        }

    logger.info("PubWorld hotspot router created — %d rooms with hotspot data", len(_HOTSPOT_CACHE))
    return router
