"""SQLite storage for users, reports and per-admin read tracking."""
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    full_name     TEXT,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    is_suspended  INTEGER NOT NULL DEFAULT 0,  -- "blocked"
    is_approved   INTEGER NOT NULL DEFAULT 0,  -- new users can't send reports until an admin approves them
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(telegram_id),
    kind            TEXT NOT NULL CHECK (kind IN ('text', 'photo', 'document')),
    text            TEXT,
    file_id         TEXT,
    media_group_id  TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at);

-- Which admin has read which report. Each admin has their own read state.
CREATE TABLE IF NOT EXISTS report_reads (
    admin_id   INTEGER NOT NULL,
    report_id  INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    read_at    TEXT NOT NULL,
    PRIMARY KEY (admin_id, report_id)
);
"""


@dataclass
class User:
    telegram_id: int
    username: str | None
    full_name: str | None
    role: str
    is_suspended: bool
    is_approved: bool
    created_at: str
    last_seen_at: str

    @property
    def is_super_admin(self) -> bool:
        return self.telegram_id in config.SUPER_ADMIN_IDS

    @property
    def is_admin(self) -> bool:
        return self.is_super_admin or self.role == "admin"

    @property
    def is_pending(self) -> bool:
        """New user waiting for an admin to approve them."""
        return not self.is_admin and not self.is_approved and not self.is_suspended

    @property
    def can_report(self) -> bool:
        return not self.is_suspended and (self.is_approved or self.is_admin)

    @property
    def display_name(self) -> str:
        name = self.full_name or "Unknown"
        return f"{name} (@{self.username})" if self.username else name


@dataclass
class Report:
    id: int
    user_id: int
    kind: str
    text: str | None
    file_id: str | None
    media_group_id: str | None
    created_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_today() -> date:
    return datetime.now(config.TIMEZONE).date()


def today_range_utc() -> tuple[str, str]:
    """Start/end of 'today' in the configured timezone, as UTC ISO strings."""
    return day_range_utc(local_today())


def day_range_utc(day: date) -> tuple[str, str]:
    """Start/end of a local calendar day, as UTC ISO strings."""
    start = datetime.combine(day, time.min, tzinfo=config.TIMEZONE)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).isoformat(),
        end.astimezone(timezone.utc).isoformat(),
    )


def to_local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(config.TIMEZONE)


class Database:
    def __init__(self, path: str):
        if os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}
        if "is_approved" not in columns:
            # Databases from before approval existed: everyone already there stays allowed.
            self.conn.execute("ALTER TABLE users ADD COLUMN is_approved INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    # ---------- users ----------
    def upsert_user(self, telegram_id: int, username: str | None, full_name: str | None) -> User:
        now = _now()
        self.conn.execute(
            """
            INSERT INTO users (telegram_id, username, full_name, is_approved, created_at, last_seen_at)
            VALUES (?, ?, ?, 0, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name,
                last_seen_at = excluded.last_seen_at
            """,
            (telegram_id, username, full_name, now, now),
        )
        if telegram_id in config.SUPER_ADMIN_IDS:
            self.conn.execute(
                "UPDATE users SET role = 'admin', is_suspended = 0, is_approved = 1 WHERE telegram_id = ?",
                (telegram_id,),
            )
        self.conn.commit()
        return self.get_user(telegram_id)

    def get_user(self, telegram_id: int) -> User | None:
        row = self.conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
        return _user(row) if row else None

    def list_users(self, search: str = "") -> list[dict]:
        """All users plus their report counts (today and total)."""
        start, end = today_range_utc()
        like = f"%{search.strip()}%"
        rows = self.conn.execute(
            """
            SELECT u.*,
                   (SELECT COUNT(*) FROM reports r WHERE r.user_id = u.telegram_id) AS total_reports,
                   (SELECT COUNT(*) FROM reports r WHERE r.user_id = u.telegram_id
                        AND r.created_at >= ? AND r.created_at < ?) AS today_reports
            FROM users u
            WHERE ? = '%%' OR u.full_name LIKE ? OR u.username LIKE ?
                  OR CAST(u.telegram_id AS TEXT) LIKE ?
            -- Users waiting for approval first, then admins, then everyone else by name.
            ORDER BY (u.role = 'user' AND u.is_approved = 0 AND u.is_suspended = 0) DESC,
                     (u.role = 'admin') DESC, u.full_name COLLATE NOCASE
            """,
            (start, end, like, like, like, like),
        ).fetchall()
        return [
            {"user": _user(r), "total_reports": r["total_reports"], "today_reports": r["today_reports"]}
            for r in rows
        ]

    def set_role(self, telegram_id: int, role: str) -> None:
        self.conn.execute("UPDATE users SET role = ? WHERE telegram_id = ?", (role, telegram_id))
        self.conn.commit()

    def set_approved(self, telegram_id: int, approved: bool) -> None:
        self.conn.execute(
            "UPDATE users SET is_approved = ? WHERE telegram_id = ?", (int(approved), telegram_id)
        )
        self.conn.commit()

    def set_suspended(self, telegram_id: int, suspended: bool) -> None:
        self.conn.execute(
            "UPDATE users SET is_suspended = ? WHERE telegram_id = ?", (int(suspended), telegram_id)
        )
        self.conn.commit()

    # ---------- reports ----------
    def add_report(
        self,
        user_id: int,
        kind: str,
        text: str | None,
        file_id: str | None = None,
        media_group_id: str | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO reports (user_id, kind, text, file_id, media_group_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, kind, text, file_id, media_group_id, _now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def active_admin_ids(self) -> list[int]:
        """Admins who should receive live reports (not suspended, have started the bot)."""
        rows = self.conn.execute(
            "SELECT telegram_id, role FROM users WHERE is_suspended = 0"
        ).fetchall()
        return [
            r["telegram_id"]
            for r in rows
            if r["role"] == "admin" or r["telegram_id"] in config.SUPER_ADMIN_IDS
        ]

    def get_reports(self, report_ids: list[int]) -> list[Report]:
        if not report_ids:
            return []
        marks = ",".join("?" * len(report_ids))
        rows = self.conn.execute(
            f"SELECT * FROM reports WHERE id IN ({marks}) ORDER BY created_at, id", report_ids
        ).fetchall()
        return [Report(**dict(r)) for r in rows]

    def unread_users_today(self, admin_id: int) -> list[tuple[User, int]]:
        """Users who submitted reports today that this admin hasn't read yet,
        ordered by who reported first. Returns (user, unread_count)."""
        start, end = today_range_utc()
        rows = self.conn.execute(
            """
            SELECT u.*, COUNT(r.id) AS unread
            FROM reports r JOIN users u ON u.telegram_id = r.user_id
            WHERE r.created_at >= ? AND r.created_at < ?
              AND r.user_id != ?
              AND NOT EXISTS (SELECT 1 FROM report_reads rr
                              WHERE rr.admin_id = ? AND rr.report_id = r.id)
            GROUP BY u.telegram_id
            ORDER BY MIN(r.created_at)
            """,
            (start, end, admin_id, admin_id),
        ).fetchall()
        return [(_user(r), r["unread"]) for r in rows]

    def unread_reports_today_for_user(self, admin_id: int, user_id: int) -> list[Report]:
        start, end = today_range_utc()
        rows = self.conn.execute(
            """
            SELECT r.* FROM reports r
            WHERE r.user_id = ? AND r.created_at >= ? AND r.created_at < ?
              AND NOT EXISTS (SELECT 1 FROM report_reads rr
                              WHERE rr.admin_id = ? AND rr.report_id = r.id)
            ORDER BY r.created_at, r.id
            """,
            (user_id, start, end, admin_id),
        ).fetchall()
        return [Report(**dict(r)) for r in rows]

    def mark_read(self, admin_id: int, report_ids: list[int]) -> None:
        now = _now()
        self.conn.executemany(
            "INSERT OR IGNORE INTO report_reads (admin_id, report_id, read_at) VALUES (?, ?, ?)",
            [(admin_id, rid, now) for rid in report_ids],
        )
        self.conn.commit()

    def user_report_counts(self, user_id: int) -> tuple[int, int]:
        """(today, total) number of reports sent by this user."""
        start, end = today_range_utc()
        row = self.conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN created_at >= ? AND created_at < ? THEN 1 ELSE 0 END) AS today
               FROM reports WHERE user_id = ?""",
            (start, end, user_id),
        ).fetchone()
        return row["today"] or 0, row["total"]

    def user_report_days(self, user_id: int) -> list[date]:
        """Local calendar days on which this user sent reports, oldest first."""
        rows = self.conn.execute(
            "SELECT created_at FROM reports WHERE user_id = ?", (user_id,)
        ).fetchall()
        return sorted({to_local(r["created_at"]).date() for r in rows})

    def user_reports_on(self, user_id: int, day: date) -> list[Report]:
        start, end = day_range_utc(day)
        rows = self.conn.execute(
            """SELECT * FROM reports WHERE user_id = ? AND created_at >= ? AND created_at < ?
               ORDER BY created_at, id""",
            (user_id, start, end),
        ).fetchall()
        return [Report(**dict(r)) for r in rows]

    def today_stats(self, admin_id: int) -> dict:
        start, end = today_range_utc()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS total,
                   COUNT(DISTINCT r.user_id) AS reporters,
                   SUM(CASE WHEN rr.report_id IS NULL THEN 1 ELSE 0 END) AS unread
            FROM reports r
            LEFT JOIN report_reads rr ON rr.report_id = r.id AND rr.admin_id = ?
            WHERE r.created_at >= ? AND r.created_at < ? AND r.user_id != ?
            """,
            (admin_id, start, end, admin_id),
        ).fetchone()
        return {"total": row["total"], "reporters": row["reporters"], "unread": row["unread"] or 0}


def _user(row: sqlite3.Row) -> User:
    data = {k: row[k] for k in User.__dataclass_fields__}
    data["is_suspended"] = bool(data["is_suspended"])
    data["is_approved"] = bool(data["is_approved"])
    return User(**data)


db = Database(config.DATABASE_PATH)
