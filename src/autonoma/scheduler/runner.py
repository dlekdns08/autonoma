"""Background scheduler that ticks once a minute and fires due schedules.

Wiring point: ``api.py`` calls ``scheduler_runner.start()`` from its
startup hook. The runner is idempotent — calling ``start`` twice is a
no-op, and ``stop`` cleanly cancels the loop.

For Phase 4-A we emit ``schedule.fire_requested`` on the bus when a
schedule is due. The actual headless swarm spawn (creating a virtual
session, running ``_run_swarm`` without a WebSocket) is a separate
piece of work — gating it behind an event keeps this module reusable
once that lands.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from autonoma.event_bus import bus
from autonoma.scheduler.store import schedule_store

logger = logging.getLogger(__name__)


# Once-per-minute polling. Keep tight — the wakeup is cheap and missed
# fires are unrecoverable until the next day.
_POLL_INTERVAL_SECONDS = 60.0


class SchedulerRunner:
    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self.running:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._loop(), name="scheduler-loop")
        logger.info("[scheduler] runner started (poll=60s)")

    async def stop(self) -> None:
        if not self.running:
            return
        self._stopping.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover — defensive
                logger.exception("[scheduler] task raised during shutdown")
        logger.info("[scheduler] runner stopped")

    async def fire_now(self, owner: str, sched_id: str) -> bool:
        """Manually trigger a schedule, bypassing the time check.

        Returns True iff the schedule existed and was enabled. Updates
        ``last_fired_at`` so the daily-suppression logic works the same
        way as automatic fires.
        """
        try:
            schedule = schedule_store.get(owner, sched_id)
        except Exception:
            logger.warning(
                "[scheduler] fire_now failed to load schedule owner=%s sched_id=%s",
                owner, sched_id, exc_info=True,
            )
            return False
        if not schedule.enabled:
            return False
        await self._fire(schedule, when=datetime.now(timezone.utc), reason="manual")
        return True

    # ── Loop ─────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover — runner must survive
                logger.exception("[scheduler] tick failed")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=_POLL_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        for schedule in schedule_store.iter_all():
            if not schedule.is_due(now):
                continue
            await self._fire(schedule, when=now, reason="cron")

    async def _fire(self, schedule, when: datetime, reason: str) -> None:
        started = time.monotonic()
        logger.info(
            f"[scheduler] firing schedule={schedule.id} owner={schedule.owner_user_id} "
            f"reason={reason} goal={schedule.goal[:60]!r}"
        )
        # Persist the fire timestamp first so back-to-back ticks don't
        # double-fire if the bus listener takes >60s to complete.
        schedule.mark_fired(when)
        try:
            schedule_store.save(schedule)
        except Exception as exc:
            logger.warning(
                f"[scheduler] failed to persist fire timestamp: {exc}"
            )

        await bus.emit(
            "schedule.fire_requested",
            schedule_id=schedule.id,
            owner=schedule.owner_user_id,
            goal=schedule.goal,
            preset_id=schedule.preset_id,
            name=schedule.name,
            reason=reason,
        )
        elapsed = time.monotonic() - started
        logger.info(
            "[scheduler] fired schedule=%s reason=%s in %.3fs",
            schedule.id, reason, elapsed,
        )


scheduler_runner = SchedulerRunner()
