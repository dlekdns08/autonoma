"""Persona breeding — feature #13.

Take two parent personas and produce a "child" persona by mixing
their seed strings, tags, and prompt_style. The child carries a
``parent_persona_ids`` lineage list so the marketplace UI can render
genealogy.

The seed combination algorithm is intentionally deterministic: same
parents + same salt → same child seed_string. This lets the UI offer
a "preview" without committing the row, and lets tests assert on
exact values.

LLM is optional. When an llm_client is provided we ask it to fuse
the two parents' ``prompt_style`` into a 1-2 sentence blended voice.
When omitted we just join the parents' styles with a ``\\n---\\n``
separator so callers get something readable.

We deliberately do NOT import ``autonoma.routers.personas`` — that
file is shared, and re-importing its helpers would create a churn
target. The persona row is inserted directly via the schema table,
mirroring the pattern in routers/personas.py without coupling.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import insert, select

from autonoma.db.engine import get_engine, init_db
from autonoma.db.schema import personas

logger = logging.getLogger(__name__)


PERSONA_BUNDLE_VERSION = "1"


def breed_seed_strings(seed_a: str, seed_b: str, salt: str = "") -> str:
    """Deterministically combine two seed strings into a child seed.

    Algorithm
    ─────────
    1. Split each parent seed by ``:`` into segments.
    2. Interleave segments — take from ``seed_a`` then ``seed_b`` in
       round-robin order, preserving discovery of unique segments.
       Duplicate segments (case-insensitive) are kept only once so a
       child seeded from ``coder:fox`` × ``coder:bear`` doesn't get
       a redundant ``coder`` slot.
    3. Hash the leftover (everything else + salt) with sha256 and
       append the first 8 hex chars as a final segment so two parents
       with overlapping prefixes still produce a distinct child.

    The result has the shape ``"seg1:seg2:...:childhash"``. Empty
    parent inputs degenerate gracefully — they contribute no segments
    but still feed the hash.
    """
    parts_a = [p for p in (seed_a or "").split(":") if p]
    parts_b = [p for p in (seed_b or "").split(":") if p]

    interleaved: list[str] = []
    seen: set[str] = set()
    max_len = max(len(parts_a), len(parts_b))
    for i in range(max_len):
        for source in (parts_a, parts_b):
            if i < len(source):
                seg = source[i]
                key = seg.lower()
                if key not in seen:
                    seen.add(key)
                    interleaved.append(seg)

    digest_input = f"{seed_a}|{seed_b}|{salt}".encode()
    child_hash = hashlib.sha256(digest_input).hexdigest()[:8]
    interleaved.append(child_hash)
    return ":".join(interleaved)


def merge_tags(tags_a: list[str], tags_b: list[str], max_tags: int = 6) -> list[str]:
    """Set union of two tag lists, preferring overlapping tags first.

    Tags shared between both parents go to the front (they're the
    "essential" traits of the lineage), then unique tags from each
    parent in their original order. Comparison is case-insensitive
    but the original casing of the first occurrence is preserved.

    The returned list is capped at ``max_tags`` to keep persona cards
    visually compact.
    """
    norm_a = {t.lower(): t for t in tags_a if isinstance(t, str) and t}
    norm_b = {t.lower(): t for t in tags_b if isinstance(t, str) and t}

    common_keys = [k for k in norm_a if k in norm_b]
    a_only = [k for k in norm_a if k not in norm_b]
    b_only = [k for k in norm_b if k not in norm_a]

    ordered: list[str] = []
    seen: set[str] = set()
    for source_keys, source_map in (
        (common_keys, norm_a),
        (a_only, norm_a),
        (b_only, norm_b),
    ):
        for k in source_keys:
            if k in seen:
                continue
            seen.add(k)
            ordered.append(source_map[k])
            if len(ordered) >= max_tags:
                return ordered
    return ordered


def _row_to_bundle(m: Any) -> dict[str, Any]:
    """Render a personas row as a JSON-friendly dict.

    Mirrors ``routers/personas._row_to_bundle`` shape but adds the
    ``parent_persona_ids`` lineage list so the breeding endpoint's
    response is self-contained.
    """
    try:
        tags = json.loads(m["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags = []
    try:
        parent_ids = json.loads(m["parent_persona_ids"] or "[]")
    except (json.JSONDecodeError, KeyError, TypeError):
        parent_ids = []
    return {
        "bundle_version": PERSONA_BUNDLE_VERSION,
        "id": m["id"],
        "name": m["name"],
        "role": m["role"],
        "seed_string": m["seed_string"],
        "voice_profile_id": m["voice_profile_id"],
        "vrm_file": m["vrm_file"],
        "prompt_style": m["prompt_style"],
        "tags": tags,
        "is_public": bool(m["is_public"]),
        "download_count": int(m["download_count"] or 0),
        "parent_persona_ids": parent_ids,
        "created_at": str(m["created_at"]),
        "updated_at": str(m["updated_at"]),
    }


def _blend_prompt_style_offline(style_a: str, style_b: str) -> str:
    """Default prompt_style merger when no LLM is available.

    Just concatenates with a separator. Empty inputs are skipped so
    we don't emit dangling ``---`` markers.
    """
    a = (style_a or "").strip()
    b = (style_b or "").strip()
    if a and b:
        return f"{a}\n---\n{b}"
    return a or b


async def _blend_prompt_style_llm(style_a: str, style_b: str, llm_client: Any) -> str:
    """Ask the LLM to fuse two prompt_style strings into one voice.

    Falls back to the offline blend if the LLM call fails or returns
    empty text — breeding should never hard-fail on an LLM hiccup.
    """
    a = (style_a or "").strip()
    b = (style_b or "").strip()
    if not a and not b:
        return ""
    if not a or not b:
        return a or b
    system = (
        "You blend two AI agent persona voices into one new voice. "
        "Reply with 1-2 short sentences describing the blended tone. "
        "No preamble, no quotes."
    )
    user_msg = f"Parent A voice:\n{a}\n\nParent B voice:\n{b}\n\nWrite the blended child's voice."
    try:
        # ``BaseLLMClient.create`` expects model/max_tokens/temperature.
        # Sensible small defaults — this is a one-off blending call.
        response = await llm_client.create(
            model=getattr(llm_client, "model", "claude-haiku-4-5"),
            max_tokens=200,
            temperature=0.7,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = (response.text or "").strip()
        if text:
            return text
    except Exception as exc:  # noqa: BLE001 — we deliberately swallow
        logger.warning("breed_personas: LLM blend failed (%s); using offline merge", exc)
    return _blend_prompt_style_offline(a, b)


async def breed_personas(
    parent_a_id: str,
    parent_b_id: str,
    child_name: str,
    owner_user_id: str,
    llm_client: Any = None,
) -> dict[str, Any]:
    """Create a child persona from two parents.

    The child:

    * inherits a deterministic ``seed_string`` (see
      :func:`breed_seed_strings`)
    * inherits the merged tag set (see :func:`merge_tags`)
    * inherits a blended ``prompt_style`` — LLM-fused if a client is
      provided, otherwise concatenated with a ``\\n---\\n`` separator
    * picks ``role`` from parent A (parent A is "dominant" — the UI
      should put the parent the user clicked first as A)
    * starts private (``is_public=0``) under ``owner_user_id``
    * has ``parent_persona_ids = [parent_a_id, parent_b_id]``

    Raises
    ──────
    ValueError
        On same-parent breeding or when either parent row is missing.
        The router translates this into HTTP 400.
    """
    if parent_a_id == parent_b_id:
        raise ValueError("breed_same_parent")
    if not parent_a_id or not parent_b_id:
        raise ValueError("missing_parent")

    await init_db()
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(personas).where(personas.c.id.in_([parent_a_id, parent_b_id]))
            )
        ).all()
    by_id = {r._mapping["id"]: r._mapping for r in rows}
    parent_a = by_id.get(parent_a_id)
    parent_b = by_id.get(parent_b_id)
    if parent_a is None or parent_b is None:
        raise ValueError("parent_not_found")

    try:
        tags_a = json.loads(parent_a["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags_a = []
    try:
        tags_b = json.loads(parent_b["tags_json"] or "[]")
    except json.JSONDecodeError:
        tags_b = []
    merged_tags = merge_tags(tags_a, tags_b)

    # Salt the seed with parent ids so the same two seeds with swapped
    # parent rows still yield distinct children — keeps the hash tied
    # to row identity, not just raw seed text.
    salt = f"{parent_a_id}|{parent_b_id}"
    child_seed = breed_seed_strings(
        parent_a["seed_string"] or "",
        parent_b["seed_string"] or "",
        salt=salt,
    )

    if llm_client is None:
        blended_style = _blend_prompt_style_offline(
            parent_a["prompt_style"], parent_b["prompt_style"]
        )
    else:
        blended_style = await _blend_prompt_style_llm(
            parent_a["prompt_style"], parent_b["prompt_style"], llm_client
        )

    child_id = str(uuid.uuid4())
    parent_lineage = [parent_a_id, parent_b_id]

    # Pull voice/vrm preferentially from parent A — children "inherit"
    # the dominant parent's appearance until the user customizes.
    voice_profile_id = parent_a["voice_profile_id"] or parent_b["voice_profile_id"]
    vrm_file = parent_a["vrm_file"] or parent_b["vrm_file"] or ""

    async with engine.begin() as conn:
        await conn.execute(
            insert(personas).values(
                id=child_id,
                owner_user_id=owner_user_id,
                name=child_name,
                role=parent_a["role"] or "coder",
                seed_string=child_seed,
                voice_profile_id=voice_profile_id,
                vrm_file=vrm_file,
                prompt_style=blended_style,
                tags_json=json.dumps(merged_tags),
                is_public=0,
                parent_persona_ids=json.dumps(parent_lineage),
            )
        )
        new_row = (await conn.execute(select(personas).where(personas.c.id == child_id))).first()

    if new_row is None:  # pragma: no cover — defensive
        raise RuntimeError("breed_personas: failed to read inserted row")
    return _row_to_bundle(new_row._mapping)


# ``Counter`` is imported for callers that want to weight tags by
# combined frequency (UI ranking helpers); keep the symbol exported
# even though merge_tags doesn't currently use it.
__all__ = [
    "PERSONA_BUNDLE_VERSION",
    "breed_personas",
    "breed_seed_strings",
    "merge_tags",
    "Counter",
]
