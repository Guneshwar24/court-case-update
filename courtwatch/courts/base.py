from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import CaseStatus, TrackedCase


class CourtError(Exception):
    """The court site could not answer (network, layout change, captcha exhausted...)."""


class CaseNotFound(CourtError):
    pass


@dataclass
class SearchHit:
    cnr: str | None
    internal_no: str | None
    case_type_name: str | None
    case_no: str
    case_year: int
    petitioner: str | None
    respondent: str | None

    @property
    def title(self) -> str | None:
        if self.petitioner and self.respondent:
            return f"{self.petitioner} vs {self.respondent}"
        return self.petitioner or self.respondent


class CourtAdapter(ABC):
    """One implementation per court (or per court platform + configuration)."""

    key: str
    name: str

    @abstractmethod
    def benches(self) -> dict[str, str]:
        """bench code -> bench name"""

    @abstractmethod
    def case_types(self, bench: str) -> dict[str, str]:
        """case type code -> case type name for a bench"""

    @abstractmethod
    def search(self, bench: str, case_type: str, case_no: str, year: int) -> list[SearchHit]:
        """Look a case up by type/number/year. May need a captcha."""

    @abstractmethod
    def fetch_status(self, case: TrackedCase) -> CaseStatus:
        """Latest status for a tracked case (next hearing date, stage, ...)."""
