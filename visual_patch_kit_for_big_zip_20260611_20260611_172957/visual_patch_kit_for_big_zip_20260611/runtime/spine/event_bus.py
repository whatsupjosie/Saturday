"""Canonical event routing for the PubCast runtime spine."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any, Awaitable, Callable, Deque, Dict, Iterable, List, Optional, Tuple


EVENT_TYPES: Tuple[str, ...] = (
    "performer:spawned",
    "performer:moved",
    "performer:animated",
    "performer:state_change",
    "performer:interaction",
    "performer:contact",
    "interaction:fit_report",
    "object:adaptive_prop_extension_proposed",
    "object:adaptive_prop_extension",
    "object:adaptive_prop_extension_rejected",
    "object:adaptive_prop_extension_saved",
    "object:adaptive_prop_extension_kept_in_room",
    "object:adaptive_prop_extension_retired",
    "object:adaptive_prop_extension_cached",
    "object:adaptive_prop_extension_cache_expired",
    "motion:feedback",
    "motion:feedback_sink_error",
    "motion:feedback_sink_pending",
    "room:entered",
    "room:exited",
    "room:loaded",
    "station:activated",
    "station:deactivated",
    "station:state_change",
    "camera:focused",
    "camera:live",
    "camera:transition",
    "lighting:preset",
    "lighting:fade",
    "ui:modal_open",
    "ui:modal_close",
    "ui:panel_update",
    "pub:interaction",
    "pub:message",
    "audio:level",
    "audio:state_change",
    "avatar:compensation_hint",
    "avatar:interaction_compensation",
    "avatar:mesh_glitch",
    "avatar:visual_patch",
    "avatar:visual_patch_bake_requested",
    "avatar:visual_patch_mesh_ready",
    "avatar:visual_patch_retired",
    "avatar:digital_makeup",
    "costume:style_adjustment",
    "jeremy:stage_direction",
    "jeremy:visual_patch_request",
    "jeremy:style_request",
    "set:style_adjustment",
    "trivia:started",
    "trivia:question",
    "visual:style_plan",
    "visual:style_request",
    "visual:patch_workflow_denied",
    "visual:patch_workflow_report",
    "visual:patch_workflow_request",
)


class FrozenDict(Mapping):
    """Small immutable mapping used for event payloads."""

    def __init__(self, values: Mapping[str, Any]):
        self._values = dict(values)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"FrozenDict({self._values!r})"

    def to_dict(self) -> Dict[str, Any]:
        return _thaw(self)


Handler = Callable[[FrozenDict], Any]
logger = logging.getLogger("pubcast.runtime.spine.event_bus")


def _freeze(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, FrozenDict):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


class EventBus:
    """Canonical event routing for PubCast runtime."""

    def __init__(self, history_limit: int = 500):
        self._subscriptions: Dict[str, List[Tuple[str, Handler]]] = defaultdict(list)
        self._tokens: Dict[str, Tuple[str, Handler]] = {}
        self._history: Deque[FrozenDict] = deque(maxlen=history_limit)
        self._errors: Deque[FrozenDict] = deque(maxlen=history_limit)

    def subscribe(self, event_type: str, handler: Handler) -> str:
        """Subscribe to event type, return unsubscribe token."""

        token = uuid.uuid4().hex
        self._subscriptions[event_type].append((token, handler))
        self._tokens[token] = (event_type, handler)
        return token

    def unsubscribe(self, token: str):
        """Clean removal from subscriptions."""

        subscription = self._tokens.pop(token, None)
        if not subscription:
            return
        event_type, handler = subscription
        self._subscriptions[event_type] = [
            item for item in self._subscriptions[event_type]
            if item[0] != token
        ]

    def emit(self, event_type: str, data: Optional[dict] = None, source: str = "system"):
        """Emit structured event with source tracking."""

        event = self._record_event(event_type, data or {}, source)
        for handler in self._handlers_for(event_type):
            try:
                result = handler(event)
            except Exception as exc:  # noqa: BLE001 - event bus must isolate subscribers.
                self._record_handler_error(event, handler, exc)
                continue
            if inspect.isawaitable(result):
                self._finish_awaitable(result, event, handler)
        return event

    async def emit_async(self, event_type: str, data: Optional[dict] = None, source: str = "system"):
        """Async-safe emission."""

        event = self._record_event(event_type, data or {}, source)
        await asyncio.gather(*(self._call_handler(handler, event) for handler in self._handlers_for(event_type)))
        return event

    def clear(self):
        """Full teardown for testing and shutdown."""

        self._subscriptions.clear()
        self._tokens.clear()
        self._history.clear()
        self._errors.clear()

    def history(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return recent events as JSON-serializable dictionaries."""

        events: Iterable[FrozenDict] = self._history if limit is None else list(self._history)[-limit:]
        return [_thaw(event) for event in events]

    def subscriber_counts(self) -> Dict[str, int]:
        return {event_type: len(handlers) for event_type, handlers in self._subscriptions.items()}

    def errors(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Return recent handler failures without interrupting event flow."""

        errors: Iterable[FrozenDict] = self._errors if limit is None else list(self._errors)[-limit:]
        return [_thaw(error) for error in errors]

    def _record_event(self, event_type: str, data: dict, source: str) -> FrozenDict:
        event = _freeze({
            "event_type": event_type,
            "data": dict(data),
            "source": source,
            "timestamp": time.time(),
        })
        self._history.append(event)
        return event

    def _handlers_for(self, event_type: str) -> List[Handler]:
        handlers = [handler for _, handler in self._subscriptions.get(event_type, [])]
        handlers.extend(handler for _, handler in self._subscriptions.get("*", []))
        return handlers

    async def _call_handler(self, handler: Handler, event: FrozenDict):
        try:
            if inspect.iscoroutinefunction(handler):
                await handler(event)
                return
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, handler, event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - one subscriber cannot stop the bus.
            self._record_handler_error(event, handler, exc)

    def _finish_awaitable(self, awaitable: Awaitable[Any], event: FrozenDict, handler: Handler):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(awaitable)
            except Exception as exc:  # noqa: BLE001
                self._record_handler_error(event, handler, exc)
            return
        task = loop.create_task(awaitable)
        task.add_done_callback(lambda done: self._record_task_error(done, event, handler))

    def _record_task_error(self, task: asyncio.Task, event: FrozenDict, handler: Handler):
        try:
            task.result()
        except Exception as exc:  # noqa: BLE001
            self._record_handler_error(event, handler, exc)

    def _record_handler_error(self, event: FrozenDict, handler: Handler, exc: Exception):
        error = _freeze({
            "event_type": "event:error",
            "data": {
                "source_event_type": event["event_type"],
                "handler": _handler_name(handler),
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            "source": "event_bus",
            "timestamp": time.time(),
        })
        self._errors.append(error)
        logger.debug(
            "event handler failed for %s via %s",
            event["event_type"],
            _handler_name(handler),
            exc_info=exc,
        )


def _handler_name(handler: Handler) -> str:
    return getattr(handler, "__qualname__", getattr(handler, "__name__", repr(handler)))
