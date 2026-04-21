"""Users table + CRUD helpers for the authentication system.

Defines the ``users`` table on the shared ``metadata`` from
``autonoma.db.schema`` so it participates in the existing migration flow
(``init_db`` runs ``create_all(checkfirst=True)``). That means a fresh DB
gets the table automatically and an existing DB gets it added the next
time ``init_db`` runs — no extra migration wiring needed.

The API is intentionally minimal:

- ``create_user(username, password_hash)`` → new pending user.
- ``get_user_by_id`` / ``get_user_by_username`` → lookup helpers.
- ``list_users`` → admin console list.
- ``update_user_status`` → used by the approve / deny / disable /
  reactivate flows.

All helpers are async and use the shared engine from ``db.engine``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import (
    Column,
    DateTime,
    String,
    Table,
    func,
    insert,
    select,
    update,
)

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import metadata

UserRole = Literal["admin", "user"]
UserStatus = Literal["pending", "active", "disabled"]


# ── users table ───────────────────────────────────────────────────────
users = Table(
    "users",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("username", String(32), nullable=False, unique=True, index=True),
    Column("password_hash", String(255), nullable=False),
    Column("role", String(16), nullable=False, default="user"),
    Column("status", String(16), nullable=False, default="pending"),
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


@dataclass
class User:
    """In-memory projection of a ``users`` row.

    Excludes nothing — including ``password_hash`` — because all the
    callers that reach this layer are trusted (FastAPI deps after auth).
    The public API layer is responsible for scrubbing ``password_hash``
    before sending a user payload to the wire.
    """

    id: str
    username: str
    password_hash: str
    role: UserRole
    status: UserStatus
    created_at: datetime
    updated_at: datetime

    def public_dict(self) -> dict:
        """Return a dict safe to send to clients (no password hash)."""
        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def _row_to_user(row) -> User:
    return User(
        id=row["id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=row["role"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── CRUD helpers ──────────────────────────────────────────────────────


async def create_user(
    *,
    username: str,
    password_hash: str,
    role: UserRole = "user",
    status: UserStatus = "pending",
) -> User:
    """Insert a user row. Caller must ensure the username is unique or be
    prepared to handle an IntegrityError."""
    await init_db()
    engine = get_engine()
    now = datetime.now(UTC)
    user_id = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            insert(users).values(
                id=user_id,
                username=username,
                password_hash=password_hash,
                role=role,
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
    return User(
        id=user_id,
        username=username,
        password_hash=password_hash,
        role=role,
        status=status,
        created_at=now,
        updated_at=now,
    )


async def get_user_by_id(user_id: str) -> User | None:
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(users).where(users.c.id == user_id))
        ).mappings().first()
    return _row_to_user(row) if row else None


async def get_user_by_username(username: str) -> User | None:
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(select(users).where(users.c.username == username))
        ).mappings().first()
    return _row_to_user(row) if row else None


async def list_users() -> list[User]:
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(select(users).order_by(users.c.created_at))
        ).mappings().all()
    return [_row_to_user(r) for r in rows]


async def update_user_status(user_id: str, status: UserStatus) -> User | None:
    """Set a user's status and bump ``updated_at``. Returns the updated
    user, or None if no row matched."""
    await init_db()
    engine = get_engine()
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        result = await conn.execute(
            update(users)
            .where(users.c.id == user_id)
            .values(status=status, updated_at=now)
        )
        if result.rowcount == 0:
            return None
    return await get_user_by_id(user_id)
