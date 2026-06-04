from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fund_estimator.services.lof_notifications import AFTERNOON_CHECK_TIME, AFTERNOON_CHECK_WINDOW_SECONDS, LofNoticeService, MARKET_TZ, read_json


logger = logging.getLogger(__name__)


class LofDailyNoticeScheduler:
    def __init__(
        self,
        *,
        monitor: Any,
        notice: LofNoticeService,
        normal_threshold_pct: float = 2.0,
        strong_threshold_pct: float = 5.0,
        min_turnover_yuan: float = 3_000_000,
        limit: int = 120,
        send_empty: bool = True,
    ) -> None:
        self.monitor = monitor
        self.notice = notice
        self.normal_threshold_pct = normal_threshold_pct
        self.strong_threshold_pct = strong_threshold_pct
        self.min_turnover_yuan = min_turnover_yuan
        self.limit = limit
        self.send_empty = send_empty
        self._task: asyncio.Task[None] | None = None
        self._wake_event: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._wake_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._task = None
        self._wake_event = None

    def wake(self) -> None:
        if self._wake_event is not None:
            self._wake_event.set()

    async def _loop(self) -> None:
        while True:
            delay = self.seconds_until_next_run()
            event = self._wake_event
            if event is None:
                await asyncio.sleep(delay)
                continue
            try:
                await asyncio.wait_for(event.wait(), timeout=delay)
            except TimeoutError:
                try:
                    await self.run_once()
                except Exception:
                    logger.exception("LOF daily notice scheduler failed")
            else:
                event.clear()

    def seconds_until_next_run(self, now: datetime | None = None) -> float:
        now = now or datetime.now(UTC)
        state = read_json(self.notice.config.state_path, {})
        if not self.notice.effective_enabled(state):
            return 24 * 60 * 60

        local_now = now.astimezone(MARKET_TZ)
        daily_time = self.notice.effective_daily_summary_time(state)
        checks = (
            (daily_time, "last_daily_summary_date"),
            (AFTERNOON_CHECK_TIME, "last_afternoon_check_date"),
        )
        best_delay: float | None = None

        for offset in range(8):
            candidate_date = local_now.date() + timedelta(days=offset)
            if candidate_date.weekday() >= 5:
                continue
            date_text = candidate_date.isoformat()
            for hhmm, state_key in checks:
                if str(state.get(state_key) or "") == date_text:
                    continue
                candidate_at = datetime.combine(candidate_date, self.notice._parse_hhmm(hhmm), tzinfo=MARKET_TZ)
                if state_key == "last_afternoon_check_date":
                    deadline = candidate_at + timedelta(seconds=AFTERNOON_CHECK_WINDOW_SECONDS)
                    if candidate_at <= local_now <= deadline:
                        return 0
                    if deadline < local_now:
                        continue
                elif candidate_at <= local_now:
                    return 0
                delay = max(0.0, (candidate_at.astimezone(UTC) - now).total_seconds())
                best_delay = delay if best_delay is None else min(best_delay, delay)
            if best_delay is not None:
                return best_delay
        return 24 * 60 * 60

    async def run_once(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        due_daily = self.notice.should_run_daily_summary(now)
        due_afternoon = self.notice.should_run_afternoon_check(now)
        if not due_daily and not due_afternoon:
            return {"notice": {"status": "skipped_notice_schedule"}}

        response = await self.monitor.get_opportunities(
            normal_threshold_pct=self.normal_threshold_pct,
            strong_threshold_pct=self.strong_threshold_pct,
            min_turnover_yuan=self.min_turnover_yuan,
            limit=self.limit,
            refresh=True,
        )
        notice_result: dict[str, Any] = {"status": "skipped_daily_summary_schedule"}
        new_issue_notice: dict[str, Any] | None = None
        afternoon_notice: dict[str, Any] | None = None
        if due_daily:
            notice_result = self.notice.notify_daily_summary(
                response,
                now=now,
                send_empty=self.send_empty,
            )
            if notice_result.get("status") == "sent":
                new_issue_notice = await self.notice.notify_new_issue_reminder(now=now)
        if due_afternoon:
            afternoon_notice = self.notice.notify_afternoon_check(response, now=now)
        result = {
            "scan": response.model_dump(mode="json"),
            "notice": notice_result,
            "afternoon_notice": afternoon_notice,
            "new_issue_notice": new_issue_notice,
        }
        logger.info("LOF daily notice scheduler result: %s", result)
        return result
