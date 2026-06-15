"""Standalone adapter from Visual Patch Kit proposals to PubWorld voxel blocks.

This file is intentionally not wired into the runtime yet. It is a translator
for the later integration pass between:

- `avatar:visual_patch` / `avatar:visual_patch_bake_requested`
- `object:adaptive_prop_extension`
- PubWorld `/api/pubworld/props` block payloads
- canonical voxel-set style payloads

The adapter is standard-library only and performs no network, file, renderer, or
event-bus work. Callers choose when and where to submit the returned payload.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


DEFAULT_SCENE_ID = "scene_default"
DEFAULT_COLOR = "#888888"
MAX_ADAPTER_BLOCKS = 50000


def visual_patch_to_pubworld_prop(
    patch: Mapping[str, Any],
    scene_id: str = DEFAULT_SCENE_ID,
    label: Optional[str] = None,
    max_blocks: int = MAX_ADAPTER_BLOCKS,
) -> Dict[str, Any]:
    """Translate an `avatar:visual_patch` spec into a PubWorld prop payload."""

    patch_id = _text(patch.get("patch_id"), f"visual_patch_{int(time.time() * 1000)}")
    dimensions = _dimensions(patch.get("dimensions"), [3, 3, 1])
    color = _color_for_hint(_text(patch.get("color_hint"), "match_outfit"))
    blocks = _blocks_from_dimensions(
        dimensions=dimensions,
        color=color,
        kind=_text(patch.get("kind"), "cover"),
        max_blocks=max_blocks,
        hollow=True,
    )
    return {
        "scene_id": scene_id,
        "label": label or f"Visual Patch {patch_id}",
        "description": _text(patch.get("reason"), "temporary visual patch"),
        "blocks": blocks,
        "variants": [
            {
                "variant_id": "live_overlay",
                "patch_id": patch_id,
                "performer_id": patch.get("performer_id"),
                "anchor_joint": patch.get("anchor_joint"),
                "offset": _float_vector(patch.get("offset"), [0.0, 0.0, 0.0]),
                "voxel_size": _finite_float(patch.get("voxel_size"), 0.035),
                "opacity": _finite_float(patch.get("opacity"), 0.92),
                "blend_mode": _text(patch.get("blend_mode"), "soft_overlay"),
                "bake_after_seconds": _finite_float(patch.get("bake_after_seconds"), 0.75),
                "bake_to_mesh": bool(patch.get("bake_to_mesh", True)),
                "temporary": True,
            }
        ],
    }


def adaptive_extension_to_pubworld_prop(
    extension: Mapping[str, Any],
    scene_id: str = DEFAULT_SCENE_ID,
    label: Optional[str] = None,
    max_blocks: int = MAX_ADAPTER_BLOCKS,
) -> Dict[str, Any]:
    """Translate an adaptive prop extension proposal into a PubWorld prop payload."""

    extension_id = _text(extension.get("extension_id"), f"adaptive_ext_{int(time.time() * 1000)}")
    voxel_patch = _mapping(extension.get("voxel_patch"))
    dimensions = _dimensions(voxel_patch.get("dimensions"), [1, 1, 1])
    clearance_zones = _list_of_mappings(extension.get("clearance_zones"))
    subtractive_cutouts = _list_of_mappings(voxel_patch.get("subtractive_cutouts")) or clearance_zones
    color = _color_for_hint(_text(voxel_patch.get("color_hint"), "match_prop_material"))
    blocks = _blocks_from_dimensions(
        dimensions=dimensions,
        color=color,
        kind=_text(extension.get("kind"), _text(voxel_patch.get("kind"), "extend")),
        subtractive_cutouts=subtractive_cutouts,
        max_blocks=max_blocks,
        hollow=True,
    )
    return {
        "scene_id": scene_id,
        "label": label or f"Adaptive Prop {extension_id}",
        "description": _text(extension.get("reason"), "temporary adaptive prop extension"),
        "blocks": blocks,
        "variants": [
            {
                "variant_id": "adaptive_overlay",
                "extension_id": extension_id,
                "performer_id": extension.get("performer_id"),
                "object_id": extension.get("object_id"),
                "object_type": extension.get("object_type"),
                "interaction": extension.get("interaction"),
                "anchor": voxel_patch.get("anchor"),
                "offset": _float_vector(voxel_patch.get("offset"), [0.0, 0.0, 0.0]),
                "voxel_size": _finite_float(voxel_patch.get("voxel_size"), 0.035),
                "repair_effort": _mapping(extension.get("repair_effort")),
                "quality_assurance": _mapping(extension.get("quality_assurance")),
                "approval": _mapping(extension.get("approval")),
                "presence": _mapping(extension.get("presence")),
                "persistence": _mapping(extension.get("persistence")),
                "clearance_zones": clearance_zones,
                "subtractive_cutouts": subtractive_cutouts,
                "temporary": bool(extension.get("retire_when_interaction_ends", True)),
            }
        ],
    }


def adaptive_extension_to_voxel_set(
    extension: Mapping[str, Any],
    asset_id: Optional[str] = None,
    name: Optional[str] = None,
    max_blocks: int = MAX_ADAPTER_BLOCKS,
) -> Dict[str, Any]:
    """Build a canonical voxel-set-like payload from an adaptive extension."""

    prop = adaptive_extension_to_pubworld_prop(extension, max_blocks=max_blocks)
    extension_id = _text(extension.get("extension_id"), f"adaptive_ext_{int(time.time() * 1000)}")
    blocks = [
        {
            "x": block["x"],
            "y": block["y"],
            "z": block["z"],
            "material": "adaptive_patch",
            "color": block.get("color") or DEFAULT_COLOR,
            "metadata": {"kind": block.get("kind", "cube")},
        }
        for block in prop["blocks"]
    ]
    return {
        "asset_id": asset_id or _safe_id(extension_id),
        "name": name or prop["label"],
        "version": 1,
        "units": "voxel",
        "dimensions": _dimensions_xyz(prop["blocks"]),
        "origin": {"x": 0, "y": 0, "z": 0},
        "blocks": blocks,
        "materials": {
            "adaptive_patch": {
                "kind": "temporary_overlay",
                "label": "Adaptive Visual Patch",
            }
        },
        "created_by": "builder",
        "source_prompt": prop["description"],
        "safety": {
            "ai_authority": "advisory",
            "approved_by_user": _approval_is_active(extension),
            "sandbox_only": True,
        },
    }


def _blocks_from_dimensions(
    dimensions: List[int],
    color: str,
    kind: str,
    max_blocks: int,
    hollow: bool,
    subtractive_cutouts: Optional[List[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    x_len, y_len, z_len = dimensions[:3]
    blocks: List[Dict[str, Any]] = []
    for x, y, z in _iter_grid(x_len, y_len, z_len):
        if hollow and not _is_shell(x, y, z, x_len, y_len, z_len):
            continue
        if _inside_any_cutout(x, y, z, x_len, y_len, z_len, subtractive_cutouts or []):
            continue
        blocks.append({"x": x, "y": y, "z": z, "kind": kind or "cube", "color": color})
        if len(blocks) >= max(1, int(max_blocks)):
            break
    return blocks


def _iter_grid(x_len: int, y_len: int, z_len: int) -> Iterable[Tuple[int, int, int]]:
    for x in range(max(1, x_len)):
        for y in range(max(1, y_len)):
            for z in range(max(1, z_len)):
                yield x, y, z


def _is_shell(x: int, y: int, z: int, x_len: int, y_len: int, z_len: int) -> bool:
    return x in {0, x_len - 1} or y in {0, y_len - 1} or z in {0, z_len - 1}


def _inside_any_cutout(
    x: int,
    y: int,
    z: int,
    x_len: int,
    y_len: int,
    z_len: int,
    cutouts: List[Mapping[str, Any]],
) -> bool:
    for cutout in cutouts:
        if str(cutout.get("operation") or "").lower() != "subtract":
            continue
        kind = str(cutout.get("kind") or cutout.get("shape") or "").lower()
        if "tail" in kind or "slot" in kind:
            slot_width = max(1, int(math.ceil(x_len * 0.28)))
            slot_height = max(1, int(math.ceil(y_len * 0.55)))
            slot_depth = max(1, int(math.ceil(z_len * 0.34)))
            x_mid = x_len // 2
            in_x = abs(x - x_mid) <= max(1, slot_width // 2)
            in_y = y < slot_height
            in_z = z < slot_depth
            if in_x and in_y and in_z:
                return True
    return False


def _dimensions(value: Any, default: List[int]) -> List[int]:
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        out = []
        for item in value[:3]:
            out.append(max(1, int(_finite_float(item, 1))))
        return out
    return list(default)


def _dimensions_xyz(blocks: List[Mapping[str, Any]]) -> Dict[str, int]:
    if not blocks:
        return {"x": 0, "y": 0, "z": 0}
    return {
        "x": max(int(block.get("x", 0)) for block in blocks) + 1,
        "y": max(int(block.get("y", 0)) for block in blocks) + 1,
        "z": max(int(block.get("z", 0)) for block in blocks) + 1,
    }


def _approval_is_active(extension: Mapping[str, Any]) -> bool:
    approval = _mapping(extension.get("approval"))
    return str(approval.get("state") or "").lower() in {"approved", "auto_approved"}


def _color_for_hint(hint: str) -> str:
    text = hint.lower()
    if "outfit" in text or "costume" in text:
        return "#4a5568"
    if "skin" in text or "makeup" in text:
        return "#c58f72"
    if "prop" in text or "material" in text:
        return "#8a7f72"
    return DEFAULT_COLOR


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _float_vector(value: Any, default: List[float]) -> List[float]:
    if not isinstance(value, (list, tuple)):
        return list(default)
    out = [_finite_float(item, 0.0) for item in value[: len(default)]]
    while len(out) < len(default):
        out.append(default[len(out)])
    return out


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text or default


def _safe_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe[:96] or f"visual_patch_{int(time.time())}"


__all__ = [
    "adaptive_extension_to_pubworld_prop",
    "adaptive_extension_to_voxel_set",
    "visual_patch_to_pubworld_prop",
]
