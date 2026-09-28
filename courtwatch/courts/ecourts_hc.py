"""Client for the eCourts High Court services portal (hcservices.ecourts.gov.in).

Most Indian High Courts, including the High Court of Jammu & Kashmir and Ladakh,
publish case status here. Flow:

1. GET  main.php                                   -> session cookie
2. POST cases_qry/index_qry.php?action_code=fillHCBench   (state_code)
3. POST cases_qry/index_qry.php?action_code=fillCaseType  (state_code, court_code)
4. GET  securimage/securimage_show.php             -> captcha image
5. POST cases_qry/index_qry.php?action_code=showRecords   (case type/no/year + captcha)
      -> JSON list of matching cases incl. `cino` (CNR) and internal `case_no`
6. POST cases_qry/o_civil_case_history.php         (cino + internal case_no)
      -> HTML with Next Hearing Date, Stage, Coram, hearing history

Step 6 does not need a captcha, so once a case has been found we store its
CNR/internal number and later refreshes are captcha-free.
"""

from __future__ import annotations

import random
import re
import time

import requests

from ..captcha import CaptchaSolver
from ..models import CaseStatus, TrackedCase
from ..parsing import CaptchaRejected, parse_case_history, parse_options, parse_search_results
from .base import CaseNotFound, CourtAdapter, CourtError, SearchHit

BASE_URL = "https://hcservices.ecourts.gov.in/hcservices"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36"
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class ECourtsHighCourt(CourtAdapter):
    def __init__(
        self,
        key: str,
        name: str,
        state_code: str,
        solver_factory,
        *,
        bench_aliases: dict[str, str] | None = None,
        request_delay: float = 2.0,
        captcha_attempts: int = 8,
        session: requests.Session | None = None,
        base_url: str = BASE_URL,
    ):
        self.key = key
        self.name = name
        self.state_code = state_code
        self._solver_factory = solver_factory
        self._solver: CaptchaSolver | None = None
        self.bench_aliases = {_norm(k): v for k, v in (bench_aliases or {}).items()}
        self.request_delay = request_delay
        self.captcha_attempts = captcha_attempts
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": f"{self.base_url}/main.php",
            "X-Requested-With": "XMLHttpRequest",
        })
        self._started = False
        self._last_request = 0.0
        self._bench_cache: dict[str, str] | None = None
        self._type_cache: dict[str, dict[str, str]] = {}

    # ---- HTTP helpers ----------------------------------------------------

    def _throttle(self) -> None:
        wait = self.request_delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        if not self._started and path != "main.php":
            self._request("GET", "main.php")
            self._started = True
        self._throttle()
        try:
            resp = self.session.request(method, f"{self.base_url}/{path}", timeout=30, **kwargs)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise CourtError(f"{self.name}: request to {path} failed: {exc}") from exc
        return resp

    def _qry(self, action: str, data: dict) -> str:
        return self._request("POST", f"cases_qry/index_qry.php?action_code={action}", data=data).text

    @property
    def solver(self) -> CaptchaSolver:
        if self._solver is None:
            self._solver = self._solver_factory()
        return self._solver

    # ---- lookups ---------------------------------------------------------

    def benches(self) -> dict[str, str]:
        if self._bench_cache is None:
            self._bench_cache = parse_options(self._qry("fillHCBench", {"state_code": self.state_code}))
        return self._bench_cache

    def resolve_bench(self, bench: str) -> str:
        """Accept a bench code ('1') or a name/alias ('jammu', 'Srinagar Wing')."""
        bench = self.bench_aliases.get(_norm(bench), bench)
        if bench.isdigit():
            return bench
        for code, name in self.benches().items():
            if _norm(bench) in _norm(name):
                return code
        raise CourtError(f"Unknown bench {bench!r} for {self.name}. Known: {self.benches()}")

    def case_types(self, bench: str) -> dict[str, str]:
        code = self.resolve_bench(bench)
        if code not in self._type_cache:
            payload = self._qry("fillCaseType", {"state_code": self.state_code, "court_code": code})
            self._type_cache[code] = parse_options(payload)
        return self._type_cache[code]

    def resolve_case_type(self, bench: str, case_type: str) -> tuple[str, str | None]:
        """Accept a case type code ('35') or name ('WP(C)', 'wpc'). Returns (code, name)."""
        types = self.case_types(bench)
        if case_type in types:
            return case_type, types[case_type]
        wanted = _norm(case_type)
        exact = [c for c, n in types.items() if _norm(n) == wanted]
        # eCourts names often look like "WP(C) - Writ Petition (Civil)".
        prefix = [c for c, n in types.items() if _norm(n.split("-")[0]) == wanted]
        matches = exact or prefix
        if len(matches) == 1:
            return matches[0], types[matches[0]]
        if case_type.isdigit():
            return case_type, None
        hint = ", ".join(sorted({n.split("-")[0].strip() for n in types.values()})[:40])
        raise CourtError(f"Can't match case type {case_type!r}. Some options: {hint}")

    def search(self, bench: str, case_type: str, case_no: str, year: int) -> list[SearchHit]:
        bench_code = self.resolve_bench(bench)
        type_code, type_name = self.resolve_case_type(bench, case_type)

        for attempt in range(1, self.captcha_attempts + 1):
            image = self._request("GET", f"securimage/securimage_show.php?{random.random()}").content
            captcha = self.solver.solve(image)
            if not captcha:
                continue
            payload = self._qry("showRecords", {
                "court_code": bench_code,
                "state_code": self.state_code,
                "court_complex_code": bench_code,
                "caseStatusSearchType": "CScaseNumber",
                "captcha": captcha,
                "case_type": type_code,
                "case_no": case_no,
                "rgyear": str(year),
                "caseNoType": "new",
                "displayOldCaseStatus": "Old",
            })
            try:
                rows = parse_search_results(payload)
            except CaptchaRejected:
                continue
            return [self._hit(r, type_name, case_no, year) for r in rows]
        raise CourtError(f"{self.name}: captcha not solved after {self.captcha_attempts} attempts")

    @staticmethod
    def _hit(row: dict, type_name: str | None, case_no: str, year: int) -> SearchHit:
        return SearchHit(
            cnr=row.get("cino") or row.get("cnr"),
            internal_no=str(row["case_no"]) if row.get("case_no") else None,
            case_type_name=row.get("type_name") or type_name,
            case_no=str(row.get("reg_no") or row.get("case_no2") or case_no),
            case_year=int(row.get("reg_year") or year),
            petitioner=row.get("pet_name"),
            respondent=row.get("res_name"),
        )

    def fetch_status(self, case: TrackedCase) -> CaseStatus:
        bench_code = self.resolve_bench(case.bench)
        cnr, internal_no, type_name = case.cnr, case.internal_no, case.case_type_name
        hit: SearchHit | None = None
        if not (cnr and internal_no):
            hits = self.search(case.bench, case.case_type, case.case_no, case.case_year)
            if not hits:
                raise CaseNotFound(f"{case.display_number} not found at {self.name}")
            hit = hits[0]
            cnr, internal_no, type_name = hit.cnr, hit.internal_no, hit.case_type_name or type_name

        html = self._request("POST", "cases_qry/o_civil_case_history.php", data={
            "court_code": bench_code,
            "state_code": self.state_code,
            "court_complex_code": bench_code,
            "case_no": internal_no,
            "cino": cnr,
            "appFlag": "",
        }).text
        if "Next Hearing Date" not in html and "Case Status" not in html and "Decision Date" not in html:
            raise CourtError(f"{self.name}: unexpected case history page for {cnr}")

        status = parse_case_history(html)
        status.cnr = status.cnr or cnr
        status.internal_no = internal_no
        status.case_type_name = type_name
        if hit is not None:
            status.petitioner = status.petitioner or hit.petitioner
            status.respondent = status.respondent or hit.respondent
        return status
