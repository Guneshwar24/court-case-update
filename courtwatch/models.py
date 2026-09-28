from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Advocate:
    id: int | None
    name: str
    phone: str  # E.164 without "+", e.g. 919876543210
    active: bool = True


@dataclass
class TrackedCase:
    """A case an advocate asked us to watch, plus the latest status we fetched."""

    id: int | None
    advocate_id: int
    court: str  # adapter key, e.g. "jkhc"
    bench: str  # bench/court code within that court, e.g. Jammu or Srinagar
    case_type: str  # the court's case type code (e.g. "WP(C)"'s numeric code)
    case_no: str
    case_year: int
    label: str = ""  # advocate's own note, e.g. client name
    cnr: str | None = None
    internal_no: str | None = None  # court-internal id; lets refreshes skip the captcha
    title: str | None = None  # "A vs B"
    stage: str | None = None
    coram: str | None = None
    next_hearing: date | None = None
    last_hearing: date | None = None
    disposed: bool = False
    last_checked_at: datetime | None = None
    last_error: str | None = None
    # Human readable case type, e.g. "WP(C)" (filled in by the adapter when known).
    case_type_name: str | None = None

    @property
    def display_number(self) -> str:
        return f"{self.case_type_name or self.case_type} {self.case_no}/{self.case_year}"


@dataclass
class HearingEntry:
    business_date: date | None
    hearing_date: date | None
    judge: str | None = None
    purpose: str | None = None


@dataclass
class CaseStatus:
    """What a court adapter returns for a single case."""

    cnr: str | None = None
    internal_no: str | None = None
    case_type_name: str | None = None
    petitioner: str | None = None
    respondent: str | None = None
    stage: str | None = None
    coram: str | None = None
    bench_name: str | None = None
    first_hearing: date | None = None
    next_hearing: date | None = None
    last_hearing: date | None = None
    decision_date: date | None = None
    disposed: bool = False
    history: list[HearingEntry] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        if self.petitioner and self.respondent:
            return f"{self.petitioner} vs {self.respondent}"
        return self.petitioner or self.respondent
