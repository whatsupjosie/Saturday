import json
from pathlib import Path

from modules.voxel_set_contract import (
    VoxelSetValidationError,
    load_voxel_set,
    normalize_voxel_set,
    save_voxel_set,
    theater_platform_set,
)


def test_theater_platform_contract_round_trips(tmp_path: Path):
    voxel_set = theater_platform_set()
    assert voxel_set["asset_id"] == "small_theater_platform_two_walls_doorway"
    assert voxel_set["dimensions"] == {"x": 7, "y": 3, "z": 5}
    assert voxel_set["safety"]["ai_authority"] == "off"
    assert voxel_set["safety"]["sandbox_only"] is True
    assert voxel_set["safety"]["approved_by_user"] is False

    path = save_voxel_set(tmp_path, voxel_set)
    reloaded = load_voxel_set(path)
    assert reloaded["blocks"] == voxel_set["blocks"]
    assert reloaded["materials"] == voxel_set["materials"]
    assert reloaded["source_prompt"] == "small theater platform with two side walls and a doorway"


def test_voxel_contract_rejects_duplicate_coordinates():
    voxel_set = theater_platform_set("duplicate_test")
    voxel_set["blocks"].append(dict(voxel_set["blocks"][0]))
    try:
        normalize_voxel_set(voxel_set)
    except VoxelSetValidationError as exc:
        assert "duplicate block coordinate" in str(exc)
    else:
        raise AssertionError("duplicate coordinates were accepted")


def test_voxel_contract_is_json_serializable():
    voxel_set = theater_platform_set("json_test")
    encoded = json.dumps(voxel_set)
    decoded = normalize_voxel_set(json.loads(encoded))
    assert decoded["asset_id"] == "json_test"
