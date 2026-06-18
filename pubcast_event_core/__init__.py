"""PubCast event architecture core.

This package captures the boring contracts behind the PubCast premiere/concert
ideas: shared event, local reality, 3D avatar requirement, broadcast authority,
room isolation, red-carpet admission, and Pete/producer/owner control flow.

It is intentionally dependency-light so it can be imported by FastAPI, tests,
Codex probes, or a future game-runtime bridge without dragging in rendering code.
"""

from .contracts import (
    AdmissionStatus,
    AuthorityRole,
    AvatarAsset,
    AvatarRuntimeState,
    BroadcastPermission,
    ChatChannel,
    EventMode,
    LocalReality,
    ModerationAction,
    Participant,
    RoomKind,
)
from .runtime import PubCastEventRuntime

__all__ = [
    "AdmissionStatus",
    "AuthorityRole",
    "AvatarAsset",
    "AvatarRuntimeState",
    "BroadcastPermission",
    "ChatChannel",
    "EventMode",
    "LocalReality",
    "ModerationAction",
    "Participant",
    "RoomKind",
    "PubCastEventRuntime",
]
