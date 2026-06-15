# PubCast AI — pubworld.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
modules/pubworld.py
-------------------
PubWorld scene management — list, create, get scenes.
Scenes are JSON files stored under data/pubworld/scenes/.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .persistence import write_json

logger = logging.getLogger(__name__)


class Scene(BaseModel):
    scene_id:    str
    name:        str
    description: str  = ""
    created_at:  float = Field(default_factory=time.time)
    updated_at:  float = Field(default_factory=time.time)
    metadata:    Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow")


def _scenes_dir(data_dir: Path) -> Path:
    d = Path(data_dir) / "pubworld" / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_scenes(data_dir: Path) -> List[Scene]:
    scenes = []
    for f in sorted(_scenes_dir(data_dir).glob("*.json")):
        try:
            scenes.append(Scene(**json.loads(f.read_text(encoding="utf-8"))))
        except Exception as exc:
            logger.warning("pubworld: skipping corrupt scene file %s: %s", f.name, exc)
    return scenes


def create_scene(data_dir: Path, name: str, description: str = "") -> Scene:
    scene = Scene(scene_id=str(uuid.uuid4()), name=name, description=description)
    path = _scenes_dir(data_dir) / f"{scene.scene_id}.json"
    write_json(path, json.loads(scene.model_dump_json()))
    return scene


def get_scene(data_dir: Path, scene_id: str) -> Optional[Scene]:
    path = _scenes_dir(data_dir) / f"{scene_id}.json"
    if not path.exists():
        return None
    try:
        return Scene(**json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:
        logger.warning("pubworld: corrupt scene %s: %s", scene_id, exc)
        return None


__all__ = ["Scene", "list_scenes", "create_scene", "get_scene"]
