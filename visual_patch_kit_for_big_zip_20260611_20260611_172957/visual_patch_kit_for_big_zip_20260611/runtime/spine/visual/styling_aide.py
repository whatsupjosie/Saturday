"""Visual patch-up kit for sets, costuming, digital makeup, and repairs."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..event_bus import EventBus, FrozenDict
from ..performers.visual_patch import VisualPatchCoordinator, parse_visual_patch_request


STYLE_DOMAIN_REPAIR = "repair"
STYLE_DOMAIN_COSTUME = "costume"
STYLE_DOMAIN_MAKEUP = "makeup"
STYLE_DOMAIN_SET = "set"


@dataclass(frozen=True)
class StylingRequest:
    request_id: str
    domain: str
    description: str
    performer_id: Optional[str] = None
    room_id: Optional[str] = None
    palette: str = "match_scene"
    urgency: str = "live"
    source: str = "visual_patch_up_kit"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StylingPlan:
    plan_id: str
    request_id: str
    domain: str
    description: str
    actions: List[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualPatchUpKit:
    """Route physical visual repair/styling requests into non-destructive overlays and plans."""

    def __init__(self, event_bus: EventBus, patch_coordinator: Optional[VisualPatchCoordinator] = None):
        self.event_bus = event_bus
        self.patch_coordinator = patch_coordinator or VisualPatchCoordinator(event_bus)
        self._tokens: List[str] = []
        self.last_plan: Optional[StylingPlan] = None

    def start(self) -> List[str]:
        if not self._tokens:
            self.patch_coordinator.start()
            self._tokens = [
                self.event_bus.subscribe("visual:style_request", self._on_style_request),
                self.event_bus.subscribe("jeremy:style_request", self._on_style_request),
            ]
        return list(self._tokens)

    def stop(self):
        for token in self._tokens:
            self.event_bus.unsubscribe(token)
        self._tokens = []
        self.patch_coordinator.stop()

    def plan(self, request: StylingRequest, emit: bool = True) -> StylingPlan:
        actions: List[Dict[str, Any]] = []
        warnings: List[str] = []

        if request.domain == STYLE_DOMAIN_REPAIR:
            patch_payload = parse_visual_patch_request(request.description, performer_id=request.performer_id or "unknown")
            patch_payload.update(request.metadata)
            patch_payload.setdefault("color_hint", request.palette)
            actions.append({"type": "visual_patch", "payload": patch_payload})
        elif request.domain == STYLE_DOMAIN_COSTUME:
            actions.append({
                "type": "costume_adjustment",
                "payload": {
                    "performer_id": request.performer_id,
                    "palette": request.palette,
                    "description": request.description,
                    "fit_policy": "non_destructive_overlay",
                    "can_use_voxel_mesh_patch": True,
                },
            })
            if _looks_like_fit_repair(request.description):
                patch_payload = parse_visual_patch_request(request.description, performer_id=request.performer_id or "unknown")
                patch_payload.setdefault("color_hint", request.palette)
                actions.append({"type": "visual_patch", "payload": patch_payload})
        elif request.domain == STYLE_DOMAIN_MAKEUP:
            actions.append({
                "type": "digital_makeup",
                "payload": {
                    "performer_id": request.performer_id,
                    "palette": request.palette,
                    "description": request.description,
                    "blend_mode": "skin_safe_overlay",
                    "anchor_region": _makeup_region(request.description),
                },
            })
        elif request.domain == STYLE_DOMAIN_SET:
            actions.append({
                "type": "set_dressing",
                "payload": {
                    "room_id": request.room_id,
                    "palette": request.palette,
                    "description": request.description,
                    "layer": "temporary_scene_overlay" if request.urgency == "live" else "baked_scene_update",
                },
            })
        else:
            warnings.append(f"unknown styling domain: {request.domain}")

        plan = StylingPlan(
            plan_id=f"style_plan_{int(time.time() * 1000)}",
            request_id=request.request_id,
            domain=request.domain,
            description=request.description,
            actions=actions,
            warnings=warnings,
        )
        self.last_plan = plan
        if emit:
            self.publish(plan)
        return plan

    def publish(self, plan: StylingPlan):
        payload = plan.to_dict()
        self.event_bus.emit("visual:style_plan", payload, source="visual_patch_up_kit")
        for action in plan.actions:
            action_type = action.get("type")
            action_payload = dict(action.get("payload", {}))
            if action_type == "visual_patch":
                self.event_bus.emit("jeremy:visual_patch_request", action_payload, source="visual_patch_up_kit")
            elif action_type == "costume_adjustment":
                self.event_bus.emit("costume:style_adjustment", action_payload, source="visual_patch_up_kit")
            elif action_type == "digital_makeup":
                self.event_bus.emit("avatar:digital_makeup", action_payload, source="visual_patch_up_kit")
            elif action_type == "set_dressing":
                self.event_bus.emit("set:style_adjustment", action_payload, source="visual_patch_up_kit")

    def _on_style_request(self, event: FrozenDict):
        data = _plain_dict(event.get("data", {}))
        request = parse_styling_request(
            str(data.get("description") or data.get("text") or data.get("hint") or ""),
            performer_id=data.get("performer_id"),
            room_id=data.get("room_id"),
            palette=str(data.get("palette") or "match_scene"),
            urgency=str(data.get("urgency") or "live"),
            metadata=_plain_dict(data.get("metadata", {})),
            source=str(event.get("source", "event")),
        )
        self.plan(request, emit=True)


VisualStylingAide = VisualPatchUpKit


def parse_styling_request(
    text: str,
    performer_id: Optional[str] = None,
    room_id: Optional[str] = None,
    palette: str = "match_scene",
    urgency: str = "live",
    metadata: Optional[Dict[str, Any]] = None,
    source: str = "visual_patch_up_kit",
) -> StylingRequest:
    description = " ".join(str(text or "").split())
    domain = _infer_domain(description)
    return StylingRequest(
        request_id=f"style_request_{int(time.time() * 1000)}",
        domain=domain,
        description=description,
        performer_id=performer_id,
        room_id=room_id,
        palette=palette,
        urgency=urgency,
        source=source,
        metadata=metadata or {},
    )


def _infer_domain(description: str) -> str:
    text = description.lower()
    if any(word in text for word in ("clip", "popping", "poke", "gap", "too short", "lengthen", "cover up")):
        return STYLE_DOMAIN_REPAIR
    if any(word in text for word in ("jacket", "pants", "dress", "shirt", "costume", "outfit", "sleeve", "hem", "cuff")):
        return STYLE_DOMAIN_COSTUME
    if any(word in text for word in ("makeup", "lip", "eye shadow", "eyeshadow", "blush", "foundation", "freckle")):
        return STYLE_DOMAIN_MAKEUP
    if any(word in text for word in ("set", "room", "wall", "floor", "chair", "desk", "lighting", "backdrop")):
        return STYLE_DOMAIN_SET
    return STYLE_DOMAIN_REPAIR


def _looks_like_fit_repair(description: str) -> bool:
    text = description.lower()
    return any(word in text for word in ("clip", "popping", "gap", "too short", "lengthen", "fit", "inside"))


def _makeup_region(description: str) -> str:
    text = description.lower()
    if "lip" in text:
        return "lips"
    if "eye" in text:
        return "eyes"
    if "cheek" in text or "blush" in text:
        return "cheeks"
    return "face"


def _plain_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, FrozenDict):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}
