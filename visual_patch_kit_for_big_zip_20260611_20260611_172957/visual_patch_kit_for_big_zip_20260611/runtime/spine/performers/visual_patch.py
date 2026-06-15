"""Non-destructive visual patch requests for hiding avatar mesh/outfit glitches."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..event_bus import EventBus, FrozenDict


PATCH_KIND_COVER = "cover"
PATCH_KIND_EXTEND = "extend"
PATCH_KIND_FILL = "fill"
PATCH_KIND_SOFTEN = "soften"

PATCH_STAGE_LIVE_VOXEL_OVERLAY = "live_voxel_overlay"
PATCH_STAGE_BAKE_PENDING = "bake_pending"
PATCH_STAGE_BAKED_MESH_OVERLAY = "baked_mesh_overlay"
PATCH_STAGE_RETIRED = "retired"


@dataclass(frozen=True)
class VoxelPatchSpec:
    patch_id: str
    performer_id: str
    anchor_joint: str
    kind: str
    reason: str
    voxel_size: float
    resolution: str
    dimensions: List[int]
    offset: List[float]
    color_hint: str = "match_outfit"
    opacity: float = 0.92
    blend_mode: str = "soft_overlay"
    stage: str = PATCH_STAGE_LIVE_VOXEL_OVERLAY
    live_update_mode: str = "add_subtract_voxels"
    bake_after_seconds: float = 0.75
    bake_to_mesh: bool = True
    mesh_policy: str = "temporary_overlay"
    output_mesh_name: Optional[str] = None
    ttl_seconds: float = 8.0
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualPatchCoordinator:
    """Create temporary voxel overlay specs from mesh glitches or conductor requests."""

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._tokens: List[str] = []
        self.last_patch: Optional[VoxelPatchSpec] = None

    def start(self) -> List[str]:
        if not self._tokens:
            self._tokens = [
                self.event_bus.subscribe("avatar:mesh_glitch", self._on_mesh_glitch),
                self.event_bus.subscribe("jeremy:visual_patch_request", self._on_jeremy_request),
                self.event_bus.subscribe("motion:feedback", self._on_motion_feedback),
                self.event_bus.subscribe("avatar:visual_patch_mesh_ready", self._on_mesh_ready),
            ]
        return list(self._tokens)

    def stop(self):
        for token in self._tokens:
            self.event_bus.unsubscribe(token)
        self._tokens = []

    def build_patch(self, payload: Dict[str, Any]) -> Optional[VoxelPatchSpec]:
        performer_id = str(payload.get("performer_id") or payload.get("avatar_id") or "unknown")
        issue = _normalize_issue(str(payload.get("issue") or payload.get("reason") or payload.get("hint") or ""))
        if issue == "none":
            return None

        anchor = str(payload.get("anchor_joint") or _anchor_for_issue(issue))
        kind = str(payload.get("kind") or _kind_for_issue(issue))
        dimensions = _vector_int(payload.get("dimensions"), _dimensions_for_issue(issue), 3)
        offset = _vector_float(payload.get("offset"), _offset_for_issue(issue), 3)
        voxel_size = _float(payload.get("voxel_size"), _voxel_size_for_issue(issue))
        resolution = str(payload.get("resolution") or _resolution_for_issue(issue))
        ttl = _float(payload.get("ttl_seconds"), 8.0)
        bake_after = _float(payload.get("bake_after_seconds"), 0.75)
        opacity = _clamp(_float(payload.get("opacity"), 0.92), 0.1, 1.0)
        color_hint = str(payload.get("color_hint") or _color_for_issue(issue))
        patch_id = str(payload.get("patch_id") or f"patch_{performer_id}_{issue}_{int(time.time() * 1000)}")
        bake_to_mesh = bool(payload.get("bake_to_mesh", True))
        mesh_policy = str(payload.get("mesh_policy") or "temporary_overlay")
        output_mesh_name = str(payload.get("output_mesh_name") or f"{patch_id}_mesh")
        stage = str(payload.get("stage") or PATCH_STAGE_LIVE_VOXEL_OVERLAY)
        live_update_mode = str(payload.get("live_update_mode") or "add_subtract_voxels")

        return VoxelPatchSpec(
            patch_id=patch_id,
            performer_id=performer_id,
            anchor_joint=anchor,
            kind=kind,
            reason=issue,
            voxel_size=voxel_size,
            resolution=resolution,
            dimensions=dimensions,
            offset=offset,
            color_hint=color_hint,
            opacity=opacity,
            ttl_seconds=ttl,
            stage=stage,
            live_update_mode=live_update_mode,
            bake_after_seconds=bake_after,
            bake_to_mesh=bake_to_mesh,
            mesh_policy=mesh_policy,
            output_mesh_name=output_mesh_name,
        )

    def _publish_patch(self, patch: Optional[VoxelPatchSpec], source: str):
        if patch is None:
            return
        self.last_patch = patch
        self.event_bus.emit("avatar:visual_patch", patch.to_dict(), source=source)
        if patch.bake_to_mesh:
            self.event_bus.emit(
                "avatar:visual_patch_bake_requested",
                {
                    **patch.to_dict(),
                    "next_stage": PATCH_STAGE_BAKE_PENDING,
                    "swap_strategy": "keep_live_voxels_until_mesh_ready",
                },
                source=source,
            )

    def _on_mesh_glitch(self, event: FrozenDict):
        self._publish_patch(self.build_patch(_plain_dict(event.get("data", {}))), "visual_patch_coordinator")

    def _on_jeremy_request(self, event: FrozenDict):
        self._publish_patch(self.build_patch(_plain_dict(event.get("data", {}))), "visual_patch_coordinator")

    def _on_motion_feedback(self, event: FrozenDict):
        data = _plain_dict(event.get("data", {}))
        hints = [str(item) for item in data.get("compensation_hints", [])]
        if "fallback_to_simpler_animation" not in hints and "avoid_forcing_joint_limits" not in hints:
            return
        patch = self.build_patch({
            "performer_id": data.get("performer_id"),
            "issue": "joint stress cover",
            "anchor_joint": "chest",
            "kind": PATCH_KIND_SOFTEN,
            "dimensions": [3, 2, 1],
            "offset": [0.0, 0.04, 0.08],
            "ttl_seconds": 4.0,
            "opacity": 0.65,
        })
        self._publish_patch(patch, "visual_patch_coordinator")

    def _on_mesh_ready(self, event: FrozenDict):
        data = _plain_dict(event.get("data", {}))
        self.event_bus.emit(
            "avatar:visual_patch_retired",
            {
                "patch_id": data.get("patch_id"),
                "performer_id": data.get("performer_id"),
                "mesh_id": data.get("mesh_id") or data.get("output_mesh_name"),
                "retire_reason": "baked_mesh_ready",
            },
            source="visual_patch_coordinator",
        )


def parse_visual_patch_request(text: str, performer_id: str = "unknown") -> Dict[str, Any]:
    """Turn a short Jeremy-style phrase into a patch request payload."""

    raw = str(text or "").lower()
    payload: Dict[str, Any] = {"performer_id": performer_id, "issue": raw}
    if "shoulder" in raw:
        payload["anchor_joint"] = "upperarm_l" if "left" in raw else "upperarm_r" if "right" in raw else "chest"
    elif "pants" in raw or "hem" in raw or "cuff" in raw:
        payload["anchor_joint"] = "calf_l" if "left" in raw else "calf_r" if "right" in raw else "hips"
    elif "jacket" in raw or "torso" in raw or "chest" in raw:
        payload["anchor_joint"] = "chest"
    if "lengthen" in raw or "longer" in raw or "extend" in raw:
        payload["kind"] = PATCH_KIND_EXTEND
    elif "fill" in raw or "gap" in raw:
        payload["kind"] = PATCH_KIND_FILL
    return payload


def _normalize_issue(issue: str) -> str:
    text = " ".join(issue.lower().replace("_", " ").split())
    if not text:
        return "none"
    if "shoulder" in text and ("pop" in text or "clip" in text or "jacket" in text):
        return "shoulder_clip"
    if "pants" in text or "hem" in text or "cuff" in text:
        return "pants_extension"
    if "jacket" in text and ("gap" in text or "inside" in text or "clip" in text):
        return "jacket_gap"
    if "body" in text and ("poke" in text or "clip" in text):
        return "body_clip"
    if "joint stress" in text:
        return "motion_stress_cover"
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")[:48] or "generic_patch"


def _anchor_for_issue(issue: str) -> str:
    return {
        "shoulder_clip": "chest",
        "pants_extension": "calf_l",
        "jacket_gap": "chest",
        "body_clip": "chest",
        "motion_stress_cover": "chest",
    }.get(issue, "chest")


def _kind_for_issue(issue: str) -> str:
    return {
        "pants_extension": PATCH_KIND_EXTEND,
        "jacket_gap": PATCH_KIND_FILL,
        "motion_stress_cover": PATCH_KIND_SOFTEN,
    }.get(issue, PATCH_KIND_COVER)


def _dimensions_for_issue(issue: str) -> List[int]:
    return {
        "shoulder_clip": [2, 2, 1],
        "pants_extension": [2, 3, 1],
        "jacket_gap": [3, 2, 1],
        "body_clip": [3, 3, 1],
        "motion_stress_cover": [3, 2, 1],
    }.get(issue, [2, 2, 1])


def _offset_for_issue(issue: str) -> List[float]:
    return {
        "shoulder_clip": [0.0, 0.08, 0.06],
        "pants_extension": [0.0, -0.18, 0.02],
        "jacket_gap": [0.0, 0.02, 0.08],
        "body_clip": [0.0, 0.0, 0.08],
        "motion_stress_cover": [0.0, 0.04, 0.08],
    }.get(issue, [0.0, 0.0, 0.05])


def _voxel_size_for_issue(issue: str) -> float:
    return {
        "shoulder_clip": 0.025,
        "jacket_gap": 0.025,
        "body_clip": 0.03,
        "pants_extension": 0.035,
        "motion_stress_cover": 0.04,
    }.get(issue, 0.04)


def _resolution_for_issue(issue: str) -> str:
    return {
        "shoulder_clip": "fine",
        "jacket_gap": "fine",
        "body_clip": "fine",
        "pants_extension": "medium",
        "motion_stress_cover": "coarse",
    }.get(issue, "medium")


def _color_for_issue(issue: str) -> str:
    return "match_outfit" if issue in {"shoulder_clip", "pants_extension", "jacket_gap"} else "match_skin_or_outfit"


def _plain_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, FrozenDict):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _vector_int(value: Any, fallback: List[int], length: int) -> List[int]:
    try:
        items = list(value)
    except TypeError:
        items = fallback
    out = []
    for index in range(length):
        try:
            out.append(max(1, int(items[index])))
        except (IndexError, TypeError, ValueError):
            out.append(fallback[index])
    return out


def _vector_float(value: Any, fallback: List[float], length: int) -> List[float]:
    try:
        items = list(value)
    except TypeError:
        items = fallback
    out = []
    for index in range(length):
        try:
            out.append(float(items[index]))
        except (IndexError, TypeError, ValueError):
            out.append(fallback[index])
    return out


def _float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
