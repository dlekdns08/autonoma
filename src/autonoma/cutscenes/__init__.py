"""Cutscene composer (Phase 3-#3).

A cutscene is a tiny scripted scene: a sequence of timed steps that the
host can trigger manually or via an achievement / project-completion
hook. Each step is one of:

  * ``clip``  — play a mocap clip on a target VRM
  * ``speech`` — emit an agent.speech line (with TTS if enabled)
  * ``sfx``    — fire a named sound effect on connected viewers
  * ``delay``  — pure timing pad

Storage is filesystem-backed JSON under ``settings.data_dir/cutscenes/``
keyed by ``(owner_user_id, cutscene_id)``. We deliberately don't add a
DB table: cutscenes are user-authored content with version-by-overwrite
semantics, and the JSON files are easy to backup / share.

The composer UI reads/writes through the FastAPI router
(``src/autonoma/routers/cutscenes.py``); the runtime trigger publishes
``cutscene.step`` events on the bus so the frontend ``useCutscenes``
hook can sequence the playback.
"""

from autonoma.cutscenes.model import (
    Cutscene,
    CutsceneStep,
    CutsceneStepKind,
)
from autonoma.cutscenes.store import (
    CutsceneNotFound,
    CutsceneStore,
    cutscene_store,
)

__all__ = [
    "Cutscene",
    "CutsceneStep",
    "CutsceneStepKind",
    "CutsceneNotFound",
    "CutsceneStore",
    "cutscene_store",
]
