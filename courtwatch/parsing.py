"""Parsers for eCourts High Court services (hcservices.ecourts.gov.in) responses.

The eCourts pages are loosely structured HTML, so the parsers look for labels
("Next Hearing Date", "Stage of Case", ...) instead of fixed table positions.
"""

from __future__ import annotations

import json
import re
from datetime import date

from bs4 import BeautifulSoup, Tag

from .models import CaseStatus, HearingEntry

_MONTHS = {
    m: i
    for i, names in enumerate(
        [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
         ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
         ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")],
        start=1,
    )
    for m in names
}

_WORDY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})[\s,\-]+(\d{4})")
_NUMERIC_DMY = re.compile(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})")
_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def parse_date(text: str | None) -> date | None:
    """Parse the date formats eCourts uses: '29th October 2026', '29-10-2026', '2026-10-29'."""
    if not text:
        return None
    text = text.strip()
    try:
        if m := _ISO.search(text):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _WORDY.search(text):
            month = _MONTHS.get(m[2].lower())
            if month:
                return date(int(m[3]), month, int(m[1]))
        if m := _NUMERIC_DMY.search(text):
            return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None
    return None


def parse_options(payload: str) -> dict[str, str]:
    """Parse eCourts dropdown payloads of the form '0~Select#1~Jammu#2~Srinagar#'.

    Some endpoints return HTML <option> tags instead; handle those too.
    """
    payload = payload.strip()
    options: dict[str, str] = {}
    if "<option" in payload.lower():
        soup = BeautifulSoup(payload, "html.parser")
        for opt in soup.find_all("option"):
            value = (opt.get("value") or "").strip()
            label = opt.get_text(" ", strip=True)
            if value and value != "0":
                options[value] = label
        return options
    for chunk in payload.split("#"):
        if "~" not in chunk:
            continue
        code, _, label = chunk.partition("~")
        code, label = code.strip(), label.strip()
        if code and code != "0" and label:
            options[code] = label
    return options


def parse_search_results(payload: str) -> list[dict]:
    """Parse the JSON returned by `index_qry.php?action_code=showRecords`.

    The body looks like {"con": ["[{...case...}, ...]"], ...} or {"con": "[...]"};
    on a wrong captcha it is plain text / JSON containing "Invalid Captcha".
    Raises CaptchaRejected / returns [] when nothing matched.
    """
    text = payload.strip().lstrip("﻿")
    if "invalid captcha" in text.lower():
        raise CaptchaRejected()
    if not text or text.lower().startswith("record not found"):
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    def flatten(obj) -> list[dict]:
        if isinstance(obj, dict):
            if "con" in obj:
                return flatten(obj["con"])
            if "cino" in obj or "case_no" in obj:
                return [obj]
            return []
        if isinstance(obj, list):
            out: list[dict] = []
            for item in obj:
                out.extend(flatten(item))
            return out
        if isinstance(obj, str):
            try:
                return flatten(json.loads(obj))
            except json.JSONDecodeError:
                if "invalid captcha" in obj.lower():
                    raise CaptchaRejected()
                return []
        return []

    return flatten(data)


class CaptchaRejected(Exception):
    pass


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    text = re.sub(r"\s+", " ", text).strip().lstrip(":").strip()
    return text or None


def _label_value(soup: BeautifulSoup, label: str) -> str | None:
    """Find the value printed next to a label like 'Next Hearing Date'."""
    pattern = re.compile(r"^\s*" + re.escape(label) + r"\s*:?\s*$", re.I)
    for node in soup.find_all(string=pattern):
        # Walk up to the element holding the label (label/strong/td/span).
        el: Tag | None = node.parent
        for _ in range(3):
            if el is None:
                break
            sibling = el.find_next_sibling()
            if sibling is not None:
                value = _clean(sibling.get_text(" ", strip=True))
                if value:
                    return value
            el = el.parent if el.parent and el.parent.name not in ("table", "body", "html") else None
    # Fallback: "Label : value" in the same text node.
    inline = re.compile(re.escape(label) + r"\s*:\s*(.+)", re.I)
    for node in soup.find_all(string=inline):
        m = inline.search(node)
        if m and _clean(m[1]):
            return _clean(m[1])
    return None


def _party(soup: BeautifulSoup, css_class: str, heading: str) -> str | None:
    """Petitioner/respondent names; eCourts wraps them in span.<css_class> after a heading."""
    el = soup.find(class_=css_class)
    if el is None:
        head = soup.find(string=re.compile(heading, re.I))
        if head is not None:
            el = head.find_parent().find_next(["span", "td", "div"])
    if el is None:
        return None
    text = el.get_text(" ", strip=True)
    # "1) Ram Kumar Advocate- X" -> "Ram Kumar"
    first = re.split(r"\s(?=\d+\))", text)[0]
    first = re.sub(r"^\d+\)\s*", "", first)
    first = re.split(r"\s+Advocate\s*[-:]", first, flags=re.I)[0]
    return _clean(first)


def parse_case_history(html: str) -> CaseStatus:
    """Parse `cases_qry/o_civil_case_history.php` output into a CaseStatus."""
    soup = BeautifulSoup(html, "html.parser")
    status = CaseStatus()

    status.cnr = (_label_value(soup, "CNR Number") or "").split(" ")[0] or None
    status.first_hearing = parse_date(_label_value(soup, "First Hearing Date"))
    status.decision_date = parse_date(_label_value(soup, "Decision Date"))
    status.stage = _label_value(soup, "Stage of Case")
    status.coram = _label_value(soup, "Coram")
    status.bench_name = _label_value(soup, "Bench Type") or _label_value(soup, "Bench")

    case_status_text = _label_value(soup, "Case Status") or ""
    if len(case_status_text) > 60:  # matched a section heading, not a "Case Status : X" field
        case_status_text = ""
    status.disposed = bool(status.decision_date) or "disposed" in case_status_text.lower() \
        or "disposed" in (status.stage or "").lower()

    next_text = _label_value(soup, "Next Hearing Date")
    status.next_hearing = None if status.disposed else parse_date(next_text)

    status.petitioner = _party(soup, "Petitioner_Advocate_table", r"Petitioner and Advocate")
    status.respondent = _party(soup, "Respondent_Advocate_table", r"Respondent and Advocate")

    status.history = _parse_history_table(soup)
    status.last_hearing = max(
        (h.business_date for h in status.history if h.business_date), default=None
    )
    # If "Next Hearing Date" was missing, the last history row's hearing date is the next date.
    if status.next_hearing is None and not status.disposed and status.history:
        status.next_hearing = status.history[-1].hearing_date
    return status


def _parse_history_table(soup: BeautifulSoup) -> list[HearingEntry]:
    table = soup.find("table", class_=re.compile("history_table", re.I))
    if table is None:
        heading = soup.find(string=re.compile(r"History of Case Hearing", re.I))
        table = heading.find_next("table") if heading else None
    if table is None:
        return []

    header_cells = [c.get_text(" ", strip=True).lower() for c in table.find_all("th")]

    def col(*names: str) -> int | None:
        for i, h in enumerate(header_cells):
            if any(n in h for n in names):
                return i
        return None

    judge_i = col("judge")
    business_i = col("business on date", "business date")
    hearing_i = col("hearing date")
    purpose_i = col("purpose")

    entries = []
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if not cells:
            continue

        def get(i: int | None) -> str | None:
            return cells[i] if i is not None and i < len(cells) else None

        entries.append(
            HearingEntry(
                business_date=parse_date(get(business_i)),
                hearing_date=parse_date(get(hearing_i)),
                judge=_clean(get(judge_i)),
                purpose=_clean(get(purpose_i)),
            )
        )
    return entries
