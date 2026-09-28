"""The daily job: refresh every tracked case, then remind advocates of tomorrow's hearings."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable

from .config import Config
from .courts import CourtAdapter, CourtError, get_court
from .db import Database
from .models import CaseStatus, TrackedCase
from .notify import Notifier, Reminder

log = logging.getLogger(__name__)


@dataclass
class RunReport:
    refreshed: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    sent: int = 0
    send_failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"refreshed {self.refreshed}, up to date {self.skipped}, refresh failures {len(self.failed)}",
            f"reminders sent {self.sent}, send failures {len(self.send_failed)}",
        ]
        lines += [f"  refresh error: {e}" for e in self.failed]
        lines += [f"  send error: {e}" for e in self.send_failed]
        return "\n".join(lines)


class CaseWatcher:
    def __init__(
        self,
        config: Config,
        db: Database,
        notifier: Notifier | None = None,
        court_factory: Callable[[str, Config], CourtAdapter] = get_court,
    ):
        self.config = config
        self.db = db
        self.notifier = notifier
        self._court_factory = court_factory
        self._courts: dict[str, CourtAdapter] = {}

    def court(self, key: str) -> CourtAdapter:
        if key not in self._courts:
            self._courts[key] = self._court_factory(key, self.config)
        return self._courts[key]

    def now(self) -> datetime:
        return datetime.now(self.config.timezone).replace(tzinfo=None)

    # ---- refresh ---------------------------------------------------------

    def refresh_case(self, case: TrackedCase) -> TrackedCase:
        """Fetch the latest status from the court and store it. Raises CourtError."""
        try:
            status = self.court(case.court).fetch_status(case)
        except CourtError as exc:
            case.last_error = str(exc)
            case.last_checked_at = self.now()
            self.db.save_case(case)
            raise
        apply_status(case, status)
        case.last_checked_at = self.now()
        case.last_error = None
        self.db.save_case(case)
        return case

    def refresh_all(self, force: bool = False, report: RunReport | None = None) -> RunReport:
        report = report or RunReport()
        stale_before = self.now() - timedelta(hours=self.config.refresh_after_hours)
        for case in self.db.list_cases(include_disposed=False):
            fresh = case.last_checked_at and case.last_checked_at > stale_before and not case.last_error
            if fresh and not force:
                report.skipped += 1
                continue
            try:
                self.refresh_case(case)
                report.refreshed += 1
            except CourtError as exc:
                log.warning("refresh failed for case %s: %s", case.id, exc)
                report.failed.append(f"#{case.id} {case.display_number}: {exc}")
        return report

    # ---- notify ----------------------------------------------------------

    def send_reminders(self, today: date | None = None, report: RunReport | None = None) -> RunReport:
        if self.notifier is None:
            raise RuntimeError("No notifier configured")
        report = report or RunReport()
        today = today or self.now().date()
        target = today + timedelta(days=self.config.notify_days_before)
        for case in self.db.cases_with_hearing_on(target):
            if self.db.already_notified(case.id, target):
                continue
            advocate = self.db.get_advocate(case.advocate_id)
            reminder = Reminder(advocate, case, target, self.court(case.court).name)
            try:
                ref = self.notifier.send(reminder)
            except Exception as exc:  # keep going for other advocates
                log.warning("send failed for case %s: %s", case.id, exc)
                report.send_failed.append(f"#{case.id} to +{advocate.phone}: {exc}")
                continue
            self.db.record_notification(case.id, target, self.notifier.channel, ref)
            report.sent += 1
        return report

    def run_daily(self, today: date | None = None) -> RunReport:
        # Always refresh first: dates change when matters are adjourned or re-listed.
        report = self.refresh_all()
        return self.send_reminders(today=today, report=report)


def apply_status(case: TrackedCase, status: CaseStatus) -> None:
    case.cnr = status.cnr or case.cnr
    case.internal_no = status.internal_no or case.internal_no
    case.case_type_name = status.case_type_name or case.case_type_name
    case.title = status.title or case.title
    case.stage = status.stage
    case.coram = status.coram
    case.next_hearing = status.next_hearing
    case.last_hearing = status.last_hearing or case.last_hearing
    case.disposed = status.disposed
