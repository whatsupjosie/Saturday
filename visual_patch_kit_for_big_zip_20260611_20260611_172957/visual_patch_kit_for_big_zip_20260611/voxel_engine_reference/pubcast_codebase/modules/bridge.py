# PubCast AI — bridge.py
# Copyright © 2024–2026 Josie Curtsey Cobbley (Joshua Cobbley)
# Rearview Foresight LLC — All Rights Reserved
# Feic Mo Chroí — See My Heart
"""
modules/bridge.py — Twin-Engine Communication Bridge
VoxelBridge: Python ↔ C++ renderer messaging.
Operates in DISCONNECTED mode gracefully when renderer is absent.
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("pubcast.bridge")

class BridgeStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTING   = "connecting"
    CONNECTED    = "connected"
    ERROR        = "error"

@dataclass
class BridgeMetrics:
    commands_sent:   int   = 0
    commands_failed: int   = 0
    avg_latency_ms:  float = 0.0
    fps:             float = 0.0
    gpu_health:      float = 1.0
    queue_depth:     int   = 0

class VoxelBridge:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = Path(data_dir)
        self._status   = BridgeStatus.DISCONNECTED
        self._metrics  = BridgeMetrics()

    def connect(self, *, create_shared: bool = False) -> bool:
        self._status = BridgeStatus.CONNECTING
        lock = self._data_dir / "renderer.pid"
        if lock.exists():
            self._status = BridgeStatus.CONNECTED
            return True
        self._status = BridgeStatus.DISCONNECTED
        return False

    def send_command(self, cmd_type: str, payload: Dict[str, Any], priority: int = 0) -> bool:
        if self._status != BridgeStatus.CONNECTED:
            self._metrics.commands_failed += 1
            return False
        self._metrics.commands_sent += 1
        return True

    def disconnect(self) -> None:
        self._status = BridgeStatus.DISCONNECTED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status":          self._status.value,
            "commands_sent":   self._metrics.commands_sent,
            "commands_failed": self._metrics.commands_failed,
            "avg_latency_ms":  self._metrics.avg_latency_ms,
            "fps":             self._metrics.fps,
            "gpu_health":      self._metrics.gpu_health,
            "queue_depth":     self._metrics.queue_depth,
        }

__all__ = ["VoxelBridge", "BridgeStatus", "BridgeMetrics"]
