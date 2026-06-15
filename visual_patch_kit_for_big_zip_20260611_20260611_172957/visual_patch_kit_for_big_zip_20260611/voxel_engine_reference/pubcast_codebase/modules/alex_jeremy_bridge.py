from __future__ import annotations

import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_PACKET_TTL_SECONDS = 60 * 60 * 8
MAX_SIGNAL_HISTORY = 64

from .alex_core import AlexCore, AIState, UserBattery

logger = logging.getLogger("alex_jeremy_bridge")


class AlexJeremyBridge:
    """Permissioned bridge between Alex (personal) and Jeremy (PubCast space).

    Alex stays sovereign and private. Jeremy only receives a minimal packet that
    helps him run the room more gently/intelligently.
    """

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.bridge_dir = self.data_dir / "alex_bridge"
        self.bridge_dir.mkdir(parents=True, exist_ok=True)
        self._alex_instances: Dict[str, AlexCore] = {}
        self._io_lock = threading.RLock()

    def _safe(self, value: str) -> str:
        return ''.join(c if c.isalnum() or c in '-_.' else '_' for c in str(value or 'anon'))[:120] or 'anon'

    def _session_path(self, session_id: str) -> Path:
        return self.bridge_dir / f"session_{self._safe(session_id)}.json"

    def _signals_path(self, session_id: str, user_id: str) -> Path:
        return self.bridge_dir / f"signals_{self._safe(session_id)}_{self._safe(user_id)}.json"

    def alex_for(self, user_id: str) -> AlexCore:
        user_id = user_id or "default"
        alex = self._alex_instances.get(user_id)
        if alex is None:
            alex = AlexCore(user_id=user_id, data_dir=self.data_dir / "alex")
            self._alex_instances[user_id] = alex
        return alex

    def build_entry_packet(
        self,
        *,
        user_id: str,
        session_id: str,
        project_id: str,
        room_id: str,
        display_name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        alex = self.alex_for(user_id)
        packet = self._packet_from_alex(alex, user_id=user_id, session_id=session_id, project_id=project_id, room_id=room_id)
        if metadata and isinstance(metadata, dict):
            packet["entry_context"] = {
                "presence_mode": metadata.get("presence_mode"),
                "availability": metadata.get("availability"),
            }
        packet.update({
            "display_name": display_name or user_id,
            "bridge_scope": "entry",
            "generated_at": time.time(),
        })
        self._persist_packet(session_id, user_id, packet)
        # store contextual moment in Alex's personal memory without exposing it to Jeremy
        summary = f"Entered PubCast session {session_id} project {project_id} room {room_id}."
        alex.memory.store(summary, {"valence": 0.0, "intensity": min(1.0, packet["fragility_level"])})
        alex.save_state_snapshot()
        return packet

    def signal_from_jeremy(
        self,
        *,
        user_id: str,
        session_id: str,
        room_state: str,
        urgency: str,
        reason: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        alex = self.alex_for(user_id)
        signal = {
            "ts": time.time(),
            "user_id": user_id,
            "session_id": session_id,
            "room_state": room_state,
            "urgency": urgency,
            "reason": reason,
            "payload": payload or {},
        }
        signals = self._load_json(self._signals_path(session_id, user_id), default=[])
        signals.append(signal)
        signals = signals[-MAX_SIGNAL_HISTORY:]
        self._write_json(self._signals_path(session_id, user_id), signals)

        # Nudge Alex's live state so follow-up packets actually respond to room distress.
        stress_floor = {"low": 0.20, "medium": 0.45, "high": 0.75}.get(urgency, 0.25)
        room_bonus = {"stable": 0.0, "tense": 0.05, "destabilizing": 0.15, "critical": 0.25}.get(room_state, 0.0)
        target_stress = min(1.0, stress_floor + room_bonus)
        alex._user_state.stress_level = max(alex._user_state.stress_level, target_stress)
        if urgency in {"medium", "high"}:
            alex._user_state.clarity_score = min(alex._user_state.clarity_score, 0.85 if urgency == "medium" else 0.5)
        if urgency == "high":
            alex._user_state.energy_level = min(alex._user_state.energy_level, 0.4)
        alex._cached_stress_score = alex._user_state.calculate_stress_score()
        alex._update_ai_state()

        intensity = max(0.25, target_stress)
        alex.memory.store(
            f"Jeremy signaled from room: {room_state} / {urgency} / {reason}",
            {"valence": -0.2 if urgency != "low" else 0.0, "intensity": intensity},
        )
        alex.save_state_snapshot()

        base_packet = self._load_packet(session_id, user_id) or self._packet_from_alex(
            alex, user_id=user_id, session_id=session_id, project_id="", room_id=""
        )
        refreshed = self._apply_signal_overlay(dict(base_packet), signal)
        self._persist_packet(session_id, user_id, refreshed)
        return refreshed

    def current_packet(self, *, user_id: str, session_id: str) -> Dict[str, Any]:
        packet = self._load_packet(session_id, user_id)
        if packet and not self._packet_expired(packet):
            return packet
        if packet and self._packet_expired(packet):
            self.clear_packet(session_id=session_id, user_id=user_id)
        return self.build_entry_packet(user_id=user_id, session_id=session_id, project_id="default", room_id="default")

    def clear_packet(self, *, session_id: str, user_id: str) -> bool:
        path = self._session_path(session_id)
        payload = self._load_json(path, default={"session_id": session_id, "packets": {}})
        packets = payload.setdefault("packets", {})
        removed = packets.pop(user_id, None) is not None
        if removed:
            self._write_json(path, payload)
        return removed

    def clear_session(self, *, session_id: str) -> None:
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()

    def downgrade_after_stabilization(self, *, user_id: str, session_id: str) -> Dict[str, Any]:
        packet = self.current_packet(user_id=user_id, session_id=session_id)
        packet["care_priority"] = "low" if packet.get("fragility_level", 0.0) < 0.35 else "medium"
        packet["tone_guidance"] = "steady" if packet.get("fragility_level", 0.0) < 0.4 else packet.get("tone_guidance", "steady")
        packet["bridge_scope"] = "stabilized"
        packet["generated_at"] = time.time()
        self._persist_packet(session_id, user_id, packet)
        return packet

    def jeremy_whisper(self, *, user_id: str, session_id: str) -> str:
        packet = self.current_packet(user_id=user_id, session_id=session_id)
        tone = packet.get("tone_guidance", "steady")
        fragility = float(packet.get("fragility_level", 0.0))
        flags = list(packet.get("do_not_touch", []))
        active = list(packet.get("active_threads", []))
        parts = [f"Jeremy note: keep tone {tone}."]
        if fragility >= 0.7:
            parts.append("User appears fragile; minimize friction and keep steps small.")
        elif fragility >= 0.4:
            parts.append("User may need gentler pacing and reduced noise.")
        if flags:
            parts.append("Avoid: " + ", ".join(flags[:3]) + ".")
        if active:
            parts.append("Active threads: " + ", ".join(active[:3]) + ".")
        if packet.get("resumed_from_memory"):
            parts.append("This user has prior Alex memory; preserve continuity without announcing it.")
        return " ".join(parts)

    def shutdown(self) -> None:
        for alex in self._alex_instances.values():
            try:
                alex.shutdown()
            except Exception:
                logger.exception("Failed to shutdown Alex instance")
        self._alex_instances.clear()

    def _packet_from_alex(self, alex: AlexCore, *, user_id: str, session_id: str, project_id: str, room_id: str) -> Dict[str, Any]:
        state_name = alex._current_state.value
        battery = alex._user_battery.value
        stress = float(alex._user_state.calculate_stress_score())
        fragility = max(0.0, min(1.0, round(stress, 3)))

        tone = "steady"
        pace = "normal"
        intervention = "light"
        flags: List[str] = []

        if alex._current_state == AIState.ANCHOR:
            tone = "gentle"
            pace = "slow"
            intervention = "protective"
            flags.append("avoid_confrontation")
        elif alex._current_state == AIState.WITNESS:
            tone = "minimal"
            pace = "quiet"
            intervention = "hold_space"
            flags.append("avoid_overexplaining")
        elif alex._current_state == AIState.MIRROR:
            tone = "reflective"
            pace = "measured"
            intervention = "validate_first"
        elif alex._current_state == AIState.COMPANION:
            tone = "warm"
            intervention = "casual"

        if alex._user_battery in (UserBattery.LOW, UserBattery.DEPLETED):
            flags.append("avoid_complexity")
            pace = "slow"

        recent = alex.memory.recall(days_back=14, limit=5)
        active_threads = []
        recent_memory_types = []
        seen = set()
        for mem in recent:
            memory_type = getattr(getattr(mem, "memory_type", None), "value", "")
            if memory_type and memory_type not in recent_memory_types:
                recent_memory_types.append(memory_type)
            if mem.keywords:
                key = mem.keywords[0]
            else:
                key = mem.content[:32]
            if key not in seen:
                active_threads.append(key)
                seen.add(key)

        return {
            "user_id": user_id,
            "session_id": session_id,
            "project_id": project_id,
            "room_id": room_id,
            "alex_state": state_name,
            "user_battery": battery,
            "tone_guidance": tone,
            "pace_guidance": pace,
            "intervention_style": intervention,
            "fragility_level": fragility,
            "care_priority": "high" if fragility >= 0.7 else "medium" if fragility >= 0.35 else "low",
            "do_not_touch": flags,
            "active_threads": active_threads,
            "resumed_from_memory": bool(recent),
            "remembered_memory_count": len(recent),
            "recent_memory_types": recent_memory_types[:5],
        }

    def _persist_packet(self, session_id: str, user_id: str, packet: Dict[str, Any]) -> None:
        path = self._session_path(session_id)
        payload = self._load_json(path, default={"session_id": session_id, "packets": {}})
        payload.setdefault("packets", {})[user_id] = packet
        self._write_json(path, payload)

    def _load_packet(self, session_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        payload = self._load_json(self._session_path(session_id), default={})
        return (payload.get("packets") or {}).get(user_id)

    def _packet_expired(self, packet: Dict[str, Any], *, ttl_seconds: int = DEFAULT_PACKET_TTL_SECONDS) -> bool:
        generated_at = float(packet.get("generated_at") or 0)
        if generated_at <= 0:
            return False
        return (time.time() - generated_at) > ttl_seconds


    def _apply_signal_overlay(self, packet: Dict[str, Any], signal: Dict[str, Any]) -> Dict[str, Any]:
        urgency = signal.get("urgency", "low")
        room_state = signal.get("room_state", "stable")
        reason = signal.get("reason", "")

        fragility_floor = {"low": 0.25, "medium": 0.50, "high": 0.80}.get(urgency, 0.25)
        if room_state == "destabilizing":
            fragility_floor = max(fragility_floor, 0.65)
        elif room_state == "critical":
            fragility_floor = max(fragility_floor, 0.90)

        packet["fragility_level"] = max(float(packet.get("fragility_level", 0.0)), fragility_floor)

        priorities = {"low": 1, "medium": 2, "high": 3}
        current_priority = packet.get("care_priority", "low")
        target_priority = "high" if urgency == "high" or room_state == "critical" else "medium" if urgency == "medium" or room_state == "destabilizing" else "low"
        if priorities.get(target_priority, 1) > priorities.get(current_priority, 1):
            packet["care_priority"] = target_priority

        tone = packet.get("tone_guidance", "steady")
        if urgency == "high" or room_state == "critical":
            tone = "gentle"
            packet["pace_guidance"] = "slow"
            packet["intervention_style"] = "protective"
        elif urgency == "medium" or room_state == "destabilizing":
            tone = "steady" if tone == "warm" else tone
            packet["pace_guidance"] = "measured"
            packet["intervention_style"] = "validate_first"
        packet["tone_guidance"] = tone

        flags = list(packet.get("do_not_touch", []))
        if urgency in {"medium", "high"} and "avoid_complexity" not in flags:
            flags.append("avoid_complexity")
        if urgency == "high" and "avoid_confrontation" not in flags:
            flags.append("avoid_confrontation")
        if room_state in {"destabilizing", "critical"} and "reduce_noise" not in flags:
            flags.append("reduce_noise")
        packet["do_not_touch"] = flags

        packet["bridge_scope"] = "signal_response"
        packet["latest_signal"] = {k: signal[k] for k in ("room_state", "urgency", "reason", "ts")}
        packet["generated_at"] = time.time()
        packet["signal_count"] = int(packet.get("signal_count", 0)) + 1
        if reason:
            active_threads = list(packet.get("active_threads", []))
            if reason not in active_threads:
                packet["active_threads"] = ([reason] + active_threads)[:5]
        return packet

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2)
        with self._io_lock:
            with tempfile.NamedTemporaryFile('w', delete=False, dir=str(path.parent), encoding='utf-8') as tmp:
                tmp.write(data)
                tmp.flush()
                tmp_path = Path(tmp.name)
            tmp_path.replace(path)

    def _load_json(self, path: Path, default: Any) -> Any:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.exception("Failed reading bridge json: %s", path)
        return default


__all__ = ["AlexJeremyBridge"]
