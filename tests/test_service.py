from datetime import date, datetime

import pytest

from courtwatch.config import Config
from courtwatch.courts.base import CourtAdapter, CourtError
from courtwatch.db import Database, normalize_phone
from courtwatch.models import CaseStatus, TrackedCase
from courtwatch.notify import Notifier
from courtwatch.service import CaseWatcher


class FakeCourt(CourtAdapter):
    key, name = "jkhc", "High Court of J&K"

    def __init__(self, statuses):
        self.statuses = statuses  # case_no -> CaseStatus | Exception
        self.fetched = []

    def benches(self):
        return {}

    def case_types(self, bench):
        return {}

    def search(self, *a):
        return []

    def fetch_status(self, case):
        self.fetched.append(case.case_no)
        result = self.statuses[case.case_no]
        if isinstance(result, Exception):
            raise result
        return result


class RecordingNotifier(Notifier):
    channel = "test"

    def __init__(self, fail_for=()):
        self.sent = []
        self.fail_for = set(fail_for)

    def send(self, reminder):
        if reminder.advocate.phone in self.fail_for:
            raise RuntimeError("boom")
        self.sent.append(reminder)
        return f"msg{len(self.sent)}"


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def setup(db, statuses, notifier=None):
    court = FakeCourt(statuses)
    watcher = CaseWatcher(Config(), db, notifier, court_factory=lambda key, cfg: court)
    return watcher, court


def add(db, phone, number, name="Adv"):
    adv = db.upsert_advocate(name, phone)
    return db.add_case(TrackedCase(None, adv.id, "jkhc", "jammu", "WP(C)", number, 2023, label="client"))


TODAY = date(2026, 9, 28)
TOMORROW = date(2026, 9, 29)


def test_normalize_phone():
    assert normalize_phone("98765 43210") == "919876543210"
    assert normalize_phone("+91-98765-43210") == "919876543210"
    assert normalize_phone("098765 43210") == "919876543210"
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_run_daily_refreshes_then_reminds_only_tomorrow(db):
    add(db, "9000000001", "1", "Adv One")
    add(db, "9000000001", "2", "Adv One")
    add(db, "9000000002", "3", "Adv Two")
    statuses = {
        "1": CaseStatus(cnr="C1", internal_no="I1", next_hearing=TOMORROW, stage="ADMISSION",
                        petitioner="A", respondent="B"),
        "2": CaseStatus(cnr="C2", internal_no="I2", next_hearing=date(2026, 10, 5)),
        "3": CaseStatus(cnr="C3", internal_no="I3", next_hearing=TOMORROW),
    }
    notifier = RecordingNotifier()
    watcher, _ = setup(db, statuses, notifier)

    report = watcher.run_daily(today=TODAY)

    assert report.refreshed == 3 and report.sent == 2
    assert sorted(r.case.case_no for r in notifier.sent) == ["1", "3"]
    first = next(r for r in notifier.sent if r.case.case_no == "1")
    assert first.params()[2] == "A vs B"
    assert first.params()[4] == "Tue, 29 Sep 2026"
    stored = db.get_case(first.case.id)
    assert stored.cnr == "C1" and stored.internal_no == "I1"

    # Second run the same day: nothing re-fetched (fresh) and nothing re-sent.
    report2 = watcher.run_daily(today=TODAY)
    assert report2.refreshed == 0 and report2.sent == 0 and len(notifier.sent) == 2


def test_adjourned_case_gets_no_stale_reminder(db):
    case = add(db, "9000000001", "1")
    case.next_hearing = TOMORROW
    db.save_case(case)
    watcher, _ = setup(db, {"1": CaseStatus(next_hearing=date(2026, 11, 2))}, RecordingNotifier())
    assert watcher.run_daily(today=TODAY).sent == 0


def test_disposed_case_not_refreshed_or_reminded(db):
    case = add(db, "9000000001", "1")
    case.disposed, case.next_hearing = True, TOMORROW
    db.save_case(case)
    notifier = RecordingNotifier()
    watcher, court = setup(db, {}, notifier)
    watcher.run_daily(today=TODAY)
    assert court.fetched == [] and notifier.sent == []


def test_refresh_error_is_recorded_and_does_not_stop_others(db):
    add(db, "9000000001", "1")
    add(db, "9000000002", "2")
    watcher, _ = setup(db, {"1": CourtError("site down"), "2": CaseStatus(next_hearing=TOMORROW)},
                       RecordingNotifier())
    report = watcher.run_daily(today=TODAY)
    assert len(report.failed) == 1 and report.sent == 1
    failed = [c for c in db.list_cases() if c.case_no == "1"][0]
    assert failed.last_error == "site down"


def test_failed_send_is_retried_next_run(db):
    add(db, "9000000001", "1")
    notifier = RecordingNotifier(fail_for={"919000000001"})
    watcher, _ = setup(db, {"1": CaseStatus(next_hearing=TOMORROW)}, notifier)
    assert len(watcher.run_daily(today=TODAY).send_failed) == 1
    notifier.fail_for.clear()
    assert watcher.send_reminders(today=TODAY).sent == 1
