"""Schedule data model."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class ScheduleNotFound(LookupError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_HHMM = re.compile(r"^\d{2}:\d{2}$")


class Schedule(BaseModel):
    """A single recurring run entry.

    The current implementation supports a single firing per day at a
    specific UTC ``HH:MM``. The shape allows future extension:

      * ``cron_expr`` — when set, takes precedence over ``daily_at_utc``.
      * ``timezone`` — currently always UTC; ``Asia/Seoul`` etc. can
        be honoured later by parsing through ``zoneinfo``.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    owner_user_id: str
    name: str = Field(default="Nightly Run", max_length=120)
    goal: str = Field(..., min_length=1, max_length=2000)
    preset_id: str = Field(default="")
    enabled: bool = True
    daily_at_utc: str = Field(default="02:00")
    cron_expr: str = Field(default="")
    timezone: str = Field(default="UTC")  # advisory until zoneinfo support lands
    last_fired_at: Optional[str] = None
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)

    @field_validator("daily_at_utc")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("daily_at_utc must be HH:MM (UTC, 24h)")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("daily_at_utc has out-of-range hours/minutes")
        return v

    def touch(self) -> None:
        self.updated_at = _now_iso()

    # ── Firing logic ─────────────────────────────────────────────────

    def is_due(self, now: datetime, *, window_minutes: int = 1) -> bool:
        """Return True if this schedule should fire at ``now``.

        ``window_minutes`` is the slack we accept on either side of the
        scheduled minute. The runner polls every minute, so a window of
        1 minute means "fire if we're inside the same minute".

        ``last_fired_at`` is consulted to suppress duplicate fires
        within a 23h window — the schedule fires once per UTC day.
        """
        if not self.enabled:
            return False
        if self.cron_expr:
            # Cron support deferred — anything with cron_expr never
            # fires through this path. Returning False is the safe
            # default until we add a parser.
            return False
        target_h, target_m = (int(p) for p in self.daily_at_utc.split(":"))
        delta_minutes = abs(
            (now.hour - target_h) * 60 + (now.minute - target_m)
        )
        if delta_minutes > window_minutes:
            return False
        if self.last_fired_at:
            try:
                last = datetime.fromisoformat(self.last_fired_at)
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                gap = now - last
                if gap.total_seconds() < 23 * 3600:
                    return False
            except ValueError:
                # Corrupt timestamp — treat as "never fired" rather than
                # blocking the schedule forever.
                pass
        return True

    def mark_fired(self, when: datetime) -> None:
        self.last_fired_at = when.astimezone(timezone.utc).isoformat()
        self.touch()
