"""harness_policies table + CRUD helpers.

Per-user preset storage for the Harness Engineering UI. The policy body
is serialized as a JSON blob in the ``content`` column — the shape lives
in ``autonoma.harness.policy.HarnessPolicyContent`` and is validated by
Pydantic on write and on read.

The system-wide ``default`` preset has ``is_default=True`` and
``owner_user_id=NULL``; it's seeded once at startup via
``ensure_default_policy`` and can never be deleted or renamed.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    func,
    insert,
    or_,
    select,
    update as sa_update,
    delete as sa_delete,
)

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import metadata
from autonoma.harness.policy import (
    HarnessPolicy,
    HarnessPolicyContent,
    default_policy_content,
)

DEFAULT_POLICY_NAME = "default"


# ── table ─────────────────────────────────────────────────────────────
harness_policies = Table(
    "harness_policies",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "owner_user_id",
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    ),
    Column("name", String(64), nullable=False),
    Column("is_default", Boolean, nullable=False, default=False),
    Column("content", Text, nullable=False),
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
    Index("idx_harness_policies_owner", "owner_user_id"),
)


# ── helpers ───────────────────────────────────────────────────────────


def _row_to_policy(row) -> HarnessPolicy:
    return HarnessPolicy(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        name=row["name"],
        is_default=bool(row["is_default"]),
        content=HarnessPolicyContent.model_validate_json(row["content"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _content_json(content: HarnessPolicyContent) -> str:
    return content.model_dump_json()


# ── CRUD ──────────────────────────────────────────────────────────────


async def create_policy(
    *,
    owner_user_id: str | None,
    name: str,
    content: HarnessPolicyContent | None = None,
    is_default: bool = False,
) -> HarnessPolicy:
    """Insert a preset row. Caller must enforce any uniqueness rules."""
    await init_db()
    engine = get_engine()
    content = content or default_policy_content()
    now = datetime.now(UTC)
    policy_id = str(uuid.uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            insert(harness_policies).values(
                id=policy_id,
                owner_user_id=owner_user_id,
                name=name,
                is_default=is_default,
                content=_content_json(content),
                created_at=now,
                updated_at=now,
            )
        )
    return HarnessPolicy(
        id=policy_id,
        owner_user_id=owner_user_id,
        name=name,
        is_default=is_default,
        content=content,
        created_at=now,
        updated_at=now,
    )


async def get_policy_by_id(policy_id: str) -> HarnessPolicy | None:
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(harness_policies).where(harness_policies.c.id == policy_id)
            )
        ).mappings().first()
    return _row_to_policy(row) if row else None


async def get_default_policy() -> HarnessPolicy | None:
    """Return the lone system default preset, or None if not yet seeded."""
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                select(harness_policies)
                .where(harness_policies.c.is_default.is_(True))
                .limit(1)
            )
        ).mappings().first()
    return _row_to_policy(row) if row else None


async def list_policies_for_user(user_id: str) -> list[HarnessPolicy]:
    """Return the user's own presets plus the shared system default.

    The default preset is included so the UI can render it as a read-only
    baseline without a second round trip.
    """
    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(harness_policies)
                .where(
                    or_(
                        harness_policies.c.owner_user_id == user_id,
                        harness_policies.c.is_default.is_(True),
                    )
                )
                .order_by(
                    harness_policies.c.is_default.desc(),
                    harness_policies.c.created_at,
                )
            )
        ).mappings().all()
    return [_row_to_policy(r) for r in rows]


async def update_policy(
    policy_id: str,
    *,
    name: str | None = None,
    content: HarnessPolicyContent | None = None,
) -> HarnessPolicy | None:
    """Update name and/or content. Returns the updated record, or None if
    not found. Refuses to modify the default preset."""
    existing = await get_policy_by_id(policy_id)
    if existing is None:
        return None
    if existing.is_default:
        raise ValueError("default policy is read-only")
    values: dict = {"updated_at": datetime.now(UTC)}
    if name is not None:
        values["name"] = name
    if content is not None:
        values["content"] = _content_json(content)
    if len(values) == 1:
        # only updated_at would change — nothing to do
        return existing
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_update(harness_policies)
            .where(harness_policies.c.id == policy_id)
            .values(**values)
        )
    return await get_policy_by_id(policy_id)


async def delete_policy(policy_id: str) -> bool:
    """Delete a non-default preset. Returns True if a row was removed."""
    existing = await get_policy_by_id(policy_id)
    if existing is None:
        return False
    if existing.is_default:
        raise ValueError("default policy cannot be deleted")
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            sa_delete(harness_policies).where(harness_policies.c.id == policy_id)
        )
    return result.rowcount > 0


async def ensure_default_policy() -> HarnessPolicy:
    """Idempotently guarantee a system default preset exists and return it.

    Call once at application startup (after ``init_db``). If the default
    row is missing — first boot, or the DB was wiped — create it from the
    fresh ``default_policy_content()`` defaults.
    """
    existing = await get_default_policy()
    if existing is not None:
        return existing
    return await create_policy(
        owner_user_id=None,
        name=DEFAULT_POLICY_NAME,
        content=default_policy_content(),
        is_default=True,
    )
