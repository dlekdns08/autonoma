"""Searchable index over agent diary entries.

Phase 0-D scaffolding for the Phase 1 diary RAG: every diary entry an
agent writes is also indexed here so the prompt builder can pull the top
N most relevant past entries when an agent is about to ``decide``.

The default backend is a keyword index (token-set with IDF weighting).
That's good enough for ~hundreds of entries per agent and has zero
external dependencies. The :py:class:`DiarySearchIndex` interface keeps
``add`` / ``search`` decoupled from the scoring algorithm so swapping in
a vector backend (sqlite-vec, faiss, etc.) later is a one-file change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from autonoma.world.inner_life import DiaryEntry
    from autonoma.world.personality import Mood


# Korean + English friendly tokenizer: split on whitespace and any
# non-letter / non-digit Unicode code point. Keeps Hangul syllables
# (가-힣) and Latin/CJK characters intact.
_TOKEN_RE = re.compile(r"[A-Za-z0-9À-ɏ가-힣]+")

# Tiny English/Korean stopword list — diary text is short enough that
# heavy stopword filtering hurts more than it helps. Trim aggressively.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "is",
        "was", "were", "be", "with", "for", "it", "its", "this", "that",
        "i", "we", "you", "they", "my", "our", "your", "their",
        "은", "는", "이", "가", "을", "를", "에", "도", "와", "과",
    }
)


def _tokenize(text: str) -> list[str]:
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if len(t) > 1 and t.lower() not in _STOPWORDS
    ]


@dataclass
class _IndexedEntry:
    agent: str
    round_number: int
    mood: str
    content: str
    weather: str = ""
    time_of_day: str = ""
    tokens: set[str] = field(default_factory=set)


class DiarySearchIndex:
    """In-memory keyword index over diary entries.

    Thread-safety: not safe for concurrent writers. Diary writes happen
    on the swarm's single event loop, so adding/searching is sequential
    and locks would only add latency.
    """

    def __init__(self) -> None:
        self._entries: list[_IndexedEntry] = []
        # token -> set of entry indices
        self._postings: dict[str, set[int]] = {}

    # ── Mutation ──────────────────────────────────────────────────────

    def add_entry(
        self,
        *,
        agent: str,
        round_number: int,
        mood: str,
        content: str,
        weather: str = "",
        time_of_day: str = "",
    ) -> None:
        tokens = set(_tokenize(content))
        idx = len(self._entries)
        self._entries.append(
            _IndexedEntry(
                agent=agent,
                round_number=round_number,
                mood=mood,
                content=content,
                weather=weather,
                time_of_day=time_of_day,
                tokens=tokens,
            )
        )
        for tok in tokens:
            self._postings.setdefault(tok, set()).add(idx)

    def add_diary_entry(self, agent: str, entry: "DiaryEntry") -> None:
        # Convenience: mirror in-place writes from AgentDiary.
        from autonoma.world.personality import Mood

        mood_str = entry.mood.value if isinstance(entry.mood, Mood) else str(entry.mood)
        self.add_entry(
            agent=agent,
            round_number=entry.round_number,
            mood=mood_str,
            content=entry.content,
            weather=entry.weather,
            time_of_day=entry.time_of_day,
        )

    def clear(self) -> None:
        self._entries.clear()
        self._postings.clear()

    # ── Read-only views ───────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._entries)

    def for_agent(self, agent: str) -> list[_IndexedEntry]:
        return [e for e in self._entries if e.agent == agent]

    # ── Search ────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        *,
        agent: str | None = None,
        top_n: int = 3,
        recency_boost: float = 0.0,
    ) -> list[tuple[_IndexedEntry, float]]:
        """Return the top-``top_n`` most relevant entries for ``query``.

        ``agent`` filters to a specific diarist (typical RAG case: pull
        the *speaker's own* memories). ``recency_boost`` adds
        ``recency_boost * (round_number / max_round)`` to each score so
        callers can favour fresh memories without recency dominating.
        """
        tokens = _tokenize(query)
        if not tokens or not self._entries:
            return []

        # IDF over the full corpus (not the agent slice) so common words
        # are still down-weighted when one agent has only a handful of
        # entries. ``df`` is the document frequency of the token.
        n_docs = len(self._entries)
        idf: dict[str, float] = {}
        for tok in set(tokens):
            df = len(self._postings.get(tok, ()))
            if df == 0:
                continue
            idf[tok] = math.log((n_docs + 1) / (df + 1)) + 1.0

        if not idf:
            return []

        # Restrict candidates to entries that share at least one query
        # token. This keeps search O(matched_docs) instead of O(N).
        candidate_ids: set[int] = set()
        for tok in idf:
            candidate_ids.update(self._postings.get(tok, set()))

        max_round = max(e.round_number for e in self._entries) or 1

        scored: list[tuple[_IndexedEntry, float]] = []
        for idx in candidate_ids:
            entry = self._entries[idx]
            if agent is not None and entry.agent != agent:
                continue
            overlap = entry.tokens & idf.keys()
            if not overlap:
                continue
            score = sum(idf[t] for t in overlap)
            # Length normalisation — diary entries are short so the
            # square-root taper is gentle.
            score /= math.sqrt(max(1, len(entry.tokens)))
            if recency_boost:
                score += recency_boost * (entry.round_number / max_round)
            scored.append((entry, score))

        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_n]

    def search_text(self, query: str, *, agent: str | None = None, top_n: int = 3) -> list[str]:
        """Convenience wrapper that returns formatted ``"R{round}: text"`` lines."""
        results = self.search(query, agent=agent, top_n=top_n)
        return [f"R{e.round_number} ({e.mood}): {e.content}" for e, _ in results]

    def filter_by_mood(
        self,
        mood: str | "Mood",
        *,
        agent: str | None = None,
        top_n: int = 5,
    ) -> list[_IndexedEntry]:
        target = getattr(mood, "value", mood)
        out = [
            e
            for e in reversed(self._entries)  # latest first
            if e.mood == target and (agent is None or e.agent == agent)
        ]
        return out[:top_n]


# Module-level singleton: shared by all agents in the same process.
# Tests can build their own via ``DiarySearchIndex()``.
diary_index = DiarySearchIndex()


def index_existing_diaries(diaries: Iterable[tuple[str, "list[DiaryEntry]"]]) -> None:
    """Bulk-load entries from already-constructed diaries."""
    for agent_name, entries in diaries:
        for entry in entries:
            diary_index.add_diary_entry(agent_name, entry)
