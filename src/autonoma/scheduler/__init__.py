"""Scheduled-run support — Phase 4-A.

Manage recurring "run this goal at 02:00 every night" entries.

Storage: JSON files under ``settings.data_dir/schedules/{owner}/``.
Granularity: a single ``daily HH:MM UTC`` window — enough for
overnight CI-style runs and trivial to reason about. A future patch
can add `cron` support if needed; we model the field even now so the
storage format won't churn.

Firing semantics: every minute, the poll loop walks all enabled
schedules and emits ``schedule.fire_requested`` on the bus when a
schedule's window matches and ``last_fired_at`` is older than 23h.
The actual headless swarm spawn is intentionally NOT wired here —
the swarm-runner currently expects a WebSocket session, so plumbing
that in cleanly is a separate piece of work. For now, host operators
get the full schedule CRUD + a manual fire action; UIs and bots can
listen for the ``schedule.fire_requested`` event.
"""

from autonoma.scheduler.model import Schedule, ScheduleNotFound
from autonoma.scheduler.store import schedule_store, ScheduleStore
from autonoma.scheduler.runner import SchedulerRunner, scheduler_runner

__all__ = [
    "Schedule",
    "ScheduleNotFound",
    "ScheduleStore",
    "schedule_store",
    "SchedulerRunner",
    "scheduler_runner",
]
