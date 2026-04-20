"""Persistent character database.

Characters, graveyard, wills, and cross-session relationships survive here
so an agent who was born in run N can show up in run N+M remembering what
happened. Single SQLite file (WAL mode), mounted to a named Docker volume
in production.
"""

from autonoma.db.engine import get_engine, init_db, dispose_engine
from autonoma.db.schema import (
    characters,
    projects,
    project_participants,
    character_stats_history,
    relationships as relationships_table,
    graveyard,
    wills,
    famous_quotes,
)

__all__ = [
    "get_engine",
    "init_db",
    "dispose_engine",
    "characters",
    "projects",
    "project_participants",
    "character_stats_history",
    "relationships_table",
    "graveyard",
    "wills",
    "famous_quotes",
]
