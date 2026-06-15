"""Feedback bridge from animation diagnostics to avatar/Jeremy coordination."""

from __future__ import annotations

import inspect
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..event_bus import EventBus, FrozenDict


logger = logging.getLogger("pubcast.runtime.spine.motion_feedback")

FeedbackSink = Callable[[Dict[str, Any]], Any]


@dataclass(frozen=True)
class MotionFeedbackSignal:
    performer_id: str
    animation: str
    risk_level: str
    compatibility_score: float
    warnings: List[str] = field(default_factory=list)
    compensation_hints: List[str] = field(default_factory=list)
    jeremy_hint: Optional[str] = None
    avatar_ai_hint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MotionFeedbackCoordinator:
    """Listen to animation reports and publish live compensation guidance."""

    def __init__(
        self,
        event_bus: EventBus,
        jeremy_sink: Optional[FeedbackSink] = None,
        avatar_ai_sink: Optional[FeedbackSink] = None,
        min_risk_level: str = "usable",
    ):
        self.event_bus = event_bus
        self.jeremy_sink = jeremy_sink
        self.avatar_ai_sink = avatar_ai_sink
        self.min_risk_level = min_risk_level
        self._token: Optional[str] = None
        self.last_feedback: Optional[MotionFeedbackSignal] = None

    def start(self) -> str:
        """Start listening for animation reports."""

        if self._token is None:
            self._token = self.event_bus.subscribe("performer:animated", self._on_performer_animated)
        return self._token

    def stop(self):
        """Stop listening without clearing event history."""

        if self._token is not None:
            self.event_bus.unsubscribe(self._token)
            self._token = None

    def evaluate_event(self, event: FrozenDict) -> Optional[MotionFeedbackSignal]:
        data = _plain_dict(event.get("data", {}))
        report = _plain_dict(data.get("retarget_report", {}))
        if not report:
            return None

        performer_id = str(data.get("performer_id", "unknown"))
        animation = str(data.get("animation", "unknown"))
        risk_level = str(report.get("risk_level", "good"))
        score = _float(report.get("compatibility_score", 1.0), 1.0)
        warnings = [str(item) for item in report.get("warnings", [])]
        dropped = [str(item) for item in report.get("dropped_joints", [])]
        clamped = [str(item) for item in report.get("clamped_channels", [])]
        invalid = [str(item) for item in report.get("invalid_channels", [])]

        if _risk_rank(risk_level) < _risk_rank(self.min_risk_level) and not warnings:
            return None

        hints = _build_compensation_hints(risk_level, warnings, dropped, clamped, invalid)
        if not hints and risk_level == "good":
            return None

        jeremy_hint = _build_jeremy_hint(performer_id, animation, risk_level, hints)
        avatar_ai_hint = _build_avatar_ai_hint(animation, hints)
        return MotionFeedbackSignal(
            performer_id=performer_id,
            animation=animation,
            risk_level=risk_level,
            compatibility_score=score,
            warnings=warnings,
            compensation_hints=hints,
            jeremy_hint=jeremy_hint,
            avatar_ai_hint=avatar_ai_hint,
        )

    def _on_performer_animated(self, event: FrozenDict):
        feedback = self.evaluate_event(event)
        if feedback is None:
            return
        self.last_feedback = feedback
        payload = feedback.to_dict()
        self.event_bus.emit("motion:feedback", payload, source="motion_feedback_coordinator")
        self.event_bus.emit(
            "avatar:compensation_hint",
            {
                "performer_id": feedback.performer_id,
                "animation": feedback.animation,
                "hints": feedback.compensation_hints,
                "risk_level": feedback.risk_level,
                "compatibility_score": feedback.compatibility_score,
            },
            source="motion_feedback_coordinator",
        )
        if feedback.jeremy_hint:
            self.event_bus.emit(
                "jeremy:stage_direction",
                {
                    "performer_id": feedback.performer_id,
                    "animation": feedback.animation,
                    "hint": feedback.jeremy_hint,
                    "risk_level": feedback.risk_level,
                },
                source="motion_feedback_coordinator",
            )
        self._send_sink(self.avatar_ai_sink, payload, "avatar_ai_sink")
        self._send_sink(self.jeremy_sink, payload, "jeremy_sink")

    def _send_sink(self, sink: Optional[FeedbackSink], payload: Dict[str, Any], label: str):
        if sink is None:
            return
        try:
            result = sink(payload)
            if inspect.isawaitable(result):
                self.event_bus.emit(
                    "motion:feedback_sink_pending",
                    {"sink": label, "performer_id": payload.get("performer_id")},
                    source="motion_feedback_coordinator",
                )
        except Exception as exc:  # noqa: BLE001 - feedback must not break animation.
            logger.warning("motion feedback sink failed: %s", exc)
            self.event_bus.emit(
                "motion:feedback_sink_error",
                {"sink": label, "error_type": type(exc).__name__, "error": str(exc)},
                source="motion_feedback_coordinator",
            )


def _build_compensation_hints(
    risk_level: str,
    warnings: List[str],
    dropped: List[str],
    clamped: List[str],
    invalid: List[str],
) -> List[str]:
    hints: List[str] = []
    if risk_level in {"strained", "likely_bad"}:
        hints.append("increase_blend_time")
        hints.append("reduce_motion_amplitude")
    if risk_level == "likely_bad":
        hints.append("prefer_target_equivalent_motion")
        hints.append("fallback_to_simpler_animation")
    if any("extreme_" in warning for warning in warnings):
        hints.append("scale_stride_and_reach_conservatively")
    if any("many_channels_clamped" == warning for warning in warnings) or len(clamped) >= 3:
        hints.append("avoid_forcing_joint_limits")
    if any("many_source_joints_dropped" == warning for warning in warnings) or len(dropped) >= 3:
        hints.append("use_primary_body_intent_only")
    if invalid:
        hints.append("sanitize_or_reacquire_motion_frame")
    if "no_target_skeleton" in warnings:
        hints.append("delay_until_avatar_skeleton_ready")
    return _dedupe(hints)


def _build_jeremy_hint(performer_id: str, animation: str, risk_level: str, hints: List[str]) -> Optional[str]:
    if not hints:
        return None
    if risk_level in {"good", "usable"} and len(hints) <= 1:
        return None
    hint_text = ", ".join(hints[:3]).replace("_", " ")
    return (
        f"{performer_id}'s {animation} motion is {risk_level}; privately steer the performance toward "
        f"{hint_text} so the scene stays fluid."
    )


def _build_avatar_ai_hint(animation: str, hints: List[str]) -> Optional[str]:
    if not hints:
        return None
    return f"Apply live compensation for {animation}: " + ", ".join(hints[:5])


def _plain_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, FrozenDict):
        return value.to_dict()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _risk_rank(risk_level: str) -> int:
    return {"good": 0, "usable": 1, "strained": 2, "likely_bad": 3}.get(risk_level, 0)


def _dedupe(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
