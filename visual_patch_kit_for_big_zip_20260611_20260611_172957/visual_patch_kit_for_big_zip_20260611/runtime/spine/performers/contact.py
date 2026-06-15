"""Avatar/object contact resolution for stations, props, and hotspots."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..event_bus import EventBus
from .animation_presets import normalize_animation_name, sample_preset_pose


CONTACT_ANCHORS = {
    "sit": ("hips", "thigh_l", "thigh_r", "calf_l", "calf_r", "foot_l", "foot_r"),
    "type": ("hand_l", "hand_r", "upperarm_l", "upperarm_r", "lowerarm_l", "lowerarm_r"),
    "reach": ("hand_r", "upperarm_r", "lowerarm_r"),
    "stand": ("foot_l", "foot_r", "hips"),
}


@dataclass
class ContactObject:
    object_id: str
    object_type: str
    position: List[float]
    radius: float = 0.5
    animation_hint: str = ""
    contact_points: Dict[str, List[float]] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_hotspot(cls, room: str, hotspot: Dict[str, Any]) -> "ContactObject":
        action = hotspot.get("action", {})
        pos = hotspot.get("position", {})
        hint = action.get("animation") or action.get("event") or hotspot.get("id", "")
        return cls(
            object_id=str(hotspot.get("id", "hotspot")),
            object_type=_infer_object_type(str(hotspot.get("id", "")), str(hint)),
            position=[float(pos.get("x", 0.0)), 0.0, float(pos.get("y", 0.0))],
            radius=float(hotspot.get("radius", 40.0)),
            animation_hint=str(hint),
            metadata={"room": room, "source": "hotspot", "action": action},
        )


@dataclass
class ContactResolution:
    performer_id: str
    object_id: str
    contact_type: str
    animation: str
    anchors: List[str]
    distance: float
    reachable: bool
    warnings: List[str] = field(default_factory=list)
    pose_delta: Dict[str, Dict[str, List[float]]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContactResolver:
    """Resolve how an avatar should make contact with an object."""

    def __init__(self, event_bus: Optional[EventBus] = None, max_reach: float = 1.25):
        self.event_bus = event_bus
        self.max_reach = max_reach

    def resolve(self, performer: Any, contact_object: ContactObject, emit: bool = True) -> ContactResolution:
        contact_type = _contact_type_for(contact_object)
        animation = normalize_animation_name(contact_object.animation_hint or contact_type)
        anchors = list(CONTACT_ANCHORS.get(animation, CONTACT_ANCHORS.get(contact_type, ("hips",))))
        distance = _distance(getattr(performer, "position", [0.0, 0.0, 0.0]), contact_object.position)
        reachable = distance <= max(self.max_reach, contact_object.radius)
        warnings = self._warnings_for(performer, contact_object, anchors, reachable)
        resolution = ContactResolution(
            performer_id=getattr(performer, "performer_id", "unknown"),
            object_id=contact_object.object_id,
            contact_type=contact_type,
            animation=animation,
            anchors=anchors,
            distance=distance,
            reachable=reachable,
            warnings=warnings,
            pose_delta=sample_preset_pose(animation, elapsed=999.0),
        )
        if emit and self.event_bus is not None:
            self.event_bus.emit("performer:contact", resolution.to_dict(), source="contact_resolver")
        return resolution

    def _warnings_for(self, performer: Any, contact_object: ContactObject, anchors: List[str], reachable: bool) -> List[str]:
        warnings: List[str] = []
        skeleton = getattr(performer, "avatar_skeleton", None)
        skeleton_joints = set(getattr(skeleton, "joints", {}).keys())
        if skeleton_joints:
            missing = [anchor for anchor in anchors if anchor not in skeleton_joints]
            if missing:
                warnings.append(f"missing skeleton anchors: {', '.join(missing)}")
        if not reachable:
            warnings.append(
                f"object {contact_object.object_id!r} is outside contact reach"
            )
        return warnings


def _contact_type_for(contact_object: ContactObject) -> str:
    object_type = contact_object.object_type.lower()
    hint = contact_object.animation_hint.lower()
    if any(word in object_type or word in hint for word in ("couch", "chair", "seat", "sit")):
        return "sit"
    if any(word in object_type or word in hint for word in ("typewriter", "keyboard", "desk", "script")):
        return "type"
    if any(word in object_type or word in hint for word in ("tv", "button", "switch", "phone", "board")):
        return "reach"
    return "stand"


def _infer_object_type(object_id: str, hint: str) -> str:
    text = f"{object_id} {hint}".lower()
    for name in ("couch", "chair", "typewriter", "keyboard", "tv", "phone", "board", "wardrobe"):
        if name in text:
            return name
    if "sit" in text:
        return "couch"
    return "hotspot"


def _distance(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((float(a[index]) - float(b[index])) ** 2 for index in range(3)))

