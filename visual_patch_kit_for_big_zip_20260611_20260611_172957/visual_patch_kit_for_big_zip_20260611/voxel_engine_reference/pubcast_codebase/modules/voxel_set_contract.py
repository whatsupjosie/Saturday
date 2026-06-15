# Copyright (c) 2024-2026 Rear View Foresight LLC
"""Canonical PubCast voxel set contract.

This module is intentionally standard-library only. It gives the builder,
PubWorld, tests, and recreate-bundle code one boring JSON-compatible shape for
voxel assets without pulling in the FastAPI/Pydantic runtime.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple


AI_AUTHORITY_MODES = {
    "off",
    "advisory",
    "planning",
    "assigned_role",
    "supervised_operator",
    "recovery",
    "freeplay_sandbox",
}


class VoxelSetValidationError(ValueError):
    """Raised when a voxel set violates the stable contract."""


def _as_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise VoxelSetValidationError(f"{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise VoxelSetValidationError(f"{field} must be an integer") from exc


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise VoxelSetValidationError(f"{key} must be a non-empty string")
    return value.strip()


def _dict_xyz(payload: Mapping[str, Any], key: str) -> Dict[str, int]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise VoxelSetValidationError(f"{key} must be an object")
    return {
        "x": _as_int(value.get("x"), f"{key}.x"),
        "y": _as_int(value.get("y"), f"{key}.y"),
        "z": _as_int(value.get("z"), f"{key}.z"),
    }


def dimensions_for_blocks(blocks: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    coords = [(_as_int(b.get("x"), "block.x"), _as_int(b.get("y"), "block.y"), _as_int(b.get("z"), "block.z")) for b in blocks]
    if not coords:
        return {"x": 0, "y": 0, "z": 0}
    xs, ys, zs = zip(*coords)
    return {
        "x": max(xs) - min(xs) + 1,
        "y": max(ys) - min(ys) + 1,
        "z": max(zs) - min(zs) + 1,
    }


def normalize_voxel_set(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a validated, JSON-compatible voxel set payload."""

    asset_id = _require_string(payload, "asset_id")
    name = _require_string(payload, "name")
    units = str(payload.get("units") or "voxel").strip()
    if units != "voxel":
        raise VoxelSetValidationError("units must be 'voxel'")
    version = _as_int(payload.get("version", 1), "version")
    if version != 1:
        raise VoxelSetValidationError("version must be 1")

    origin = _dict_xyz(payload, "origin")
    blocks_raw = payload.get("blocks")
    if not isinstance(blocks_raw, list):
        raise VoxelSetValidationError("blocks must be a list")

    materials = payload.get("materials") or {}
    if not isinstance(materials, Mapping):
        raise VoxelSetValidationError("materials must be an object")

    blocks: List[Dict[str, Any]] = []
    seen: set[Tuple[int, int, int]] = set()
    for index, block in enumerate(blocks_raw):
        if not isinstance(block, Mapping):
            raise VoxelSetValidationError(f"blocks[{index}] must be an object")
        x = _as_int(block.get("x"), f"blocks[{index}].x")
        y = _as_int(block.get("y"), f"blocks[{index}].y")
        z = _as_int(block.get("z"), f"blocks[{index}].z")
        coord = (x, y, z)
        if coord in seen:
            raise VoxelSetValidationError(f"duplicate block coordinate {coord}")
        seen.add(coord)
        material = str(block.get("material") or "default").strip()
        if not material:
            raise VoxelSetValidationError(f"blocks[{index}].material must be non-empty")
        if material != "default" and material not in materials:
            raise VoxelSetValidationError(f"blocks[{index}].material '{material}' is not defined")
        color = str(block.get("color") or "#888888").strip()
        metadata = block.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise VoxelSetValidationError(f"blocks[{index}].metadata must be an object")
        blocks.append({"x": x, "y": y, "z": z, "material": material, "color": color, "metadata": dict(metadata)})

    safety = payload.get("safety") or {}
    if not isinstance(safety, Mapping):
        raise VoxelSetValidationError("safety must be an object")
    authority = str(safety.get("ai_authority") or "off").strip()
    if authority not in AI_AUTHORITY_MODES:
        raise VoxelSetValidationError(f"safety.ai_authority must be one of {sorted(AI_AUTHORITY_MODES)}")
    approved_by_user = bool(safety.get("approved_by_user", False))
    sandbox_only = bool(safety.get("sandbox_only", True))

    created_by = str(payload.get("created_by") or "builder").strip()
    if created_by not in {"builder", "prompt", "import"}:
        raise VoxelSetValidationError("created_by must be builder, prompt, or import")
    source_prompt = payload.get("source_prompt")
    if source_prompt is not None and not isinstance(source_prompt, str):
        raise VoxelSetValidationError("source_prompt must be a string when provided")

    draft = bool(payload.get("draft", False))
    if not blocks and not draft:
        raise VoxelSetValidationError("empty sets must be marked draft=true")

    dims = _dict_xyz(payload, "dimensions") if isinstance(payload.get("dimensions"), Mapping) else dimensions_for_blocks(blocks)
    required_dims = dimensions_for_blocks(blocks)
    if any(dims[axis] < required_dims[axis] for axis in ("x", "y", "z")):
        raise VoxelSetValidationError("dimensions must contain all blocks")

    normalized = {
        "asset_id": asset_id,
        "name": name,
        "version": version,
        "units": units,
        "dimensions": dims,
        "origin": origin,
        "blocks": blocks,
        "materials": dict(materials),
        "created_by": created_by,
        "source_prompt": source_prompt,
        "safety": {
            "ai_authority": authority,
            "approved_by_user": approved_by_user,
            "sandbox_only": sandbox_only,
        },
    }
    if draft:
        normalized["draft"] = True
    return normalized


def save_voxel_set(base_dir: Path, payload: Mapping[str, Any]) -> Path:
    """Validate and save a voxel set under a workspace-local voxel_sets folder."""

    normalized = normalize_voxel_set(payload)
    out_dir = Path(base_dir) / "pubworld" / "voxel_sets"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in normalized["asset_id"])[:96]
    if not safe_name:
        raise VoxelSetValidationError("asset_id produced an empty safe filename")
    path = out_dir / f"{safe_name}.json"
    if path.exists():
        raise FileExistsError(f"voxel set already exists: {path}")
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_voxel_set(path: Path) -> Dict[str, Any]:
    return normalize_voxel_set(json.loads(Path(path).read_text(encoding="utf-8")))


def theater_platform_set(asset_id: str = "small_theater_platform_two_walls_doorway") -> Dict[str, Any]:
    """Build the deterministic proof set requested for the voxel loop."""

    blocks: List[Dict[str, Any]] = []
    for x in range(7):
        for z in range(5):
            blocks.append({"x": x, "y": 0, "z": z, "material": "stage_wood", "color": "#8b5a2b", "metadata": {"part": "platform"}})
    for z in range(5):
        blocks.append({"x": 0, "y": 1, "z": z, "material": "wall_red", "color": "#7a1f2a", "metadata": {"part": "left_wall"}})
        blocks.append({"x": 6, "y": 1, "z": z, "material": "wall_red", "color": "#7a1f2a", "metadata": {"part": "right_wall"}})
    for x in [2, 4]:
        blocks.append({"x": x, "y": 1, "z": 4, "material": "wall_red", "color": "#7a1f2a", "metadata": {"part": "doorway_side"}})
    blocks.append({"x": 3, "y": 2, "z": 4, "material": "brass_trim", "color": "#d8af5b", "metadata": {"part": "doorway_header"}})
    payload = {
        "asset_id": asset_id,
        "name": "Small Theater Platform With Two Side Walls And A Doorway",
        "version": 1,
        "units": "voxel",
        "dimensions": {"x": 7, "y": 3, "z": 5},
        "origin": {"x": 0, "y": 0, "z": 0},
        "blocks": blocks,
        "materials": {
            "stage_wood": {"kind": "solid", "label": "Stage wood"},
            "wall_red": {"kind": "solid", "label": "Red theater wall"},
            "brass_trim": {"kind": "solid", "label": "Brass trim"},
        },
        "created_by": "builder",
        "source_prompt": "small theater platform with two side walls and a doorway",
        "safety": {
            "ai_authority": "off",
            "approved_by_user": False,
            "sandbox_only": True,
        },
    }
    return normalize_voxel_set(deepcopy(payload))


__all__ = [
    "AI_AUTHORITY_MODES",
    "VoxelSetValidationError",
    "dimensions_for_blocks",
    "load_voxel_set",
    "normalize_voxel_set",
    "save_voxel_set",
    "theater_platform_set",
]
