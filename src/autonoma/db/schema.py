"""SQLAlchemy Core table definitions for the persistent character DB.

We use Core (not the ORM) because character state is a small, bounded
record and we don't want Session/lazy-loading complexity. Everything is
accessed via ``sqlalchemy.insert/select/update`` with explicit binds.

Design notes
────────────
- Every character has a stable ``character_uuid`` (string) independent of
  its display name. Names can collide across sessions; the uuid can't.
- ``seed_hash`` — md5 of ``f"{role}:{name}:autonoma-world-v1"`` — is the
  deterministic key the swarm already uses in ``AgentBones.from_role``.
  We index it to enable "give me the same character as last time for this
  role+name combination".
- ``rarity`` is persisted on the character row and drives the revival
  policy: legendary → always revived, everything else → per-run decision.
- Graveyard rows are immutable; once an agent dies they stay dead forever
  in the sense that the DB still holds their tombstone.
- Wills are short messages an agent leaves when they die. Loaded into the
  opening narrator/spawn context of future runs that rehire the same
  character (if they somehow come back) or into the graveyard UI for
  everyone else.
- Keep nullable columns minimal; use explicit defaults at insert time so
  schema evolution stays predictable.
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    Index,
    func,
)

metadata = MetaData()


# ── characters ─────────────────────────────────────────────────────────
# One row per distinct character ever spawned. Re-visiting a role+name
# seed returns this exact row; the registry owns that lookup.
characters = Table(
    "characters",
    metadata,
    Column("character_uuid", String(36), primary_key=True),
    Column("seed_hash", String(32), nullable=False, index=True),
    Column("name", String(64), nullable=False),
    Column("role", String(128), nullable=False),
    Column("species", String(32), nullable=False),
    Column("species_emoji", String(8), nullable=False),
    Column("catchphrase", String(128), nullable=False, default=""),
    Column("rarity", String(16), nullable=False, default="common"),
    # cumulative, across every run the character has lived through
    Column("level", Integer, nullable=False, default=1),
    Column("total_xp_earned", Integer, nullable=False, default=0),
    Column("runs_survived", Integer, nullable=False, default=0),
    Column("runs_died", Integer, nullable=False, default=0),
    Column("tasks_completed_lifetime", Integer, nullable=False, default=0),
    Column("files_created_lifetime", Integer, nullable=False, default=0),
    # json blob; keeps ``stats`` (debugging/patience/chaos/wisdom/speed)
    # and any trait list without forcing more tables. Access via json1.
    Column("traits_json", Text, nullable=False, default="[]"),
    Column("stats_json", Text, nullable=False, default="{}"),
    # Last known mood/title flavor; purely cosmetic
    Column("last_mood", String(32), nullable=False, default=""),
    Column("voice_id", String(64), nullable=False, default=""),
    Column("is_alive", Integer, nullable=False, default=1),  # 0/1
    Column("first_seen_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("last_seen_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    # No unique constraint on (seed_hash, name): when a non-legendary
    # character dies, the next run with the same seed spawns a *new* row
    # (fresh uuid) so the graveyard row stays intact. The registry picks
    # the right row at lookup time (alive > dead legendary > create new).
)

Index("ix_characters_seed", characters.c.seed_hash)


# ── projects ──────────────────────────────────────────────────────────
# One row per swarm run. A "project" here means: a single AgentSwarm.run()
# invocation that was persisted. Tracks outcome so we can tell the story
# of a character's arc across the runs they were in.
projects = Table(
    "projects",
    metadata,
    Column("project_uuid", String(36), primary_key=True),
    Column("name", String(128), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("goal", Text, nullable=False, default=""),
    Column("status", String(16), nullable=False, default="running"),
    Column("rounds_used", Integer, nullable=False, default=0),
    Column("max_rounds", Integer, nullable=False, default=30),
    Column("started_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("ended_at", DateTime, nullable=True),
    Column("exit_reason", String(32), nullable=False, default=""),
    Column("final_answer", Text, nullable=False, default=""),
)


# ── project_participants ──────────────────────────────────────────────
# Many-to-many between projects and characters. Holds per-run outcomes
# for each character: did they survive, how much XP did they earn, etc.
project_participants = Table(
    "project_participants",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "project_uuid",
        String(36),
        ForeignKey("projects.project_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "character_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role_in_run", String(128), nullable=False, default=""),
    Column("xp_earned", Integer, nullable=False, default=0),
    Column("tasks_completed", Integer, nullable=False, default=0),
    Column("files_created", Integer, nullable=False, default=0),
    Column("survived", Integer, nullable=False, default=1),  # 0/1
    Column("death_cause", String(64), nullable=False, default=""),
    Column("joined_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("left_at", DateTime, nullable=True),
    UniqueConstraint("project_uuid", "character_uuid", name="uq_pp_project_char"),
)


# ── character_stats_history ───────────────────────────────────────────
# Append-only snapshots taken at project end, so we can draw a graph of
# a character's stat growth across their career.
character_stats_history = Table(
    "character_stats_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "character_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "project_uuid",
        String(36),
        ForeignKey("projects.project_uuid", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("level", Integer, nullable=False, default=1),
    Column("total_xp_earned", Integer, nullable=False, default=0),
    Column("stats_json", Text, nullable=False, default="{}"),
    Column("recorded_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)


# ── relationships ─────────────────────────────────────────────────────
# Directional. (from_uuid -> to_uuid) persisted at project end. On the
# next run where both characters are present, trust is seeded from here.
relationships = Table(
    "relationships",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "from_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "to_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("trust", Float, nullable=False, default=0.5),
    Column("familiarity", Integer, nullable=False, default=0),
    Column("shared_tasks", Integer, nullable=False, default=0),
    Column("conflicts", Integer, nullable=False, default=0),
    Column("sentiment", String(16), nullable=False, default="neutral"),
    Column("last_interaction", Text, nullable=False, default=""),
    Column("updated_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("from_uuid", "to_uuid", name="uq_relationship_directed"),
)

Index("ix_rel_from", relationships.c.from_uuid)
Index("ix_rel_to", relationships.c.to_uuid)


# ── graveyard ─────────────────────────────────────────────────────────
# One row per death. A character CAN die multiple times if they're
# revived in later runs — each death gets its own tombstone so the
# visual graveyard tells the full story.
graveyard = Table(
    "graveyard",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "character_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "project_uuid",
        String(36),
        ForeignKey("projects.project_uuid", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("died_at_round", Integer, nullable=False, default=0),
    Column("cause", String(64), nullable=False, default="unknown"),
    Column("epitaph", Text, nullable=False, default=""),
    Column("died_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)


# ── wills ─────────────────────────────────────────────────────────────
# A character's final message at the moment of death. The display layer
# pulls these for graveyard rendering + eulogy context.
wills = Table(
    "wills",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "character_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "project_uuid",
        String(36),
        ForeignKey("projects.project_uuid", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("text", Text, nullable=False, default=""),
    Column("written_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)


# ── famous_quotes ─────────────────────────────────────────────────────
# Memorable one-liners a character said. Curated by the narrator at the
# end of each run (top 1-2 quotes per project). Shown in the character's
# profile / the hall of fame overlay.
famous_quotes = Table(
    "famous_quotes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "character_uuid",
        String(36),
        ForeignKey("characters.character_uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "project_uuid",
        String(36),
        ForeignKey("projects.project_uuid", ondelete="SET NULL"),
        nullable=True,
    ),
    Column("text", Text, nullable=False, default=""),
    Column("round_number", Integer, nullable=False, default=0),
    Column("saved_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)


# ── file_history ────────────────────────────────────────────────────────
# Feature 9: Every file artifact created during a session is versioned
# here. The ``content`` column stores the full text; the list endpoint
# intentionally omits it to avoid large payloads.
file_history = Table(
    "file_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Integer, nullable=False, index=True),
    Column("path", String(512), nullable=False),
    Column("content", Text, nullable=False, default=""),
    Column("created_by", String(64), nullable=False, default=""),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("version_number", Integer, nullable=False, default=1),
)

Index("ix_file_history_session_path", file_history.c.session_id, file_history.c.path)


# ── run_summary ──────────────────────────────────────────────────────────
# Feature 12: One row per completed swarm run. Inserted at run completion
# so cross-run analytics ("compare run A vs run B") are possible without
# touching the per-run observability layer.
run_summary = Table(
    "run_summary",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Integer, nullable=False),
    Column("goal", Text, nullable=False, default=""),
    Column("started_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    Column("completed_at", DateTime, nullable=True),
    Column("agent_count", Integer, nullable=False, default=0),
    Column("task_count", Integer, nullable=False, default=0),
    Column("tasks_done", Integer, nullable=False, default=0),
    Column("tasks_failed", Integer, nullable=False, default=0),
    Column("total_rounds", Integer, nullable=False, default=0),
    Column("llm_calls", Integer, nullable=False, default=0),
    Column("preset_id", String(36), nullable=False, default=""),
    Column("policy_hash", String(32), nullable=False, default=""),
)


# ── session_checkpoint ────────────────────────────────────────────────────
# Feature 30: Periodic serialized snapshots of ProjectState. One row per
# checkpoint (upserted by session_id + round_number). The ``state_json``
# column holds the full JSON dump of ProjectState. Supports future resume.
session_checkpoint = Table(
    "session_checkpoint",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("session_id", Integer, nullable=False),
    Column("round_number", Integer, nullable=False, default=0),
    Column("state_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
    UniqueConstraint("session_id", "round_number", name="uq_checkpoint_session_round"),
)

Index("ix_checkpoint_session", session_checkpoint.c.session_id)


# ── world_event_log ───────────────────────────────────────────────────────
# Feature 10: Persistent log of world events processed by WorldEventLedger.
# Complements the in-memory ledger with durable queryability across sessions.
world_event_log = Table(
    "world_event_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_type", String(64), nullable=False, default=""),
    Column("title", String(256), nullable=False, default=""),
    Column("description", Text, nullable=False, default=""),
    Column("round", Integer, nullable=False, default=0),
    Column("triggered_by", String(64), nullable=False, default="system"),
    Column("created_at", DateTime, nullable=False, server_default=func.current_timestamp()),
)

Index("ix_world_event_log_round", world_event_log.c.round)
Index("ix_world_event_log_type", world_event_log.c.event_type)


# ── mocap_clips ───────────────────────────────────────────────────────────
# User-recorded motion capture clips. Payload is gzipped JSON of a
# ``MocapClip`` (frontend format: version=1, humanoid bone quaternion
# tracks + VRM expression tracks, rig-agnostic). Kept in-column because
# clips are small (< 100 KB compressed) and rarely updated.
mocap_clips = Table(
    "mocap_clips",
    metadata,
    Column("id", String(36), primary_key=True),  # uuid4
    Column(
        "owner_user_id",
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column("name", String(128), nullable=False),
    # The .vrm filename used when recording. Participates in rig-
    # independence checks only; playback is not limited to this VRM.
    Column("source_vrm", String(64), nullable=False),
    Column("duration_s", Float, nullable=False),
    Column("fps", Integer, nullable=False),
    Column("frame_count", Integer, nullable=False),
    Column("payload_gz", LargeBinary, nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
)


# ── mocap_bindings ────────────────────────────────────────────────────────
# Global (site-wide) trigger → clip bindings, keyed by VRM character file.
# Any agent rendered with ``vrm_file`` plays ``clip_id`` when the matching
# trigger (mood / emote / state / manual) fires. Single row per
# (vrm_file, kind, value) so setting a new clip just upserts.
mocap_bindings = Table(
    "mocap_bindings",
    metadata,
    Column("vrm_file", String(64), nullable=False),
    Column("trigger_kind", String(16), nullable=False),  # mood|emote|state|manual
    Column("trigger_value", String(64), nullable=False),
    Column(
        "clip_id",
        String(36),
        ForeignKey("mocap_clips.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "updated_by",
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    UniqueConstraint(
        "vrm_file",
        "trigger_kind",
        "trigger_value",
        name="pk_mocap_bindings",
    ),
)

Index(
    "ix_mocap_bindings_vrm",
    mocap_bindings.c.vrm_file,
    mocap_bindings.c.trigger_kind,
    mocap_bindings.c.trigger_value,
)


# ── voice_profiles ────────────────────────────────────────────────────────
# Per-character reference-audio profiles for zero-shot voice cloning
# (OmniVoice / k2-fsa).
#
# Storage layout, historical → current:
#   v1 (migration 006): ``ref_audio`` LargeBinary (NOT NULL). WAV bytes
#       stored in-column. Simple but bloats DB backups and blocks nginx
#       sendfile for the audio preview endpoint.
#   v2 (migration 007): ``ref_audio_path`` String (nullable) added; both
#       columns made nullable. New uploads go to
#       ``{data_dir}/voice_refs/{id}.{ext}`` and the column stores the
#       basename only. Reads prefer the path; missing path falls back to
#       the legacy BLOB so old rows keep working with zero downtime.
#       A future migration can drop the BLOB column once all rows are
#       migrated — out of scope for this change.
voice_profiles = Table(
    "voice_profiles",
    metadata,
    Column("id", String(36), primary_key=True),  # uuid4
    Column(
        "owner_user_id",
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column("name", String(128), nullable=False),
    Column("ref_text", Text, nullable=False, default=""),
    # Legacy BLOB — kept nullable for rows written in v1 (before the
    # filesystem migration). New writes leave this NULL and populate
    # ``ref_audio_path`` instead.
    Column("ref_audio", LargeBinary, nullable=True),
    # Basename of the ref audio file inside ``{data_dir}/voice_refs/``.
    # Storing the basename (not an absolute path) so the deployment
    # root can move without a DB migration.
    Column("ref_audio_path", String(128), nullable=True),
    Column("ref_audio_mime", String(32), nullable=False, default="audio/wav"),
    Column("duration_s", Float, nullable=False, default=0.0),
    Column("size_bytes", Integer, nullable=False, default=0),
    Column(
        "created_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
)


# ── voice_bindings ────────────────────────────────────────────────────────
# Global (site-wide) vrm_file → voice_profile mapping. Mirrors the mocap
# pattern: one row per VRM character, admin-editable from the /voice page,
# broadcast via the ``voice.bindings.updated`` WS event so every live
# viewer picks up the new voice without reconnecting.
voice_bindings = Table(
    "voice_bindings",
    metadata,
    Column("vrm_file", String(64), primary_key=True),
    Column(
        "profile_id",
        String(36),
        ForeignKey("voice_profiles.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    ),
    Column(
        "updated_by",
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    ),
    Column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    ),
)
