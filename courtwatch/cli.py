"""Command line interface: `courtwatch --help`."""

from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from datetime import date, timedelta

from .config import Config
from .courts import CourtError, get_court
from .db import Database
from .models import TrackedCase
from .notify import make_notifier
from .service import CaseWatcher

_CASE_REF = re.compile(
    r"^\s*(?P<type>.*?[^\d\s])\s*(?:No\.?|Number|#)?\s*[-:\s]*\s*(?P<no>\d+)\s*(?:/|of|-)\s*(?P<year>\d{4})\s*$",
    re.I,
)


def parse_case_ref(text: str) -> tuple[str, str, int]:
    """'WP(C) 1234/2023', 'OWP No. 55 of 2019', 'CRM(M)-12/2024' -> (type, number, year)."""
    m = _CASE_REF.match(text)
    if not m:
        raise ValueError(f"Can't read case reference {text!r}; use e.g. 'WP(C) 1234/2023'")
    return m["type"].strip(" -:"), m["no"].lstrip("0") or "0", int(m["year"])


def _fmt_case(c: TrackedCase) -> str:
    nxt = c.next_hearing.isoformat() if c.next_hearing else ("disposed" if c.disposed else "-")
    parts = [f"#{c.id:<4} {c.court}/{c.bench:<9} {c.display_number:<22} next: {nxt:<10}"]
    if c.title:
        parts.append(c.title[:60])
    if c.label:
        parts.append(f"[{c.label}]")
    if c.last_error:
        parts.append(f"(last error: {c.last_error[:80]})")
    return "  ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="courtwatch", description="Track court cases and send advocates WhatsApp hearing reminders."
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add-advocate", help="Add (or rename) an advocate profile")
    p.add_argument("name")
    p.add_argument("phone", help="WhatsApp number, e.g. 9876543210 or +91 98765 43210")

    sub.add_parser("advocates", help="List advocates")

    p = sub.add_parser("add-case", help="Track a case for an advocate")
    p.add_argument("phone", help="Advocate's phone (must be added first)")
    p.add_argument("case", help="Case reference, e.g. 'WP(C) 1234/2023'")
    p.add_argument("--court", default="jkhc")
    p.add_argument("--bench", default="jammu", help="jammu or srinagar (or bench code)")
    p.add_argument("--label", default="", help="Your note, e.g. client name")
    p.add_argument("--no-fetch", action="store_true", help="Don't look the case up now")

    p = sub.add_parser("import", help="Bulk add advocates + cases from CSV (see README)")
    p.add_argument("csv_file")
    p.add_argument("--fetch", action="store_true", help="Look each case up right away")

    p = sub.add_parser("cases", help="List tracked cases")
    p.add_argument("--phone")

    p = sub.add_parser("remove-case", help="Stop tracking a case")
    p.add_argument("case_id", type=int)

    p = sub.add_parser("search", help="Look a case up on the court site without saving it")
    p.add_argument("case", help="e.g. 'WP(C) 1234/2023'")
    p.add_argument("--court", default="jkhc")
    p.add_argument("--bench", default="jammu")

    p = sub.add_parser("benches", help="List benches of a court (live)")
    p.add_argument("--court", default="jkhc")

    p = sub.add_parser("case-types", help="List case types of a bench (live)")
    p.add_argument("--court", default="jkhc")
    p.add_argument("--bench", default="jammu")

    p = sub.add_parser("refresh", help="Re-fetch case status from the courts")
    p.add_argument("--force", action="store_true", help="Ignore the refresh interval")

    p = sub.add_parser("notify", help="Send reminders for hearings N days out (no refresh)")
    p.add_argument("--today", type=date.fromisoformat, help="Pretend today is YYYY-MM-DD")

    p = sub.add_parser("run-daily", help="Refresh, then send reminders. Schedule this once a day.")
    p.add_argument("--today", type=date.fromisoformat)

    p = sub.add_parser("upcoming", help="Hearings in the next N days")
    p.add_argument("--days", type=int, default=7)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    config = Config.from_env()
    db = Database(config.db_path)
    try:
        return _dispatch(args, config, db)
    except (CourtError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def _dispatch(args, config: Config, db: Database) -> int:
    cmd = args.cmd

    if cmd == "add-advocate":
        adv = db.upsert_advocate(args.name, args.phone)
        print(f"advocate #{adv.id} {adv.name} +{adv.phone}")
        return 0

    if cmd == "advocates":
        for adv in db.list_advocates():
            n = len(db.list_cases(adv.id))
            print(f"#{adv.id:<4} {adv.name:<30} +{adv.phone:<14} {n} case(s)")
        return 0

    if cmd == "add-case":
        adv = db.get_advocate_by_phone(args.phone)
        if adv is None:
            raise ValueError(f"No advocate with phone {args.phone}; run add-advocate first")
        return _add_case(config, db, adv.id, args.court, args.bench, args.case, args.label, not args.no_fetch)

    if cmd == "import":
        return _import(config, db, args.csv_file, args.fetch)

    if cmd == "cases":
        adv_id = None
        if args.phone:
            adv = db.get_advocate_by_phone(args.phone)
            if adv is None:
                raise ValueError(f"No advocate with phone {args.phone}")
            adv_id = adv.id
        for c in db.list_cases(adv_id):
            print(_fmt_case(c))
        return 0

    if cmd == "remove-case":
        print("removed" if db.remove_case(args.case_id) else "no such case")
        return 0

    if cmd == "search":
        case_type, number, year = parse_case_ref(args.case)
        probe = TrackedCase(None, 0, args.court, args.bench, case_type, number, year)
        status = get_court(args.court, config).fetch_status(probe)
        print(f"CNR:          {status.cnr}")
        print(f"Case:         {status.case_type_name or case_type} {number}/{year}")
        print(f"Parties:      {status.title}")
        print(f"Stage:        {status.stage}")
        print(f"Coram:        {status.coram}")
        print(f"Next hearing: {status.next_hearing}")
        print(f"Last hearing: {status.last_hearing}")
        print(f"Disposed:     {status.disposed}")
        return 0

    if cmd == "benches":
        for code, name in get_court(args.court, config).benches().items():
            print(f"{code:>4}  {name}")
        return 0

    if cmd == "case-types":
        for code, name in get_court(args.court, config).case_types(args.bench).items():
            print(f"{code:>4}  {name}")
        return 0

    if cmd == "refresh":
        print(CaseWatcher(config, db).refresh_all(force=args.force).summary())
        return 0

    if cmd == "notify":
        watcher = CaseWatcher(config, db, make_notifier(config))
        print(watcher.send_reminders(today=args.today).summary())
        return 0

    if cmd == "run-daily":
        watcher = CaseWatcher(config, db, make_notifier(config))
        report = watcher.run_daily(today=args.today)
        print(report.summary())
        return 1 if report.send_failed else 0

    if cmd == "upcoming":
        today = CaseWatcher(config, db).now().date()
        for offset in range(args.days + 1):
            day = today + timedelta(days=offset)
            for c in db.cases_with_hearing_on(day):
                adv = db.get_advocate(c.advocate_id)
                print(f"{day.isoformat()}  {adv.name:<20} {_fmt_case(c)}")
        return 0

    raise AssertionError(cmd)


def _add_case(config, db, advocate_id, court, bench, ref, label, fetch) -> int:
    case_type, number, year = parse_case_ref(ref)
    get_court(court, config)  # validate the court key early
    case = db.add_case(TrackedCase(None, advocate_id, court, bench.lower(), case_type, number, year, label))
    print(f"tracking case #{case.id}: {case.display_number} ({court}/{case.bench})")
    if fetch:
        try:
            case = CaseWatcher(config, db).refresh_case(case)
            print(_fmt_case(case))
        except CourtError as exc:
            print(f"  saved, but lookup failed (will retry on next refresh): {exc}", file=sys.stderr)
    return 0


def _import(config, db, path, fetch) -> int:
    """CSV columns: name, phone, case [, court, bench, label]."""
    count = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            try:
                parse_case_ref(row["case"])  # validate before creating anything
                adv = db.upsert_advocate(row["name"], row["phone"])
                _add_case(config, db, adv.id, row.get("court") or "jkhc", row.get("bench") or "jammu",
                          row["case"], row.get("label", ""), fetch)
                count += 1
            except (KeyError, ValueError) as exc:
                print(f"line {line_no}: skipped ({exc})", file=sys.stderr)
    print(f"imported {count} case(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
