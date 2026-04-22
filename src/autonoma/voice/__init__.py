"""Voice profile / binding service for OmniVoice-backed TTS.

Parallel to ``autonoma.mocap``: a global (site-wide) mapping of
``vrm_file → voice_profile`` owns how each character sounds, and the
profiles themselves are small reference-audio blobs + their transcripts.

The synthesis path (``autonoma.tts``) reads these bindings at speech
time: agent → vrm_file → profile → (ref_audio, ref_text) → OmniVoice.
"""

from autonoma.voice.store import (
    Binding,
    Profile,
    ProfileSummary,
    create_profile,
    delete_binding,
    delete_profile,
    get_binding,
    get_profile,
    get_profile_audio,
    get_profile_summary,
    list_bindings,
    list_profile_summaries,
    profile_is_bound,
    upsert_binding,
)
from autonoma.voice.vrm_map import vrm_file_for_agent

__all__ = [
    "Binding",
    "Profile",
    "ProfileSummary",
    "create_profile",
    "delete_binding",
    "delete_profile",
    "get_binding",
    "get_profile",
    "get_profile_audio",
    "get_profile_summary",
    "list_bindings",
    "list_profile_summaries",
    "profile_is_bound",
    "upsert_binding",
    "vrm_file_for_agent",
]
