"""SQLite storage for advocates, their cases and the reminders already sent."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from .models import Advocate, TrackedCase

SCHEMA = """
CREATE TABLE IF NOT EXISTS advocates (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL UNIQUE,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cases (
    id              INTEGER PRIMARY KEY,
    advocate_id     INTEGER NOT NULL REFERENCES advocates(id) ON DELETE CASCADE,
    court           TEXT NOT NULL,
    bench           TEXT NOT NULL,
    case_type       TEXT NOT NULL,
    case_type_name  TEXT,
    case_no         TEXT NOT NULL,
    case_year       INTEGER NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    cnr             TEXT,
    internal_no     TEXT,
    title           TEXT,
    stage           TEXT,
    coram           TEXT,
    next_hearing    TEXT,
    last_hearing    TEXT,
    disposed        INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    last_error      TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (advocate_id, court, bench, case_type, case_no, case_year)
);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY,
    case_id      INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    hearing_date TEXT NOT NULL,
    channel      TEXT NOT NULL,
    provider_ref TEXT,
    sent_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (case_id, hearing_date)
);
"""

_CASE_COLUMNS = [
    "id", "advocate_id", "court", "bench", "case_type", "case_type_name", "case_no",
    "case_year", "label", "cnr", "internal_no", "title", "stage", "coram", "next_hearing",
    "last_hearing", "disposed", "last_checked_at", "last_error",
]


def _d(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- advocates -------------------------------------------------------

    def upsert_advocate(self, name: str, phone: str) -> Advocate:
        phone = normalize_phone(phone)
        with self.conn:
            self.conn.execute(
                "INSERT INTO advocates (name, phone) VALUES (?, ?) "
                "ON CONFLICT(phone) DO UPDATE SET name = excluded.name, active = 1",
                (name, phone),
            )
        return self.get_advocate_by_phone(phone)  # type: ignore[return-value]

    def get_advocate(self, advocate_id: int) -> Advocate | None:
        row = self.conn.execute("SELECT * FROM advocates WHERE id = ?", (advocate_id,)).fetchone()
        return self._advocate(row) if row else None

    def get_advocate_by_phone(self, phone: str) -> Advocate | None:
        row = self.conn.execute(
            "SELECT * FROM advocates WHERE phone = ?", (normalize_phone(phone),)
        ).fetchone()
        return self._advocate(row) if row else None

    def list_advocates(self) -> list[Advocate]:
        rows = self.conn.execute("SELECT * FROM advocates ORDER BY name").fetchall()
        return [self._advocate(r) for r in rows]

    @staticmethod
    def _advocate(row: sqlite3.Row) -> Advocate:
        return Advocate(id=row["id"], name=row["name"], phone=row["phone"], active=bool(row["active"]))

    # ---- cases -----------------------------------------------------------

    def add_case(self, case: TrackedCase) -> TrackedCase:
        with self.conn:
            self.conn.execute(
                "INSERT INTO cases (advocate_id, court, bench, case_type, case_no, case_year, label, cnr) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(advocate_id, court, bench, case_type, case_no, case_year) "
                "DO UPDATE SET label = excluded.label",
                (case.advocate_id, case.court, case.bench, case.case_type, case.case_no,
                 case.case_year, case.label, case.cnr),
            )
        row = self.conn.execute(
            "SELECT * FROM cases WHERE advocate_id = ? AND court = ? AND bench = ? "
            "AND case_type = ? AND case_no = ? AND case_year = ?",
            (case.advocate_id, case.court, case.bench, case.case_type, case.case_no, case.case_year),
        ).fetchone()
        return self._case(row)

    def remove_case(self, case_id: int) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        return cur.rowcount > 0

    def get_case(self, case_id: int) -> TrackedCase | None:
        row = self.conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return self._case(row) if row else None

    def list_cases(self, advocate_id: int | None = None, include_disposed: bool = True) -> list[TrackedCase]:
        sql = "SELECT * FROM cases WHERE 1=1"
        args: list = []
        if advocate_id is not None:
            sql += " AND advocate_id = ?"
            args.append(advocate_id)
        if not include_disposed:
            sql += " AND disposed = 0"
        sql += " ORDER BY next_hearing IS NULL, next_hearing, id"
        return [self._case(r) for r in self.conn.execute(sql, args).fetchall()]

    def cases_with_hearing_on(self, day: date) -> list[TrackedCase]:
        rows = self.conn.execute(
            "SELECT c.* FROM cases c JOIN advocates a ON a.id = c.advocate_id "
            "WHERE c.next_hearing = ? AND c.disposed = 0 AND a.active = 1 ORDER BY c.advocate_id, c.id",
            (day.isoformat(),),
        ).fetchall()
        return [self._case(r) for r in rows]

    def save_case(self, case: TrackedCase) -> None:
        values = {
            "case_type_name": case.case_type_name,
            "cnr": case.cnr,
            "internal_no": case.internal_no,
            "title": case.title,
            "stage": case.stage,
            "coram": case.coram,
            "next_hearing": case.next_hearing.isoformat() if case.next_hearing else None,
            "last_hearing": case.last_hearing.isoformat() if case.last_hearing else None,
            "disposed": int(case.disposed),
            "last_checked_at": case.last_checked_at.isoformat() if case.last_checked_at else None,
            "last_error": case.last_error,
        }
        assignments = ", ".join(f"{k} = ?" for k in values)
        with self.conn:
            self.conn.execute(f"UPDATE cases SET {assignments} WHERE id = ?", [*values.values(), case.id])

    @staticmethod
    def _case(row: sqlite3.Row) -> TrackedCase:
        data = {k: row[k] for k in _CASE_COLUMNS}
        data["next_hearing"] = _d(data["next_hearing"])
        data["last_hearing"] = _d(data["last_hearing"])
        data["last_checked_at"] = _dt(data["last_checked_at"])
        data["disposed"] = bool(data["disposed"])
        return TrackedCase(**data)

    # ---- notifications ---------------------------------------------------

    def already_notified(self, case_id: int, hearing_date: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM notifications WHERE case_id = ? AND hearing_date = ?",
            (case_id, hearing_date.isoformat()),
        ).fetchone()
        return row is not None

    def record_notification(self, case_id: int, hearing_date: date, channel: str, provider_ref: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO notifications (case_id, hearing_date, channel, provider_ref) "
                "VALUES (?, ?, ?, ?)",
                (case_id, hearing_date.isoformat(), channel, provider_ref),
            )


def normalize_phone(phone: str, default_country: str = "91") -> str:
    """Return digits-only E.164 (no '+'). Bare 10-digit numbers are assumed Indian."""
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = default_country + digits
    if len(digits) < 11:
        raise ValueError(f"Phone number looks too short: {phone!r}")
    return digits
