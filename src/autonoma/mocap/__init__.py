"""Motion capture clip storage and binding service.

Exposes server-side validation for recorded VRM motion clips uploaded
from the ``/mocap`` frontend, along with CRUD helpers for the clip
library and the global (per-VRM-character) trigger-to-clip bindings
used by the VTuber playback path.
"""

from autonoma.mocap.triggers import (
    ALLOWED_TRIGGER_KINDS,
    MOOD_TRIGGERS,
    EMOTE_TRIGGERS,
    STATE_TRIGGERS,
    MANUAL_SLUG_RE,
    is_known_vrm,
    validate_trigger,
)
from autonoma.mocap.validator import (
    MocapValidationError,
    validate_payload,
    MAX_PAYLOAD_SIZE_BYTES,
)

__all__ = [
    "ALLOWED_TRIGGER_KINDS",
    "MOOD_TRIGGERS",
    "EMOTE_TRIGGERS",
    "STATE_TRIGGERS",
    "MANUAL_SLUG_RE",
    "MAX_PAYLOAD_SIZE_BYTES",
    "MocapValidationError",
    "is_known_vrm",
    "validate_trigger",
    "validate_payload",
]
