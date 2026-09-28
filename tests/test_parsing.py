from datetime import date

import pytest

from courtwatch.parsing import (
    CaptchaRejected,
    parse_case_history,
    parse_date,
    parse_options,
    parse_search_results,
)


@pytest.mark.parametrize("text,expected", [
    ("29th September 2026", date(2026, 9, 29)),
    (": 1st Oct 2026", date(2026, 10, 1)),
    ("02-03-2023", date(2023, 3, 2)),
    ("02/03/2023", date(2023, 3, 2)),
    ("2026-10-05", date(2026, 10, 5)),
    ("", None),
    ("not listed", None),
    ("31-02-2026", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_options_tilde_format():
    assert parse_options("0~Select Bench#1~Jammu Wing#2~Srinagar Wing#") == {
        "1": "Jammu Wing", "2": "Srinagar Wing",
    }


def test_parse_options_html_format():
    html = '<option value="0">Select</option><option value="35">WP(C) - Writ Petition (Civil)</option>'
    assert parse_options(html) == {"35": "WP(C) - Writ Petition (Civil)"}


def test_parse_search_results_nested_json_string():
    payload = '{"con":["[{\\"cino\\":\\"JKHC010012342023\\",\\"case_no\\":\\"203500012342023\\",\\"pet_name\\":\\"Ram Kumar\\"}]"],"totRecords":1}'
    rows = parse_search_results(payload)
    assert rows == [{"cino": "JKHC010012342023", "case_no": "203500012342023", "pet_name": "Ram Kumar"}]


def test_parse_search_results_invalid_captcha():
    with pytest.raises(CaptchaRejected):
        parse_search_results('{"con":"Invalid Captcha"}')


def test_parse_search_results_not_found():
    assert parse_search_results("Record Not Found") == []
    assert parse_search_results('{"con":"[]"}') == []


def test_parse_pending_history(fixture_text):
    s = parse_case_history(fixture_text("history_pending.html"))
    assert s.cnr == "JKHC010012342023"
    assert s.next_hearing == date(2026, 9, 29)
    assert s.first_hearing == date(2023, 3, 10)
    assert s.last_hearing == date(2023, 6, 5)
    assert s.stage == "FOR ADMISSION"
    assert s.coram == "HON'BLE MR. JUSTICE A. B. SHARMA"
    assert s.petitioner == "Ram Kumar"
    assert s.respondent == "UT of J&K and others"
    assert s.title == "Ram Kumar vs UT of J&K and others"
    assert not s.disposed
    assert len(s.history) == 2


def test_parse_disposed_history(fixture_text):
    s = parse_case_history(fixture_text("history_disposed.html"))
    assert s.disposed
    assert s.next_hearing is None
    assert s.decision_date == date(2024, 1, 12)
    assert s.title == "Abdul Rashid vs State of J&K"


def test_next_hearing_falls_back_to_history():
    html = """<h2>History of Case Hearing</h2><table>
      <tr><th>Judge</th><th>Business On Date</th><th>Hearing Date</th><th>Purpose</th></tr>
      <tr><td>X</td><td>01-09-2026</td><td>15-10-2026</td><td>ORDERS</td></tr></table>"""
    assert parse_case_history(html).next_hearing == date(2026, 10, 15)
