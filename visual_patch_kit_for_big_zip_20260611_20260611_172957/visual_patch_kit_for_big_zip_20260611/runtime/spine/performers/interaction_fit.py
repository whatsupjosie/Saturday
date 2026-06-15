"""Proportional fitting for avatar interactions with props and stations."""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from ..event_bus import EventBus
from .contact import ContactObject, ContactResolver
from .motion_retargeting import measure_skeleton_metrics


DEFAULT_ADAPTIVE_VOXEL_SIZE = 0.035
MAX_ADAPTIVE_VOXEL_AXIS = 96
MAX_ADAPTIVE_VOXEL_COUNT = 120000
DEFAULT_ABSENCE_REGRESS_SECONDS = 120.0
DEFAULT_EXTENSION_CACHE_SECONDS = 600.0
REPAIR_EFFORT_LIVE = "live"
REPAIR_EFFORT_BALANCED = "balanced"
REPAIR_EFFORT_METICULOUS = "meticulous"
REPAIR_EFFORT_POLICIES: Dict[str, Dict[str, Any]] = {
    REPAIR_EFFORT_LIVE: {
        "effort": REPAIR_EFFORT_LIVE,
        "latency_budget_ms": 80,
        "first_response": "live_coarse_overlay",
        "voxel_resolution": "medium",
        "voxel_size_multiplier": 1.0,
        "budget_multiplier": 1.0,
        "bake_after_seconds": 0.75,
        "quality_passes": 1,
        "quality_checks": ["fit_bounds", "preserve_original_asset", "voxel_budget"],
        "requires_remeasure_before_bake": False,
    },
    REPAIR_EFFORT_BALANCED: {
        "effort": REPAIR_EFFORT_BALANCED,
        "latency_budget_ms": 250,
        "first_response": "live_overlay_then_refine",
        "voxel_resolution": "high",
        "voxel_size_multiplier": 0.82,
        "budget_multiplier": 1.35,
        "bake_after_seconds": 1.4,
        "quality_passes": 2,
        "quality_checks": ["fit_bounds", "preserve_original_asset", "voxel_budget", "anatomy_clearance"],
        "requires_remeasure_before_bake": True,
    },
    REPAIR_EFFORT_METICULOUS: {
        "effort": REPAIR_EFFORT_METICULOUS,
        "latency_budget_ms": 900,
        "first_response": "live_safe_overlay_then_meticulous_refine",
        "voxel_resolution": "ultra",
        "voxel_size_multiplier": 0.64,
        "budget_multiplier": 2.0,
        "bake_after_seconds": 2.5,
        "quality_passes": 4,
        "quality_checks": [
            "fit_bounds",
            "preserve_original_asset",
            "voxel_budget",
            "anatomy_clearance",
            "contact_stability",
            "visual_blend",
        ],
        "requires_remeasure_before_bake": True,
    },
}


@dataclass(frozen=True)
class InteractionFitReport:
    performer_id: str
    object_id: str
    object_type: str
    interaction: str
    fit_score: float
    risk_level: str
    proportional_scale: float
    anchor_offsets: Dict[str, List[float]] = field(default_factory=dict)
    object_adjustments: Dict[str, Any] = field(default_factory=dict)
    adaptive_extensions: List[Dict[str, Any]] = field(default_factory=list)
    compensation_hints: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    repair_effort: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InteractionFitCoordinator:
    """Create live object/avatar alignment hints without mutating props."""

    def __init__(self, event_bus: Optional[EventBus] = None, contact_resolver: Optional[ContactResolver] = None):
        self.event_bus = event_bus
        self.contact_resolver = contact_resolver or ContactResolver(event_bus)
        self._pending_extensions: Dict[str, Dict[str, Any]] = {}
        self._active_extensions: Dict[str, Dict[str, Any]] = {}
        self._extension_cache: Dict[str, Dict[str, Any]] = {}

    def evaluate(self, performer: Any, contact_object: ContactObject, emit: bool = True) -> InteractionFitReport:
        contact = self.contact_resolver.resolve(performer, contact_object, emit=False)
        skeleton = getattr(performer, "avatar_skeleton", None)
        metrics = measure_skeleton_metrics(skeleton)
        object_type = contact_object.object_type.lower()
        interaction = contact.contact_type
        body_scale = _clamp(metrics.height / 1.7, 0.35, 2.75)
        repair_effort = _repair_effort_policy(contact_object)
        warnings = list(contact.warnings)
        hints: List[str] = []
        anchor_offsets: Dict[str, List[float]] = {}
        object_adjustments: Dict[str, Any] = {}
        adaptive_extensions: List[Dict[str, Any]] = []
        score = 1.0

        if object_type in {"phone", "handset", "remote", "cup", "mug", "glass"}:
            ideal = 0.09 * metrics.arm / 0.53
            observed = _object_size(contact_object, default=contact_object.radius)
            ratio = _safe_ratio(observed, ideal)
            clearance_zones = _anatomy_clearance_zones(contact_object, skeleton, metrics, body_scale, "handheld")
            if ratio < 0.7:
                score -= 0.22
                warnings.append("object_too_small_for_hand")
                hints.extend(["scale_prop_proxy_up", "use_precision_grip"])
                object_adjustments["scale_proxy"] = round(_clamp(ideal / max(observed, 1e-6), 1.0, 2.5), 3)
            elif ratio > 1.45:
                score -= 0.16
                warnings.append("object_too_large_for_hand")
                hints.extend(["use_two_hand_grip", "scale_reach_conservatively"])
            if clearance_zones:
                warnings.append("anatomy_clearance_required")
                hints.append("preserve_anatomy_clearance")
                object_adjustments["clearance_zones"] = clearance_zones
            anchor_offsets["hand_r"] = [0.0, 0.0, 0.02 * body_scale]

        elif object_type in {"chair", "seat", "couch"} or interaction == "sit":
            seat_height = _metadata_float(contact_object, "seat_height", 0.45)
            ideal = max(metrics.leg * 0.52, 0.18)
            if abs(seat_height - ideal) > max(0.08, ideal * 0.22):
                score -= 0.22
                warnings.append("seat_height_mismatch")
                hints.extend(["adjust_hips_to_seat", "plant_feet_after_sit"])
                object_adjustments["seat_height_proxy"] = round(ideal, 3)
            extension = _build_seat_extension(
                performer_id=getattr(performer, "performer_id", "unknown"),
                contact_object=contact_object,
                skeleton=skeleton,
                metrics=metrics,
                body_scale=body_scale,
                seat_height=seat_height,
                ideal_seat_height=ideal,
                interaction=interaction,
                repair_effort=repair_effort,
            )
            if extension:
                hints.append("build_adaptive_prop_extension")
                warnings.append("prop_needs_adaptive_extension_for_performer_scale")
                if extension.get("clearance_zones"):
                    hints.append("preserve_anatomy_clearance")
                    warnings.append("anatomy_clearance_required")
                adaptive_extensions.append(extension)
            anchor_offsets["hips"] = [0.0, seat_height - 0.9, 0.0]
            anchor_offsets["foot_l"] = [-metrics.hip_width * 0.5, 0.0, 0.12]
            anchor_offsets["foot_r"] = [metrics.hip_width * 0.5, 0.0, 0.12]

        elif object_type in {"stairs", "stair", "steps"}:
            step_height = _metadata_float(contact_object, "step_height", 0.18)
            comfortable = max(metrics.leg * 0.22, 0.08)
            if step_height > comfortable:
                score -= 0.3
                warnings.append("step_height_too_large")
                hints.extend(["insert_intermediate_foot_targets", "raise_hips_before_step"])
                object_adjustments["virtual_step_height"] = round(comfortable, 3)
            anchor_offsets["foot_l"] = [-metrics.hip_width * 0.5, step_height, 0.12]
            anchor_offsets["foot_r"] = [metrics.hip_width * 0.5, step_height, 0.12]

        elif object_type in {"keyboard", "typewriter", "desk", "table"} or interaction == "type":
            desk_height = _metadata_float(contact_object, "desk_height", 0.72)
            ideal = max(metrics.spine * 0.9, 0.72 * body_scale, 0.45)
            if abs(desk_height - ideal) > 0.16:
                score -= 0.18
                warnings.append("typing_surface_height_mismatch")
                hints.extend(["adjust_elbow_height", "slide_keyboard_proxy"])
                object_adjustments["typing_surface_height_proxy"] = round(ideal, 3)
            extension = _build_surface_extension(
                performer_id=getattr(performer, "performer_id", "unknown"),
                contact_object=contact_object,
                metrics=metrics,
                body_scale=body_scale,
                surface_height=desk_height,
                ideal_surface_height=ideal,
                interaction=interaction,
                repair_effort=repair_effort,
            )
            if extension:
                hints.append("build_adaptive_prop_extension")
                warnings.append("prop_needs_adaptive_extension_for_performer_scale")
                adaptive_extensions.append(extension)
            reach = min(metrics.arm * 0.55, 0.42)
            anchor_offsets["hand_l"] = [-metrics.hip_width, desk_height - 1.35, reach]
            anchor_offsets["hand_r"] = [metrics.hip_width, desk_height - 1.35, reach]

        elif object_type in {"steering_wheel", "wheel", "vehicle_wheel"}:
            wheel_radius = _metadata_float(contact_object, "wheel_radius", 0.18)
            ideal = max(metrics.arm * 0.32, 0.12)
            if abs(wheel_radius - ideal) > 0.08:
                score -= 0.18
                warnings.append("steering_wheel_scale_mismatch")
                hints.extend(["scale_steering_proxy", "lock_two_hand_grip"])
                object_adjustments["wheel_radius_proxy"] = round(ideal, 3)
            anchor_offsets["hand_l"] = [-ideal * 0.85, 0.0, 0.0]
            anchor_offsets["hand_r"] = [ideal * 0.85, 0.0, 0.0]

        else:
            if not contact.reachable:
                score -= 0.2
                hints.append("move_avatar_or_proxy_into_reach")

        if contact.distance > max(contact_object.radius, self.contact_resolver.max_reach):
            score -= 0.18
            warnings.append("impact_box_or_contact_radius_misaligned")
            hints.append("expand_or_shift_contact_proxy")
            object_adjustments["contact_radius_proxy"] = round(max(contact.distance, contact_object.radius), 3)

        score = round(_clamp(score, 0.0, 1.0), 3)
        risk_level = _risk(score)
        report = InteractionFitReport(
            performer_id=getattr(performer, "performer_id", "unknown"),
            object_id=contact_object.object_id,
            object_type=contact_object.object_type,
            interaction=interaction,
            fit_score=score,
            risk_level=risk_level,
            proportional_scale=round(body_scale, 3),
            anchor_offsets=anchor_offsets,
            object_adjustments=object_adjustments,
            adaptive_extensions=adaptive_extensions,
            compensation_hints=_dedupe(hints),
            warnings=_dedupe(warnings),
            repair_effort=repair_effort,
        )
        if emit and self.event_bus is not None:
            self.event_bus.emit("interaction:fit_report", report.to_dict(), source="interaction_fit_coordinator")
            if report.compensation_hints or report.object_adjustments:
                self.event_bus.emit(
                    "avatar:interaction_compensation",
                    {
                        "performer_id": report.performer_id,
                        "object_id": report.object_id,
                        "interaction": report.interaction,
                        "anchor_offsets": report.anchor_offsets,
                        "object_adjustments": report.object_adjustments,
                        "adaptive_extensions": report.adaptive_extensions,
                        "hints": report.compensation_hints,
                        "risk_level": report.risk_level,
                        "repair_effort": report.repair_effort,
                    },
                    source="interaction_fit_coordinator",
                )
            for extension in report.adaptive_extensions:
                proposed = self._prepare_extension_proposal(extension, contact_object)
                extension_id = str(proposed["extension_id"])
                if _metadata_bool(contact_object, "adaptive_prop_auto_approve", False):
                    proposed["approval"]["state"] = "auto_approved"
                    proposed["approval"]["approved_at"] = time.time()
                    self._activate_extension(proposed, emit=True)
                else:
                    self._pending_extensions[extension_id] = proposed
                    self.event_bus.emit(
                        "object:adaptive_prop_extension_proposed",
                        proposed,
                        source="interaction_fit_coordinator",
                    )
        return report

    def pending_extensions(self) -> List[Dict[str, Any]]:
        return [dict(extension) for extension in self._pending_extensions.values()]

    def active_extensions(self) -> List[Dict[str, Any]]:
        return [dict(extension) for extension in self._active_extensions.values()]

    def cached_extensions(self) -> List[Dict[str, Any]]:
        return [dict(extension) for extension in self._extension_cache.values()]

    def mark_performer_present(self, performer_id: str, now: Optional[float] = None) -> int:
        timestamp = _now(now)
        count = 0
        for extension in self._active_extensions.values():
            if extension.get("performer_id") != performer_id:
                continue
            extension["presence"] = {
                "state": "present",
                "last_seen_at": timestamp,
                "absent_since": None,
            }
            count += 1
        return count

    def approve_extension(self, extension_id: str, emit: bool = True, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        extension = self._pending_extensions.pop(str(extension_id), None)
        if extension is None:
            return None
        timestamp = _now(now)
        approval = dict(extension.get("approval", {}))
        approval["state"] = "approved"
        approval["approved_at"] = timestamp
        extension["approval"] = approval
        return self._activate_extension(extension, emit=emit, now=timestamp)

    def reject_extension(
        self,
        extension_id: str,
        reason: str = "user_rejected",
        emit: bool = True,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        extension = self._pending_extensions.pop(str(extension_id), None)
        if extension is None:
            return None
        timestamp = _now(now)
        payload = {
            "extension_id": extension.get("extension_id"),
            "performer_id": extension.get("performer_id"),
            "object_id": extension.get("object_id"),
            "reject_reason": reason,
            "rejected_at": timestamp,
        }
        if emit and self.event_bus is not None:
            self.event_bus.emit(
                "object:adaptive_prop_extension_rejected",
                payload,
                source="interaction_fit_coordinator",
            )
        return payload

    def mark_performer_absent(self, performer_id: str, now: Optional[float] = None) -> int:
        timestamp = _now(now)
        count = 0
        for extension in self._active_extensions.values():
            if extension.get("performer_id") != performer_id:
                continue
            presence = dict(extension.get("presence", {}))
            presence["state"] = "absent"
            presence.setdefault("last_seen_at", timestamp)
            if presence.get("absent_since") is None:
                presence["absent_since"] = timestamp
            extension["presence"] = presence
            count += 1
        return count

    def sweep_presence(
        self,
        now: Optional[float] = None,
        regress_after_seconds: float = DEFAULT_ABSENCE_REGRESS_SECONDS,
        cache_ttl_seconds: float = DEFAULT_EXTENSION_CACHE_SECONDS,
        emit: bool = True,
    ) -> Dict[str, List[Dict[str, Any]]]:
        timestamp = _now(now)
        retired: List[Dict[str, Any]] = []
        for extension in list(self._active_extensions.values()):
            presence = dict(extension.get("presence", {}))
            absent_since = presence.get("absent_since")
            if _extension_kept_in_room(extension):
                continue
            if absent_since is None:
                continue
            try:
                elapsed = timestamp - float(absent_since)
            except (TypeError, ValueError):
                continue
            if elapsed >= max(0.0, regress_after_seconds):
                retired.extend(
                    self.retire_extensions_for(
                        str(extension.get("performer_id")),
                        object_id=str(extension.get("object_id")),
                        reason="performer_absent_timeout",
                        emit=emit,
                        now=timestamp,
                        cache_ttl_seconds=cache_ttl_seconds,
                    )
                )
        expired = self.expire_cached_extensions(now=timestamp, emit=emit)
        return {"retired": retired, "expired": expired}

    def retire_extensions_for(
        self,
        performer_id: str,
        object_id: Optional[str] = None,
        reason: str = "interaction_ended",
        emit: bool = True,
        now: Optional[float] = None,
        cache_ttl_seconds: float = DEFAULT_EXTENSION_CACHE_SECONDS,
        force: bool = False,
    ) -> List[Dict[str, Any]]:
        timestamp = _now(now)
        retired: List[Dict[str, Any]] = []
        for extension_id, extension in list(self._active_extensions.items()):
            if extension.get("performer_id") != performer_id:
                continue
            if object_id is not None and extension.get("object_id") != object_id:
                continue
            if not force and _extension_kept_in_room(extension) and reason in {"interaction_ended", "performer_absent_timeout"}:
                continue
            retired.append(self._active_extensions.pop(extension_id))

        payloads = []
        for extension in retired:
            cached_until = timestamp + max(0.0, cache_ttl_seconds)
            payload = {
                "extension_id": extension.get("extension_id"),
                "performer_id": extension.get("performer_id"),
                "object_id": extension.get("object_id"),
                "object_type": extension.get("object_type"),
                "interaction": extension.get("interaction"),
                "voxel_patch_id": _nested_get(extension, ["voxel_patch", "patch_id"]),
                "retire_reason": reason,
                "preserve_original_asset": extension.get("preserve_original_asset", True),
                "cached_at": timestamp,
                "cached_until": cached_until,
            }
            self._extension_cache[str(extension.get("extension_id"))] = {
                "extension": dict(extension),
                "retired": dict(payload),
                "cached_at": timestamp,
                "expires_at": cached_until,
            }
            payloads.append(payload)
            if emit and self.event_bus is not None:
                self.event_bus.emit(
                    "object:adaptive_prop_extension_retired",
                    payload,
                    source="interaction_fit_coordinator",
                )
                self.event_bus.emit(
                    "object:adaptive_prop_extension_cached",
                    self._extension_cache[str(extension.get("extension_id"))],
                    source="interaction_fit_coordinator",
                )
        return payloads

    def retire_all_extensions(self, reason: str = "coordinator_shutdown", emit: bool = True) -> List[Dict[str, Any]]:
        performer_ids = {str(extension.get("performer_id")) for extension in self._active_extensions.values()}
        retired: List[Dict[str, Any]] = []
        for performer_id in performer_ids:
            retired.extend(self.retire_extensions_for(performer_id, reason=reason, emit=emit))
        return retired

    def save_extension(
        self,
        extension_id: str,
        inventory_id: Optional[str] = None,
        save_to_inventory: bool = True,
        keep_in_room: bool = False,
        emit: bool = True,
        now: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        timestamp = _now(now)
        extension = self._find_extension_for_persistence(extension_id)
        if extension is None:
            return None

        persistence = dict(extension.get("persistence", {}))
        persistence.update(
            {
                "saved_to_inventory": bool(save_to_inventory),
                "inventory_id": inventory_id or f"inventory_{extension_id}",
                "keep_in_room": bool(keep_in_room),
                "saved_at": timestamp,
            }
        )
        extension["persistence"] = persistence
        if keep_in_room:
            extension["retire_when_interaction_ends"] = False
            presence = dict(extension.get("presence", {}))
            presence["state"] = "room_persistent"
            presence["absent_since"] = None
            extension["presence"] = presence

        payload = {
            "extension_id": extension.get("extension_id"),
            "performer_id": extension.get("performer_id"),
            "object_id": extension.get("object_id"),
            "inventory_id": persistence["inventory_id"],
            "saved_to_inventory": bool(save_to_inventory),
            "keep_in_room": bool(keep_in_room),
            "saved_at": timestamp,
        }
        if emit and self.event_bus is not None:
            if save_to_inventory:
                self.event_bus.emit(
                    "object:adaptive_prop_extension_saved",
                    payload,
                    source="interaction_fit_coordinator",
                )
            if keep_in_room:
                self.event_bus.emit(
                    "object:adaptive_prop_extension_kept_in_room",
                    payload,
                    source="interaction_fit_coordinator",
                )
        return payload

    def expire_cached_extensions(self, now: Optional[float] = None, emit: bool = True) -> List[Dict[str, Any]]:
        timestamp = _now(now)
        expired: List[Dict[str, Any]] = []
        for extension_id, cached in list(self._extension_cache.items()):
            try:
                expires_at = float(cached.get("expires_at", 0.0))
            except (TypeError, ValueError):
                expires_at = 0.0
            if timestamp < expires_at:
                continue
            expired_payload = {
                "extension_id": extension_id,
                "performer_id": _nested_get(cached, ["retired", "performer_id"]),
                "object_id": _nested_get(cached, ["retired", "object_id"]),
                "expired_at": timestamp,
                "cache_expire_reason": "cache_ttl_elapsed",
            }
            expired.append(expired_payload)
            self._extension_cache.pop(extension_id, None)
            if emit and self.event_bus is not None:
                self.event_bus.emit(
                    "object:adaptive_prop_extension_cache_expired",
                    expired_payload,
                    source="interaction_fit_coordinator",
                )
        return expired

    def _prepare_extension_proposal(self, extension: Dict[str, Any], contact_object: ContactObject) -> Dict[str, Any]:
        proposed = dict(extension)
        proposed["approval"] = {
            "state": "pending",
            "requires_user_ok": True,
            "auto_approve_available": True,
            "auto_approve": _metadata_bool(contact_object, "adaptive_prop_auto_approve", False),
        }
        proposed["persistence"] = {
            "saved_to_inventory": False,
            "inventory_id": None,
            "keep_in_room": False,
        }
        return proposed

    def _find_extension_for_persistence(self, extension_id: str) -> Optional[Dict[str, Any]]:
        key = str(extension_id)
        if key in self._active_extensions:
            return self._active_extensions[key]
        if key in self._pending_extensions:
            return self._pending_extensions[key]
        cached = self._extension_cache.get(key)
        if isinstance(cached, dict) and isinstance(cached.get("extension"), dict):
            return cached["extension"]
        return None

    def _activate_extension(
        self,
        extension: Dict[str, Any],
        emit: bool = True,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        timestamp = _now(now)
        active_extension = dict(extension)
        active_extension["presence"] = {
            "state": "present",
            "last_seen_at": timestamp,
            "absent_since": None,
        }
        self._active_extensions[str(active_extension["extension_id"])] = active_extension
        if emit and self.event_bus is not None:
            self.event_bus.emit(
                "object:adaptive_prop_extension",
                active_extension,
                source="interaction_fit_coordinator",
            )
        return active_extension


def _object_size(contact_object: ContactObject, default: float) -> float:
    for key in ("size", "diameter", "width"):
        value = contact_object.metadata.get(key)
        if isinstance(value, (int, float, str)):
            return _finite_float(value, default)
        if isinstance(value, list) and value:
            numbers = [_finite_float(item, math.nan) for item in value]
            numbers = [item for item in numbers if math.isfinite(item)]
            if numbers:
                return max(numbers)
    return float(default)


def _metadata_float(contact_object: ContactObject, key: str, default: float) -> float:
    return _finite_float(contact_object.metadata.get(key, default), default)


def _metadata_bool(contact_object: ContactObject, key: str, default: bool) -> bool:
    value = contact_object.metadata.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"0", "false", "no", "off", "disabled"}:
            return False
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
    return default


def _repair_effort_policy(contact_object: ContactObject) -> Dict[str, Any]:
    value = (
        contact_object.metadata.get("adaptive_prop_effort")
        or contact_object.metadata.get("repair_effort")
        or contact_object.metadata.get("visual_patch_effort")
    )
    if _metadata_bool(contact_object, "meticulous_repair", False):
        value = REPAIR_EFFORT_METICULOUS
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


def _adaptive_voxel_size(contact_object: ContactObject, repair_effort: Dict[str, Any]) -> float:
    configured = contact_object.metadata.get("adaptive_voxel_size")
    base = max(_metadata_float(contact_object, "adaptive_voxel_size", DEFAULT_ADAPTIVE_VOXEL_SIZE), 0.005)
    if configured is not None:
        return base
    multiplier = _finite_float(repair_effort.get("voxel_size_multiplier"), 1.0)
    return max(base * _clamp(multiplier, 0.35, 1.5), 0.005)


def _nested_get(value: Dict[str, Any], keys: List[str]) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _extension_kept_in_room(extension: Dict[str, Any]) -> bool:
    persistence = extension.get("persistence", {})
    return isinstance(persistence, dict) and bool(persistence.get("keep_in_room"))


def _safe_ratio(value: float, reference: float) -> float:
    return value / reference if reference > 0 else 1.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value if math.isfinite(value) else low))


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _now(value: Optional[float]) -> float:
    if value is None:
        return time.time()
    return _finite_float(value, time.time())


def _build_seat_extension(
    performer_id: str,
    contact_object: ContactObject,
    skeleton: Any,
    metrics: Any,
    body_scale: float,
    seat_height: float,
    ideal_seat_height: float,
    interaction: str,
    repair_effort: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _metadata_bool(contact_object, "adaptive_prop_extension", True):
        return None

    seat_width = _metadata_float(contact_object, "seat_width", 0.48)
    seat_depth = _metadata_float(contact_object, "seat_depth", 0.48)
    if ideal_seat_height < seat_height - max(0.08, ideal_seat_height * 0.22):
        return _build_small_seat_support(
            performer_id=performer_id,
            contact_object=contact_object,
            metrics=metrics,
            body_scale=body_scale,
            seat_height=seat_height,
            ideal_seat_height=ideal_seat_height,
            interaction=interaction,
            repair_effort=repair_effort,
        )
    if body_scale < 1.12 and ideal_seat_height <= seat_height + 0.06:
        return None

    target_width = max(seat_width, metrics.hip_width * 2.8, 0.48 * body_scale)
    target_depth = max(seat_depth, metrics.leg * 0.48, 0.48 * body_scale)
    add_height = max(0.0, ideal_seat_height - seat_height)
    add_width = max(0.0, target_width - seat_width)
    add_depth = max(0.0, target_depth - seat_depth)

    if add_height < 0.035 and add_width < 0.04 and add_depth < 0.04:
        return None

    voxel_size = _adaptive_voxel_size(contact_object, repair_effort)
    clearance_zones = _anatomy_clearance_zones(contact_object, skeleton, metrics, body_scale, "seat")
    dimensions, voxel_budget = _voxel_dimensions(
        [target_width, max(add_height, voxel_size), target_depth],
        voxel_size,
        contact_object,
        repair_effort,
    )
    extension_id = f"fit_ext_{performer_id}_{contact_object.object_id}_{int(time.time() * 1000)}"
    return {
        "extension_id": extension_id,
        "performer_id": performer_id,
        "object_id": contact_object.object_id,
        "object_type": contact_object.object_type,
        "interaction": interaction,
        "kind": "seat_build_up",
        "reason": "avatar_larger_than_standard_prop",
        "proportional_scale": round(body_scale, 3),
        "preserve_original_asset": True,
        "clearance_zones": clearance_zones,
        "retire_when_interaction_ends": True,
        "render_mode": "live_voxel_overlay_then_mesh_bake",
        "repair_effort": dict(repair_effort),
        "quality_assurance": _quality_assurance("seat_build_up", repair_effort, clearance_zones, voxel_budget),
        "voxel_budget": voxel_budget,
        "dimensions_meters": {
            "seat_height": round(seat_height, 3),
            "target_seat_height": round(max(seat_height, ideal_seat_height), 3),
            "add_height": round(add_height, 3),
            "seat_width": round(seat_width, 3),
            "target_width": round(target_width, 3),
            "add_width": round(add_width, 3),
            "seat_depth": round(seat_depth, 3),
            "target_depth": round(target_depth, 3),
            "add_depth": round(add_depth, 3),
        },
        "voxel_patch": {
            "patch_id": f"{extension_id}_voxels",
            "anchor_object_id": contact_object.object_id,
            "anchor": "seat_surface",
            "kind": "extend",
            "reason": "adaptive_seat_extension",
            "voxel_size": round(voxel_size, 4),
            "resolution": repair_effort.get("voxel_resolution", "medium"),
            "dimensions": dimensions,
            "offset": [0.0, round(add_height * 0.5, 3), 0.0],
            "color_hint": "match_prop_material",
            "opacity": 0.96,
            "blend_mode": "physical_overlay",
            "bake_after_seconds": repair_effort.get("bake_after_seconds", 0.75),
            "bake_to_mesh": True,
            "mesh_policy": "temporary_prop_overlay",
            "first_response": repair_effort.get("first_response", "live_coarse_overlay"),
            "latency_budget_ms": repair_effort.get("latency_budget_ms", 80),
            "requires_remeasure_before_bake": repair_effort.get("requires_remeasure_before_bake", False),
            "subtractive_cutouts": clearance_zones,
        },
    }


def _build_small_seat_support(
    performer_id: str,
    contact_object: ContactObject,
    metrics: Any,
    body_scale: float,
    seat_height: float,
    ideal_seat_height: float,
    interaction: str,
    repair_effort: Dict[str, Any],
) -> Dict[str, Any]:
    foot_support_height = max(0.04, seat_height - ideal_seat_height)
    support_width = max(metrics.hip_width * 2.2, 0.28 * max(body_scale, 0.6))
    support_depth = max(metrics.leg * 0.22, 0.16)
    voxel_size = _adaptive_voxel_size(contact_object, repair_effort)
    dimensions, voxel_budget = _voxel_dimensions(
        [support_width, foot_support_height, support_depth],
        voxel_size,
        contact_object,
        repair_effort,
    )
    extension_id = f"fit_ext_{performer_id}_{contact_object.object_id}_{int(time.time() * 1000)}"
    return {
        "extension_id": extension_id,
        "performer_id": performer_id,
        "object_id": contact_object.object_id,
        "object_type": contact_object.object_type,
        "interaction": interaction,
        "kind": "foot_support",
        "reason": "avatar_smaller_than_standard_prop",
        "proportional_scale": round(body_scale, 3),
        "preserve_original_asset": True,
        "retire_when_interaction_ends": True,
        "render_mode": "live_voxel_overlay_then_mesh_bake",
        "repair_effort": dict(repair_effort),
        "quality_assurance": _quality_assurance("foot_support", repair_effort, [], voxel_budget),
        "voxel_budget": voxel_budget,
        "dimensions_meters": {
            "seat_height": round(seat_height, 3),
            "ideal_seat_height": round(ideal_seat_height, 3),
            "foot_support_height": round(foot_support_height, 3),
            "support_width": round(support_width, 3),
            "support_depth": round(support_depth, 3),
        },
        "voxel_patch": {
            "patch_id": f"{extension_id}_voxels",
            "anchor_object_id": contact_object.object_id,
            "anchor": "front_floor_contact",
            "kind": "support",
            "reason": "adaptive_foot_support",
            "voxel_size": round(voxel_size, 4),
            "resolution": repair_effort.get("voxel_resolution", "medium"),
            "dimensions": dimensions,
            "offset": [0.0, round(foot_support_height * 0.5, 3), round(support_depth * 0.55, 3)],
            "color_hint": "match_prop_material",
            "opacity": 0.96,
            "blend_mode": "physical_overlay",
            "bake_after_seconds": repair_effort.get("bake_after_seconds", 0.75),
            "bake_to_mesh": True,
            "mesh_policy": "temporary_prop_overlay",
            "first_response": repair_effort.get("first_response", "live_coarse_overlay"),
            "latency_budget_ms": repair_effort.get("latency_budget_ms", 80),
            "requires_remeasure_before_bake": repair_effort.get("requires_remeasure_before_bake", False),
        },
    }


def _build_surface_extension(
    performer_id: str,
    contact_object: ContactObject,
    metrics: Any,
    body_scale: float,
    surface_height: float,
    ideal_surface_height: float,
    interaction: str,
    repair_effort: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not _metadata_bool(contact_object, "adaptive_prop_extension", True):
        return None
    lift_height = ideal_surface_height - surface_height
    if lift_height < max(0.06, ideal_surface_height * 0.08):
        return None

    surface_width = _metadata_float(contact_object, "surface_width", _metadata_float(contact_object, "width", 0.8))
    surface_depth = _metadata_float(contact_object, "surface_depth", _metadata_float(contact_object, "depth", 0.55))
    target_width = max(surface_width, metrics.hip_width * 3.0, 0.55 * body_scale)
    target_depth = max(surface_depth, metrics.arm * 0.85, 0.45 * body_scale)
    voxel_size = _adaptive_voxel_size(contact_object, repair_effort)
    dimensions, voxel_budget = _voxel_dimensions(
        [target_width, lift_height, target_depth],
        voxel_size,
        contact_object,
        repair_effort,
    )
    extension_id = f"fit_ext_{performer_id}_{contact_object.object_id}_{int(time.time() * 1000)}"
    return {
        "extension_id": extension_id,
        "performer_id": performer_id,
        "object_id": contact_object.object_id,
        "object_type": contact_object.object_type,
        "interaction": interaction,
        "kind": "surface_lift",
        "reason": "avatar_larger_than_standard_surface",
        "proportional_scale": round(body_scale, 3),
        "preserve_original_asset": True,
        "retire_when_interaction_ends": True,
        "render_mode": "live_voxel_overlay_then_mesh_bake",
        "repair_effort": dict(repair_effort),
        "quality_assurance": _quality_assurance("surface_lift", repair_effort, [], voxel_budget),
        "voxel_budget": voxel_budget,
        "dimensions_meters": {
            "surface_height": round(surface_height, 3),
            "target_surface_height": round(ideal_surface_height, 3),
            "lift_height": round(lift_height, 3),
            "surface_width": round(surface_width, 3),
            "target_width": round(target_width, 3),
            "surface_depth": round(surface_depth, 3),
            "target_depth": round(target_depth, 3),
        },
        "voxel_patch": {
            "patch_id": f"{extension_id}_voxels",
            "anchor_object_id": contact_object.object_id,
            "anchor": "surface_top",
            "kind": "extend",
            "reason": "adaptive_surface_lift",
            "voxel_size": round(voxel_size, 4),
            "resolution": repair_effort.get("voxel_resolution", "medium"),
            "dimensions": dimensions,
            "offset": [0.0, round(lift_height * 0.5, 3), 0.0],
            "color_hint": "match_prop_material",
            "opacity": 0.96,
            "blend_mode": "physical_overlay",
            "bake_after_seconds": repair_effort.get("bake_after_seconds", 0.75),
            "bake_to_mesh": True,
            "mesh_policy": "temporary_prop_overlay",
            "first_response": repair_effort.get("first_response", "live_coarse_overlay"),
            "latency_budget_ms": repair_effort.get("latency_budget_ms", 80),
            "requires_remeasure_before_bake": repair_effort.get("requires_remeasure_before_bake", False),
        },
    }


def _anatomy_clearance_zones(
    contact_object: ContactObject,
    skeleton: Any,
    metrics: Any,
    body_scale: float,
    interaction_context: str,
) -> List[Dict[str, Any]]:
    zones: List[Dict[str, Any]] = []
    profile = _avatar_profile(contact_object, skeleton, interaction_context)

    if interaction_context == "seat":
        needs_tail = _metadata_bool(contact_object, "tail_clearance", "tail" in profile["traits"])
        if needs_tail:
            zones.append(_tail_clearance_zone(contact_object, metrics, body_scale))
        for zone in _profile_clearance_zones(profile, contact_object, body_scale, include_seat=True, include_handheld=False):
            zones.append(zone)
    elif interaction_context == "handheld":
        for zone in _profile_clearance_zones(profile, contact_object, body_scale, include_seat=False, include_handheld=True):
            zones.append(zone)

    return _dedupe_zones(zones)


def _tail_clearance_zone(contact_object: ContactObject, metrics: Any, body_scale: float) -> Dict[str, Any]:
    tail_width = max(
        _metadata_float(contact_object, "tail_width", 0.0),
        _metadata_float(contact_object, "tail_diameter", 0.0),
        metrics.hip_width * 0.45,
        0.14 * body_scale,
    )
    tail_depth = max(
        _metadata_float(contact_object, "tail_depth", 0.0),
        tail_width * 1.35,
        0.18 * body_scale,
    )
    tail_height = max(
        _metadata_float(contact_object, "tail_height", 0.0),
        tail_width,
        0.16 * body_scale,
    )
    return {
        "kind": "tail_clearance",
        "operation": "subtract",
        "anchor": "rear_seat_center",
        "shape": "rounded_slot",
        "dimensions_meters": {
            "width": round(tail_width, 3),
            "height": round(tail_height, 3),
            "depth": round(tail_depth, 3),
        },
        "offset": [0.0, round(tail_height * 0.5, 3), round(-tail_depth * 0.55, 3)],
        "reason": "preserve_tail_anatomy_clearance",
    }


def _avatar_profile(contact_object: ContactObject, skeleton: Any, interaction_context: str) -> Dict[str, Any]:
    raw = contact_object.metadata.get("avatar_profile", {})
    profile = dict(raw) if isinstance(raw, dict) else {}
    traits = set(str(item).lower() for item in profile.get("traits", []) if str(item).strip())
    traits.update(_skeleton_traits(skeleton))
    silhouette = dict(profile.get("silhouette", {})) if isinstance(profile.get("silhouette", {}), dict) else {}
    pose_profiles = profile.get("pose_profiles", {})
    pose_profile = {}
    if isinstance(pose_profiles, dict):
        pose_key = "seated" if interaction_context == "seat" else interaction_context
        raw_pose = pose_profiles.get(pose_key, {})
        pose_profile = dict(raw_pose) if isinstance(raw_pose, dict) else {}
    pose_silhouette = pose_profile.get("silhouette", {})
    if isinstance(pose_silhouette, dict):
        silhouette.update(pose_silhouette)
    pose_traits = pose_profile.get("traits", [])
    if isinstance(pose_traits, (list, tuple, set)):
        traits.update(str(item).lower() for item in pose_traits if str(item).strip())
    return {
        "traits": traits,
        "silhouette": silhouette,
        "pose": "seated" if interaction_context == "seat" else interaction_context,
    }


def _skeleton_traits(skeleton: Any) -> set[str]:
    joints = getattr(skeleton, "joints", {}) or {}
    bind_pose = getattr(skeleton, "bind_pose", {}) or {}
    names = list(joints.keys()) if isinstance(joints, dict) else []
    if isinstance(bind_pose, dict):
        names.extend(bind_pose.keys())
    traits = getattr(skeleton, "traits", None)
    if isinstance(traits, (list, tuple, set)):
        names.extend(str(item) for item in traits)
    elif isinstance(traits, str):
        names.append(traits)
    out = set()
    for name in names:
        text = str(name).lower()
        for token in ("tail", "horn", "wing", "hoof", "claw", "extra_arm", "extra_hand", "broad_shoulder", "hump"):
            if token in text:
                out.add(token)
        if "extra" in text and ("arm" in text or "hand" in text):
            out.add("extra_hand")
    return out


def _profile_clearance_zones(
    profile: Dict[str, Any],
    contact_object: ContactObject,
    body_scale: float,
    include_seat: bool,
    include_handheld: bool,
) -> List[Dict[str, Any]]:
    traits = profile["traits"]
    silhouette = profile["silhouette"]
    zones: List[Dict[str, Any]] = []
    shoulder_width = _profile_float(silhouette, "shoulder_width", 0.0)
    horn_span = _profile_float(silhouette, "horn_span", 0.0)
    wing_span = _profile_float(silhouette, "wing_span", 0.0)
    hand_width = _profile_float(silhouette, "hand_width", 0.0)
    grip_diameter = _profile_float(silhouette, "grip_diameter", 0.0)

    if include_seat:
        if "wing" in traits or wing_span > 1.2 * body_scale:
            width = max(wing_span * 0.22, 0.28 * body_scale)
            zones.extend([
                _side_clearance("wing_clearance_l", "left_back_side", width, 0.34 * body_scale, "preserve_wing_fold_clearance"),
                _side_clearance("wing_clearance_r", "right_back_side", width, 0.34 * body_scale, "preserve_wing_fold_clearance"),
            ])
        if "broad_shoulder" in traits or shoulder_width > 0.65 * body_scale:
            zones.append({
                "kind": "shoulder_clearance",
                "operation": "extend",
                "anchor": "seat_back_width",
                "shape": "wide_back_relief",
                "dimensions_meters": {
                    "width": round(max(shoulder_width, 0.7 * body_scale), 3),
                    "height": round(0.45 * body_scale, 3),
                    "depth": round(0.12 * body_scale, 3),
                },
                "offset": [0.0, round(0.35 * body_scale, 3), round(-0.18 * body_scale, 3)],
                "reason": "preserve_upper_body_silhouette_clearance",
            })

    if include_handheld:
        if {"hoof", "claw", "extra_hand"} & traits or hand_width > 0.12 * body_scale or grip_diameter > 0.08 * body_scale:
            width = max(hand_width, grip_diameter, _metadata_float(contact_object, "grip_clearance_width", 0.12 * body_scale))
            zones.append({
                "kind": "grip_clearance",
                "operation": "extend",
                "anchor": "primary_grip",
                "shape": "adaptive_grip_sleeve",
                "dimensions_meters": {
                    "width": round(width, 3),
                    "height": round(max(width * 1.15, 0.12 * body_scale), 3),
                    "depth": round(max(width * 0.85, 0.08 * body_scale), 3),
                },
                "offset": [0.0, 0.0, 0.0],
                "reason": "preserve_nonstandard_hand_or_grip_clearance",
            })
        if "horn" in traits or horn_span > 0.55 * body_scale:
            zones.append({
                "kind": "face_approach_clearance",
                "operation": "offset",
                "anchor": "mouth_or_face_approach",
                "shape": "forward_offset",
                "dimensions_meters": {
                    "width": round(max(horn_span, 0.45 * body_scale), 3),
                    "height": round(0.2 * body_scale, 3),
                    "depth": round(0.12 * body_scale, 3),
                },
                "offset": [0.0, 0.0, round(0.12 * body_scale, 3)],
                "reason": "preserve_horn_or_face_silhouette_clearance",
            })
    return zones


def _side_clearance(kind: str, anchor: str, width: float, height: float, reason: str) -> Dict[str, Any]:
    side = -1.0 if anchor.startswith("left") else 1.0
    return {
        "kind": kind,
        "operation": "subtract",
        "anchor": anchor,
        "shape": "side_relief",
        "dimensions_meters": {
            "width": round(width, 3),
            "height": round(height, 3),
            "depth": round(width * 0.8, 3),
        },
        "offset": [round(side * width * 0.5, 3), round(height * 0.5, 3), round(-width * 0.35, 3)],
        "reason": reason,
    }


def _profile_float(profile: Dict[str, Any], key: str, default: float) -> float:
    return _finite_float(profile.get(key, default), default)


def _dedupe_zones(zones: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for zone in zones:
        key = (zone.get("kind"), zone.get("anchor"), zone.get("operation"))
        if key in seen:
            continue
        seen.add(key)
        out.append(zone)
    return out


def _voxel_dimensions(
    size_meters: List[float],
    voxel_size: float,
    contact_object: ContactObject,
    repair_effort: Optional[Dict[str, Any]] = None,
) -> tuple[List[int], Dict[str, Any]]:
    repair_effort = repair_effort or REPAIR_EFFORT_POLICIES[REPAIR_EFFORT_LIVE]
    multiplier = _clamp(_finite_float(repair_effort.get("budget_multiplier"), 1.0), 1.0, 4.0)
    explicit_axis = "max_adaptive_voxel_axis" in contact_object.metadata
    explicit_count = "max_adaptive_voxel_count" in contact_object.metadata
    base_axis = _metadata_float(contact_object, "max_adaptive_voxel_axis", MAX_ADAPTIVE_VOXEL_AXIS)
    base_count = _metadata_float(contact_object, "max_adaptive_voxel_count", MAX_ADAPTIVE_VOXEL_COUNT)
    max_axis = max(1, int(base_axis if explicit_axis else base_axis * min(multiplier, 1.5)))
    max_count = max(1, int(base_count if explicit_count else base_count * multiplier))
    raw = [max(1, int(math.ceil(max(_finite_float(size, 0.0), voxel_size) / voxel_size))) for size in size_meters[:3]]
    dimensions = [min(axis, max_axis) for axis in raw]
    while dimensions[0] * dimensions[1] * dimensions[2] > max_count:
        largest_index = max(range(3), key=lambda index: dimensions[index])
        if dimensions[largest_index] <= 1:
            break
        dimensions[largest_index] -= 1
    capped = dimensions != raw
    return dimensions, {
        "raw_dimensions": raw,
        "dimensions_capped": capped,
        "max_axis": max_axis,
        "max_count": max_count,
        "estimated_voxel_count": dimensions[0] * dimensions[1] * dimensions[2],
        "repair_effort": repair_effort.get("effort", REPAIR_EFFORT_LIVE),
    }


def _quality_assurance(
    kind: str,
    repair_effort: Dict[str, Any],
    clearance_zones: List[Dict[str, Any]],
    voxel_budget: Dict[str, Any],
) -> Dict[str, Any]:
    checks = list(repair_effort.get("quality_checks", []))
    if clearance_zones and "anatomy_clearance" not in checks:
        checks.append("anatomy_clearance")
    return {
        "kind": kind,
        "effort": repair_effort.get("effort", REPAIR_EFFORT_LIVE),
        "live_first": True,
        "passes": int(_finite_float(repair_effort.get("quality_passes"), 1)),
        "checks": _dedupe([str(check) for check in checks]),
        "requires_remeasure_before_bake": bool(repair_effort.get("requires_remeasure_before_bake")),
        "voxel_budget_capped": bool(voxel_budget.get("dimensions_capped")),
        "fallback": "keep_live_overlay_and_skip_bake_if_quality_check_fails",
    }


def _risk(score: float) -> str:
    if score >= 0.85:
        return "good"
    if score >= 0.68:
        return "usable"
    if score >= 0.45:
        return "strained"
    return "likely_bad"


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
