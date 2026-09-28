"""Court registry. Add a new court by registering another adapter factory here."""

from __future__ import annotations

from ..captcha import make_solver
from ..config import Config
from .base import CaseNotFound, CourtAdapter, CourtError, SearchHit
from .ecourts_hc import ECourtsHighCourt

__all__ = ["CaseNotFound", "CourtAdapter", "CourtError", "SearchHit", "get_court", "COURTS"]


def _jkhc(config: Config) -> CourtAdapter:
    # High Court of Jammu & Kashmir and Ladakh on eCourts hcservices.
    # Bench codes are discovered live via `courtwatch benches jkhc`; the aliases
    # just let advocates type "jammu" / "srinagar" / "jmu" / "sgr".
    return ECourtsHighCourt(
        key="jkhc",
        name="High Court of Jammu & Kashmir and Ladakh",
        state_code="12",
        solver_factory=lambda: make_solver(config.captcha_solver, config.captcha_dir),
        bench_aliases={"jmu": "jammu", "sgr": "srinagar", "sxr": "srinagar"},
        request_delay=config.request_delay,
        captcha_attempts=config.captcha_attempts,
    )


COURTS = {
    "jkhc": _jkhc,
}


def get_court(key: str, config: Config) -> CourtAdapter:
    try:
        return COURTS[key](config)
    except KeyError:
        raise CourtError(f"Unknown court {key!r}. Supported: {', '.join(COURTS)}") from None
