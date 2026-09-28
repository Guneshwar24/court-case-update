import json
from datetime import date

import pytest

from courtwatch.courts.base import CaseNotFound, CourtError
from courtwatch.courts.ecourts_hc import ECourtsHighCourt
from courtwatch.models import TrackedCase


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text = text
        self.content = content or text.encode()

    def raise_for_status(self):
        pass


class FakeSession:
    """Mimics hcservices. `captcha_ok_after` = number of wrong captchas before success."""

    def __init__(self, history_html, search_rows, captcha_ok_after=0):
        self.headers = {}
        self.calls = []
        self.history_html = history_html
        self.search_rows = search_rows
        self.captcha_failures_left = captcha_ok_after

    def request(self, method, url, timeout=None, data=None):
        path = url.split("/hcservices/", 1)[1]
        self.calls.append((path.split("?")[0] + ("?" + path.split("?")[1] if "action_code" in path else ""), data))
        if path == "main.php":
            return FakeResponse("<html></html>")
        if path.startswith("securimage"):
            return FakeResponse(content=b"PNG")
        if "fillHCBench" in path:
            return FakeResponse("0~Select#1~Principal Seat Jammu#2~Srinagar Wing#")
        if "fillCaseType" in path:
            return FakeResponse("0~Select#35~WP(C) - WRIT PETITION (CIVIL)#12~OWP - ORIGINAL WRIT PETITION#")
        if "showRecords" in path:
            if self.captcha_failures_left:
                self.captcha_failures_left -= 1
                return FakeResponse('{"con":"Invalid Captcha"}')
            return FakeResponse(json.dumps({"con": [json.dumps(self.search_rows)]}))
        if "o_civil_case_history" in path:
            return FakeResponse(self.history_html)
        raise AssertionError(path)


class FixedSolver:
    def solve(self, image):
        return "abc123"


def make_court(session, attempts=3):
    return ECourtsHighCourt(
        "jkhc", "JKHC", "12", FixedSolver, session=session,
        bench_aliases={"sgr": "srinagar"}, request_delay=0, captcha_attempts=attempts,
    )


ROWS = [{"cino": "JKHC010012342023", "case_no": "203500012342023", "type_name": "WP(C)",
         "reg_year": "2023", "pet_name": "Ram Kumar", "res_name": "UT of J&K"}]


def new_case(**kw):
    base = dict(id=1, advocate_id=1, court="jkhc", bench="jammu", case_type="WP(C)",
                case_no="1234", case_year=2023)
    base.update(kw)
    return TrackedCase(**base)


def test_first_lookup_searches_with_captcha_then_fetches_history(fixture_text):
    session = FakeSession(fixture_text("history_pending.html"), ROWS, captcha_ok_after=2)
    status = make_court(session).fetch_status(new_case())

    assert status.next_hearing == date(2026, 9, 29)
    assert status.cnr == "JKHC010012342023"
    assert status.internal_no == "203500012342023"
    assert status.case_type_name == "WP(C)"

    search_calls = [d for p, d in session.calls if "showRecords" in p]
    assert len(search_calls) == 3  # two rejected captchas, then success
    assert search_calls[-1]["case_type"] == "35"
    assert search_calls[-1]["court_code"] == "1"
    history = [d for p, d in session.calls if "o_civil_case_history" in p][0]
    assert history["cino"] == "JKHC010012342023" and history["case_no"] == "203500012342023"


def test_known_case_refresh_skips_captcha(fixture_text):
    session = FakeSession(fixture_text("history_pending.html"), ROWS)
    case = new_case(cnr="JKHC010012342023", internal_no="203500012342023")
    make_court(session).fetch_status(case)
    assert not any("securimage" in p or "showRecords" in p for p, _ in session.calls)


def test_bench_alias_and_name_resolution():
    court = make_court(FakeSession("", []))
    assert court.resolve_bench("Jammu") == "1"
    assert court.resolve_bench("sgr") == "2"
    assert court.resolve_bench("2") == "2"
    with pytest.raises(CourtError):
        court.resolve_bench("leh")


def test_case_type_resolution():
    court = make_court(FakeSession("", []))
    assert court.resolve_case_type("jammu", "wp(c)") == ("35", "WP(C) - WRIT PETITION (CIVIL)")
    assert court.resolve_case_type("jammu", "OWP")[0] == "12"
    with pytest.raises(CourtError):
        court.resolve_case_type("jammu", "XYZ")


def test_case_not_found(fixture_text):
    with pytest.raises(CaseNotFound):
        make_court(FakeSession("", [])).fetch_status(new_case())


def test_captcha_exhausted():
    session = FakeSession("", ROWS, captcha_ok_after=99)
    with pytest.raises(CourtError, match="captcha"):
        make_court(session, attempts=2).fetch_status(new_case())
