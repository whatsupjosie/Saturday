# PubCast AI — avatar_studio_bridge.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from .llm_orchestrator import ContextPacket, get_orchestrator
from .route_security import current_identity, require_role

SHOT_PRESETS: Dict[str, Dict[str, Any]] = {
    "wide": {"position": [0.0, 2.2, 7.0], "look_at": [0.0, 1.2, 0.0], "fov": 72.0},
    "medium": {"position": [2.0, 1.8, 4.0], "look_at": [0.0, 1.4, 0.0], "fov": 48.0},
    "close": {"position": [0.8, 1.7, 2.2], "look_at": [0.0, 1.5, 0.0], "fov": 32.0},
    "overhead": {"position": [0.0, 7.0, 1.0], "look_at": [0.0, 0.0, 0.0], "fov": 80.0},
    "stage_left": {"position": [-2.5, 2.0, 4.5], "look_at": [0.5, 1.0, 0.0], "fov": 55.0},
}

class AvatarStudioBridge:
    def __init__(self, ethereal_mgr: Any, cameras: Any, hub: Any) -> None:
        self.ethereal_mgr = ethereal_mgr
        self.cameras = cameras
        self.hub = hub
        self.avatar_slots: Dict[str, Dict[str, Any]] = {}
        self.directives: List[Dict[str, Any]] = []

    def upsert_slot(self, user_id: str, *, position=None, rotation=None, scale=None) -> Dict[str, Any]:
        slot = self.avatar_slots.setdefault(user_id, {
            "user_id": user_id,
            "position": [0.0, 0.0, 0.0],
            "rotation": [0.0, 0.0, 0.0],
            "scale": [1.0, 1.0, 1.0],
            "updated_at": time.time(),
        })
        if position is not None:
            slot["position"] = [float(x) for x in position]
        if rotation is not None:
            slot["rotation"] = [float(x) for x in rotation]
        if scale is not None:
            slot["scale"] = [float(x) for x in scale]
        slot["updated_at"] = time.time()
        return slot

    def snapshot(self) -> Dict[str, Any]:
        skins = []
        if self.ethereal_mgr:
            skins = self.ethereal_mgr.list_active_skins()
        return {
            "avatars": skins,
            "slots": list(self.avatar_slots.values()),
            "program": getattr(self.cameras.get_program_source(), 'source_id', None) if self.cameras else None,
            "preview": getattr(self.cameras.get_preview_source(), 'source_id', None) if self.cameras else None,
            "directives": self.directives[-20:],
        }

    def build_camera_directive(self, user_id: str, shot: str = 'medium', target: str = 'program') -> Dict[str, Any]:
        slot = self.avatar_slots.get(user_id)
        if not slot:
            raise KeyError(f"Unknown avatar slot for '{user_id}'")
        preset = SHOT_PRESETS.get(shot, SHOT_PRESETS['medium'])
        px, py, pz = preset['position']
        tx, ty, tz = slot['position']
        directive = {
            "type": "avatar_camera_directive",
            "target": target,
            "camera_shot": shot,
            "user_id": user_id,
            "camera": {
                "position": [tx + px, py, tz + pz],
                "look_at": [tx, ty + preset['look_at'][1], tz],
                "fov": preset['fov'],
            },
            "slot": slot,
            "timestamp": time.time(),
        }
        self.directives.append(directive)
        return directive

    async def architect_plan(self, room: str, prompt: str, focus_user_id: str = '') -> Dict[str, Any]:
        orch = get_orchestrator()
        history = []
        if self.hub:
            try:
                history = await self.hub.get_recent_history(room, limit=12)
            except Exception:
                history = []
        focus_skin = self.ethereal_mgr.get_skin(focus_user_id) if (self.ethereal_mgr and focus_user_id) else None
        ctx = ContextPacket(
            room_id=room,
            character_id=focus_user_id,
            character_name=getattr(getattr(focus_skin, 'avatar_state', None), 'display_name', '') if focus_skin else '',
            mood_state=getattr(focus_skin, 'mood', 'neutral') if focus_skin else 'neutral',
            history=[{"role": "assistant" if str(item.get('user_id','')).startswith('bot-') else 'user', "content": str(item.get('text',''))} for item in history],
            metadata={
                "avatar_slots": self.avatar_slots,
                "available_shots": list(SHOT_PRESETS.keys()),
                "program_camera": getattr(self.cameras.get_program_source(), 'source_id', None) if self.cameras else None,
                "preview_camera": getattr(self.cameras.get_preview_source(), 'source_id', None) if self.cameras else None,
            },
        )
        augmented = (
            "You are the Studio Architect for PubCast. Produce a compact production plan as JSON with keys: "
            "mood, gesture, recommended_shot, camera_target, blocking_note, dialogue_note. "
            "The recommended_shot must be one of: wide, medium, close, overhead, stage_left.\n\n"
            f"User request: {prompt}"
        )
        result = await orch.generate(augmented, role='architect', context=ctx, temperature=0.2, max_tokens=220)
        payload = {
            "type": "studio_architect_plan",
            "room": room,
            "focus_user_id": focus_user_id,
            "result": {
                "text": result.text,
                "model": result.model,
                "mind_used": result.mind_used,
                "fallback": result.fallback_occurred,
                "latency_ms": result.latency_ms,
            },
            "timestamp": time.time(),
        }
        self.directives.append(payload)
        return payload


def create_avatar_studio_router(bridge: AvatarStudioBridge) -> APIRouter:
    router = APIRouter(prefix='/api/avatar-studio', tags=['Avatar Studio'])

    @router.get('/state')
    async def get_state():
        return bridge.snapshot()

    @router.post('/slot')
    async def set_slot(request: Request, identity: Dict[str, Any] = Depends(current_identity)):
        body = await request.json()
        user_id = body.get('user_id')
        if not user_id:
            raise HTTPException(400, 'user_id required')
        slot = bridge.upsert_slot(
            user_id,
            position=body.get('position'),
            rotation=body.get('rotation'),
            scale=body.get('scale'),
        )
        if bridge.hub:
            await bridge.hub.broadcast_system_event({'type': 'avatar_slot_updated', 'payload': slot})
        return {'ok': True, 'slot': slot}

    @router.post('/camera/follow')
    async def camera_follow(request: Request, identity: Dict[str, Any] = Depends(require_role("mod"))):
        body = await request.json()
        user_id = body.get('user_id')
        if not user_id:
            raise HTTPException(400, 'user_id required')
        shot = body.get('shot', 'medium')
        target = body.get('target', 'program')
        try:
            directive = bridge.build_camera_directive(user_id, shot=shot, target=target)
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        if bridge.hub:
            await bridge.hub.broadcast_system_event(directive)
        return {'ok': True, 'directive': directive}

    @router.post('/architect/plan')
    async def architect_plan(request: Request, identity: Dict[str, Any] = Depends(require_role("mod"))):
        body = await request.json()
        room = body.get('room', 'default')
        prompt = body.get('prompt', '').strip()
        if not prompt:
            raise HTTPException(400, 'prompt required')
        focus_user_id = body.get('focus_user_id', '')
        plan = await bridge.architect_plan(room, prompt, focus_user_id=focus_user_id)
        if bridge.hub:
            await bridge.hub.broadcast_system_event(plan)
        return {'ok': True, 'plan': plan}

    return router
