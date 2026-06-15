"""Access-controlled visual patch-up workflow for selected screen regions."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..event_bus import EventBus, FrozenDict
from .styling_aide import VisualPatchUpKit, parse_styling_request


AUTHORIZED_ROLES = {"director", "host", "producer", "technical_director", "visual_artist"}
AUTHORIZED_AVATAR_PERMISSION = "visual_patch"

WORKFLOW_STAGE_REQUESTED = "requested"
WORKFLOW_STAGE_INSPECTED = "inspected"
WORKFLOW_STAGE_MEASURED = "measured"
WORKFLOW_STAGE_REPAIR_ATTEMPTED = "repair_attempted"
WORKFLOW_STAGE_PATCH_REQUESTED = "patch_requested"
SELECTION_MODE_2D = "2d"
SELECTION_MODE_3D = "3d"
MAX_LIGHTWEIGHT_OBJ_BYTES = 10 * 1024 * 1024
REPAIR_EFFORT_LIVE = "live"
REPAIR_EFFORT_BALANCED = "balanced"
REPAIR_EFFORT_METICULOUS = "meticulous"
REPAIR_EFFORT_POLICIES: Dict[str, Dict[str, Any]] = {
    REPAIR_EFFORT_LIVE: {
        "effort": REPAIR_EFFORT_LIVE,
        "latency_budget_ms": 80,
        "first_response": "live_coarse_overlay",
        "inspection_depth": "screen_region_and_known_targets",
        "quality_passes": 1,
        "requires_remeasure_before_bake": False,
    },
    REPAIR_EFFORT_BALANCED: {
        "effort": REPAIR_EFFORT_BALANCED,
        "latency_budget_ms": 250,
        "first_response": "live_overlay_then_refine",
        "inspection_depth": "screen_region_depth_and_asset_metadata",
        "quality_passes": 2,
        "requires_remeasure_before_bake": True,
    },
    REPAIR_EFFORT_METICULOUS: {
        "effort": REPAIR_EFFORT_METICULOUS,
        "latency_budget_ms": 900,
        "first_response": "live_safe_overlay_then_meticulous_refine",
        "inspection_depth": "screen_region_depth_asset_metadata_and_fit_constraints",
        "quality_passes": 4,
        "requires_remeasure_before_bake": True,
    },
}


@dataclass(frozen=True)
class VisualSelection:
    selection_id: str
    view_id: str
    selection_mode: str
    bounds: Dict[str, float]
    target_ids: List[str] = field(default_factory=list)
    screenshot_ref: Optional[str] = None
    depth_ref: Optional[str] = None
    world_hint: Dict[str, Any] = field(default_factory=dict)
    avatar_file_ref: Optional[str] = None
    object_file_ref: Optional[str] = None
    contact_constraints: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PatchRequester:
    requester_id: str
    role: str
    permissions: List[str] = field(default_factory=list)
    avatar_id: Optional[str] = None

    def can_request_patch(self) -> bool:
        return self.role in AUTHORIZED_ROLES or AUTHORIZED_AVATAR_PERMISSION in self.permissions

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PatchWorkflowRequest:
    request_id: str
    requester: PatchRequester
    selection: VisualSelection
    description: str
    performer_id: Optional[str] = None
    room_id: Optional[str] = None
    repair_effort: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VisualInspectionReport:
    request_id: str
    stage: str
    visual_findings: List[str]
    measurements: Dict[str, Any]
    repair_attempt: Dict[str, Any]
    patch_needed: bool
    repair_effort: Dict[str, Any] = field(default_factory=dict)
    patch_payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VisualPatchWorkflow:
    """Menu/director/avatar workflow for selected-area visual repair."""

    def __init__(self, event_bus: EventBus, patch_kit: Optional[VisualPatchUpKit] = None):
        self.event_bus = event_bus
        self.patch_kit = patch_kit or VisualPatchUpKit(event_bus)
        self._tokens: List[str] = []
        self.last_report: Optional[VisualInspectionReport] = None

    def start(self) -> List[str]:
        if not self._tokens:
            self.patch_kit.start()
            self._tokens = [
                self.event_bus.subscribe("visual:patch_workflow_request", self._on_workflow_request),
            ]
        return list(self._tokens)

    def stop(self):
        for token in self._tokens:
            self.event_bus.unsubscribe(token)
        self._tokens = []
        self.patch_kit.stop()

    def handle_request(self, request: PatchWorkflowRequest, emit: bool = True) -> VisualInspectionReport:
        if not request.requester.can_request_patch():
            report = VisualInspectionReport(
                request_id=request.request_id,
                stage=WORKFLOW_STAGE_REQUESTED,
                visual_findings=[],
                measurements={},
                repair_attempt={"status": "denied", "reason": "requester lacks visual patch permission"},
                patch_needed=False,
                repair_effort=_workflow_effort_policy(request),
            )
            if emit:
                self.event_bus.emit("visual:patch_workflow_denied", report.to_dict(), source="visual_patch_workflow")
            return report

        findings = self.inspect_selection(request)
        measurements = self.measure_selection(request)
        repair_attempt = self.try_direct_repair(request, findings, measurements)
        patch_needed = repair_attempt.get("status") != "repaired"
        patch_payload: Dict[str, Any] = {}
        if patch_needed:
            patch_payload = self.build_patch_payload(request, findings, measurements, repair_attempt)

        report = VisualInspectionReport(
            request_id=request.request_id,
            stage=WORKFLOW_STAGE_PATCH_REQUESTED if patch_needed else WORKFLOW_STAGE_REPAIR_ATTEMPTED,
            visual_findings=findings,
            measurements=measurements,
            repair_attempt=repair_attempt,
            patch_needed=patch_needed,
            repair_effort=_workflow_effort_policy(request),
            patch_payload=patch_payload,
        )
        self.last_report = report
        if emit:
            self.event_bus.emit("visual:patch_workflow_report", report.to_dict(), source="visual_patch_workflow")
            if patch_needed:
                self.event_bus.emit("visual:style_request", patch_payload, source="visual_patch_workflow")
        return report

    def inspect_selection(self, request: PatchWorkflowRequest) -> List[str]:
        text = request.description.lower()
        findings: List[str] = []
        if any(word in text for word in ("popping", "poke", "inside", "through", "clip", "gap")):
            findings.append("mesh_or_costume_clipping")
        if any(word in text for word in ("too small", "small", "tiny")):
            findings.append("object_scale_mismatch")
        if any(word in text for word in ("box", "impact", "collision", "alignment")):
            findings.append("contact_or_impact_alignment_issue")
        if any(word in text for word in ("color", "shade", "texture", "blend")):
            findings.append("surface_blend_issue")
        return findings or ["visual_issue_in_selected_region"]

    def measure_selection(self, request: PatchWorkflowRequest) -> Dict[str, Any]:
        bounds = request.selection.bounds
        width = _float(bounds.get("width"), 0.0)
        height = _float(bounds.get("height"), 0.0)
        area = max(width, 0.0) * max(height, 0.0)
        return {
            "screen_bounds": dict(bounds),
            "screen_area": round(area, 3),
            "selection_mode": request.selection.selection_mode,
            "target_ids": list(request.selection.target_ids),
            "has_depth_ref": bool(request.selection.depth_ref),
            "world_hint": dict(request.selection.world_hint),
            "contact_constraints": dict(request.selection.contact_constraints),
            "repair_effort": _workflow_effort_policy(request),
            "measurement_status": _measurement_status(request.selection),
            "asset_files": _asset_file_measurements(request.selection),
            "fit_problem": _fit_problem(request.selection),
            **_world_measurements(request.selection),
        }

    def try_direct_repair(
        self,
        request: PatchWorkflowRequest,
        findings: List[str],
        measurements: Dict[str, Any],
    ) -> Dict[str, Any]:
        if "surface_blend_issue" in findings and "mesh_or_costume_clipping" not in findings:
            return {
                "status": "repaired",
                "method": "material_or_texture_blend_adjustment",
                "notes": "selected issue appears blendable without geometry patch",
            }
        return {
            "status": "needs_patch",
            "method": "live_voxel_overlay_then_mesh_bake",
            "notes": "direct repair is not sufficient for selected physical mismatch",
            "live_first": True,
            "quality_passes": _workflow_effort_policy(request).get("quality_passes", 1),
            "requires_remeasure_before_bake": _workflow_effort_policy(request).get("requires_remeasure_before_bake", False),
        }

    def build_patch_payload(
        self,
        request: PatchWorkflowRequest,
        findings: List[str],
        measurements: Dict[str, Any],
        repair_attempt: Dict[str, Any],
    ) -> Dict[str, Any]:
        metadata = {
            "selection": request.selection.to_dict(),
            "findings": list(findings),
            "measurements": dict(measurements),
            "repair_attempt": dict(repair_attempt),
        }
        return {
            "performer_id": request.performer_id,
            "room_id": request.room_id,
            "description": request.description,
            "urgency": "live",
            "repair_effort": _workflow_effort_policy(request),
            "metadata": metadata,
        }

    def _on_workflow_request(self, event: FrozenDict):
        data = _plain_dict(event.get("data", {}))
        request = workflow_request_from_payload(data)
        self.handle_request(request, emit=True)


def workflow_request_from_payload(payload: Dict[str, Any]) -> PatchWorkflowRequest:
    requester_data = _plain_dict(payload.get("requester", {}))
    selection_data = _plain_dict(payload.get("selection", {}))
    requester = PatchRequester(
        requester_id=str(requester_data.get("requester_id") or payload.get("requester_id") or "unknown"),
        role=str(requester_data.get("role") or payload.get("role") or "viewer"),
        permissions=[str(item) for item in requester_data.get("permissions", payload.get("permissions", []))],
        avatar_id=requester_data.get("avatar_id") or payload.get("avatar_id"),
    )
    selection = VisualSelection(
        selection_id=str(selection_data.get("selection_id") or f"selection_{int(time.time() * 1000)}"),
        view_id=str(selection_data.get("view_id") or payload.get("view_id") or "stage"),
        selection_mode=_selection_mode(selection_data.get("selection_mode") or payload.get("selection_mode")),
        bounds={str(key): _float(value, 0.0) for key, value in _plain_dict(selection_data.get("bounds", {})).items()},
        target_ids=[str(item) for item in selection_data.get("target_ids", payload.get("target_ids", []))],
        screenshot_ref=selection_data.get("screenshot_ref") or payload.get("screenshot_ref"),
        depth_ref=selection_data.get("depth_ref") or payload.get("depth_ref"),
        world_hint=_plain_dict(selection_data.get("world_hint", {})),
        avatar_file_ref=selection_data.get("avatar_file_ref") or payload.get("avatar_file_ref"),
        object_file_ref=selection_data.get("object_file_ref") or payload.get("object_file_ref"),
        contact_constraints=_plain_dict(selection_data.get("contact_constraints", payload.get("contact_constraints", {}))),
    )
    return PatchWorkflowRequest(
        request_id=str(payload.get("request_id") or f"patch_workflow_{int(time.time() * 1000)}"),
        requester=requester,
        selection=selection,
        description=str(payload.get("description") or payload.get("text") or ""),
        performer_id=payload.get("performer_id"),
        room_id=payload.get("room_id"),
        repair_effort=_workflow_effort_from_payload(payload),
    )


def _plain_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, FrozenDict):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _selection_mode(value: Any) -> str:
    text = str(value or "").lower().strip()
    return SELECTION_MODE_3D if text in {"3d", "world", "mesh", "depth"} else SELECTION_MODE_2D


def _workflow_effort_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = payload.get("repair_effort") or payload.get("effort") or payload.get("visual_patch_effort")
    if isinstance(raw, dict):
        value = raw.get("effort") or raw.get("level") or raw.get("name")
    else:
        value = raw
    if _bool(payload.get("meticulous_repair"), False):
        value = REPAIR_EFFORT_METICULOUS
    return _effort_policy(value)


def _workflow_effort_policy(request: PatchWorkflowRequest) -> Dict[str, Any]:
    if request.repair_effort:
        return _effort_policy(request.repair_effort.get("effort") or request.repair_effort.get("requested_effort"))
    return _effort_policy(None)


def _effort_policy(value: Any) -> Dict[str, Any]:
    effort = str(value or REPAIR_EFFORT_LIVE).strip().lower().replace("-", "_")
    if effort in {"fast", "realtime", "real_time", "on_the_fly"}:
        effort = REPAIR_EFFORT_LIVE
    elif effort in {"careful", "precise", "deep", "high_quality"}:
        effort = REPAIR_EFFORT_METICULOUS
    elif effort not in REPAIR_EFFORT_POLICIES:
        effort = REPAIR_EFFORT_LIVE
    policy = dict(REPAIR_EFFORT_POLICIES[effort])
    policy["requested_effort"] = str(value or effort)
    policy["same_runtime_path"] = True
    return policy


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _measurement_status(selection: VisualSelection) -> str:
    if selection.selection_mode == SELECTION_MODE_3D:
        return "estimated_from_3d_selection" if selection.depth_ref or selection.world_hint else "needs_depth_or_world_measurement"
    return "estimated_from_2d_selection"


def _world_measurements(selection: VisualSelection) -> Dict[str, Any]:
    if selection.selection_mode != SELECTION_MODE_3D:
        return {}
    hint = selection.world_hint
    size = hint.get("size") if isinstance(hint, dict) else None
    if isinstance(size, (list, tuple)) and len(size) >= 3:
        world_size = [_float(size[index], math.nan) for index in range(3)]
        if not all(math.isfinite(value) for value in world_size):
            return {"world_size": None, "world_volume": None, "world_measurement_warning": "invalid_world_size_hint"}
        return {
            "world_size": world_size,
            "world_volume": round(world_size[0] * world_size[1] * world_size[2], 6),
        }
    return {"world_size": None, "world_volume": None}


def _asset_file_measurements(selection: VisualSelection) -> Dict[str, Any]:
    assets: Dict[str, Any] = {}
    if selection.avatar_file_ref:
        assets["avatar"] = _load_asset_file(selection.avatar_file_ref)
    if selection.object_file_ref:
        assets["object"] = _load_asset_file(selection.object_file_ref)
    return assets


def _load_asset_file(file_ref: str) -> Dict[str, Any]:
    path = Path(str(file_ref)).expanduser()
    summary: Dict[str, Any] = {
        "file_ref": str(file_ref),
        "extension": path.suffix.lower(),
        "load_status": "missing",
    }
    if not path.exists() or not path.is_file():
        return summary

    try:
        byte_size = path.stat().st_size
    except OSError as exc:
        summary.update({"load_status": "error", "error": str(exc)})
        return summary

    summary.update(
        {
            "load_status": "metadata_loaded",
            "file_name": path.name,
            "byte_size": byte_size,
        }
    )
    if path.suffix.lower() == ".obj":
        if byte_size > MAX_LIGHTWEIGHT_OBJ_BYTES:
            summary["geometry_status"] = "deferred_large_obj_needs_dedicated_mesh_loader"
            return summary
        summary.update(_measure_obj_file(path))
    elif path.suffix.lower() in {".gltf", ".glb", ".fbx", ".vrm"}:
        summary["geometry_status"] = "needs_dedicated_mesh_loader"
    else:
        summary["geometry_status"] = "unsupported_geometry_format"
    return summary


def _measure_obj_file(path: Path) -> Dict[str, Any]:
    vertex_count = 0
    skipped_vertex_count = 0
    face_count = 0
    mins = [float("inf"), float("inf"), float("inf")]
    maxs = [float("-inf"), float("-inf"), float("-inf")]
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if line.startswith("v "):
                    parts = line.split()
                    if len(parts) < 4:
                        skipped_vertex_count += 1
                        continue
                    coords = [_float(parts[index], math.nan) for index in range(1, 4)]
                    if not all(math.isfinite(coord) for coord in coords):
                        skipped_vertex_count += 1
                        continue
                    vertex_count += 1
                    for index, coord in enumerate(coords):
                        mins[index] = min(mins[index], coord)
                        maxs[index] = max(maxs[index], coord)
                elif line.startswith("f "):
                    face_count += 1
    except OSError as exc:
        return {"load_status": "error", "error": str(exc)}

    if vertex_count == 0:
        return {
            "geometry_status": "obj_loaded_without_valid_vertices",
            "vertex_count": 0,
            "skipped_vertex_count": skipped_vertex_count,
            "face_count": face_count,
        }

    size = [round(maxs[index] - mins[index], 6) for index in range(3)]
    return {
        "load_status": "geometry_loaded",
        "geometry_status": "obj_bounds_loaded",
        "vertex_count": vertex_count,
        "skipped_vertex_count": skipped_vertex_count,
        "face_count": face_count,
        "bounds": {"min": [round(value, 6) for value in mins], "max": [round(value, 6) for value in maxs]},
        "size": size,
        "volume": round(size[0] * size[1] * size[2], 6),
    }


def _fit_problem(selection: VisualSelection) -> Dict[str, Any]:
    has_avatar = bool(selection.avatar_file_ref)
    has_object = bool(selection.object_file_ref)
    if not has_avatar and not has_object:
        return {"status": "no_uploaded_asset_pair", "solver": None}

    status = "ready_for_avatar_object_fit" if has_avatar and has_object else "needs_avatar_and_object_pair"
    constraints = dict(selection.contact_constraints)
    return {
        "status": status,
        "solver": "avatar_object_contact_fit" if has_avatar and has_object else None,
        "avatar_file_ref": selection.avatar_file_ref,
        "object_file_ref": selection.object_file_ref,
        "constraint_keys": sorted(str(key) for key in constraints.keys()),
        "preferred_contact": constraints.get("contact") or constraints.get("interaction") or "meet_selected_targets",
        "alignment": {
            "avatar_anchor": constraints.get("avatar_anchor") or "auto_from_avatar_skeleton",
            "object_anchor": constraints.get("object_anchor") or "auto_from_object_bounds",
            "preserve_original_assets": True,
        },
    }
