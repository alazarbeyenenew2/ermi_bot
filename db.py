"""SQLite storage for users, reports and per-admin read tracking."""
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

import config

# Kinds of report: plain text, or a Telegram file of one of these types.
REPORT_KINDS = ("text", "photo", "video", "animation", "document", "audio", "voice", "video_note")

REPORTS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(telegram_id),
    kind            TEXT NOT NULL CHECK (kind IN ({kinds})),
    text            TEXT,
    file_id         TEXT,
    media_group_id  TEXT,
    created_at      TEXT NOT NULL,
    file_name       TEXT,  -- original name of an uploaded document / audio / video, if Telegram gives one
    message_id      INTEGER,  -- the user's Telegram message, so editing it can update the report
    edited_at       TEXT      -- set when the user edits the report
);
"""
# Columns every version of the reports table has (used when copying an old table).
REPORT_COLUMNS = "id, user_id, kind, text, file_id, media_group_id, created_at"


def _reports_table(name: str) -> str:
    return REPORTS_TABLE.format(name=name, kinds=", ".join(f"'{k}'" for k in REPORT_KINDS))


# user: sends reports. manager: also reads every report and gets new-report alerts, but can't manage
# users. admin: everything. (Super admins are admins listed in SUPER_ADMIN_IDS.)
ROLES = ("user", "manager", "admin")

USERS_TABLE = """
CREATE TABLE IF NOT EXISTS {name} (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    full_name     TEXT,
    role          TEXT NOT NULL DEFAULT 'user' CHECK (role IN ({roles})),
    is_suspended  INTEGER NOT NULL DEFAULT 0,  -- "blocked"
    is_approved   INTEGER NOT NULL DEFAULT 0,  -- new users can't send reports until an admin approves them
    created_at    TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL,
    language      TEXT  -- 'am' or 'en'; NULL until the user picks one
);
"""
USER_COLUMNS = "telegram_id, username, full_name, role, is_suspended, is_approved, created_at, last_seen_at, language"


def _users_table(name: str) -> str:
    return USERS_TABLE.format(name=name, roles=", ".join(f"'{r}'" for r in ROLES))


SCHEMA = _users_table("users") + """
""" + _reports_table("reports") + """
CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);
CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at);

-- Small key/value settings, e.g. whether the nightly backup is on.
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

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
    language: str | None = None

    @property
    def is_super_admin(self) -> bool:
        return self.telegram_id in config.SUPER_ADMIN_IDS

    @property
    def is_admin(self) -> bool:
        return self.is_super_admin or self.role == "admin"

    @property
    def is_manager(self) -> bool:
        return self.role == "manager" and not self.is_super_admin

    @property
    def can_view_reports(self) -> bool:
        """Admins and managers read everyone's reports."""
        return (self.is_admin or self.is_manager) and not self.is_suspended

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
    file_name: str | None = None
    message_id: int | None = None
    edited_at: str | None = None


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
        self._migrate_report_kinds()
        report_columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(reports)")}
        for column, sql_type in [("file_name", "TEXT"), ("message_id", "INTEGER"), ("edited_at", "TEXT")]:
            if column not in report_columns:
                self.conn.execute(f"ALTER TABLE reports ADD COLUMN {column} {sql_type}")
        self.conn.commit()
        if "language" not in {r["name"] for r in self.conn.execute("PRAGMA table_info(users)")}:
            self.conn.execute("ALTER TABLE users ADD COLUMN language TEXT")
            self.conn.commit()
        self._migrate_user_roles()

    def _migrate_user_roles(self) -> None:
        """Older databases only allowed the 'user' and 'admin' roles; copy the users table into one
        that also allows 'manager' (SQLite can't change a CHECK constraint in place)."""
        sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'"
        ).fetchone()["sql"]
        if all(f"'{r}'" in sql for r in ROLES):
            return
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.executescript(
                "BEGIN;"
                + _users_table("users_new")
                + f"INSERT INTO users_new ({USER_COLUMNS}) SELECT {USER_COLUMNS} FROM users;"
                "DROP TABLE users;"
                "ALTER TABLE users_new RENAME TO users;"
                "COMMIT;"
            )
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

    def _migrate_report_kinds(self) -> None:
        """Older databases only allowed text / photo / document reports. SQLite can't change a CHECK
        constraint in place, so copy the table into one with the current list of kinds."""
        sql = self.conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
        ).fetchone()["sql"]
        if all(f"'{k}'" in sql for k in REPORT_KINDS):
            return
        # Foreign keys off, or dropping the old table would cascade-delete everyone's read state.
        self.conn.execute("PRAGMA foreign_keys = OFF")
        try:
            self.conn.executescript(
                "BEGIN;"
                + _reports_table("reports_new")
                + f"INSERT INTO reports_new ({REPORT_COLUMNS}) SELECT {REPORT_COLUMNS} FROM reports;"
                "DROP TABLE reports;"
                "ALTER TABLE reports_new RENAME TO reports;"
                "CREATE INDEX IF NOT EXISTS idx_reports_created ON reports(created_at);"
                "CREATE INDEX IF NOT EXISTS idx_reports_user ON reports(user_id, created_at);"
                "COMMIT;"
            )
        except Exception:
            if self.conn.in_transaction:
                self.conn.rollback()
            raise
        finally:
            self.conn.execute("PRAGMA foreign_keys = ON")

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

    def set_language(self, telegram_id: int, language: str) -> None:
        self.conn.execute("UPDATE users SET language = ? WHERE telegram_id = ?", (language, telegram_id))
        self.conn.commit()

    def set_approved(self, telegram_id: int, approved: bool) -> None:
        self.conn.execute(
            "UPDATE users SET is_approved = ? WHERE telegram_id = ?", (int(approved), telegram_id)
        )
        self.conn.commit()

    def set_suspended(self, telegram_id: int, suspended: bool) -> None:
        if suspended and telegram_id in config.SUPER_ADMIN_IDS:
            raise ValueError("Super admins can't be suspended.")
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
        file_name: str | None = None,
        message_id: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            """INSERT INTO reports (user_id, kind, text, file_id, media_group_id, created_at, file_name, message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, kind, text, file_id, media_group_id, _now(), file_name, message_id),
        )
        self.conn.commit()
        return cur.lastrowid

    def report_viewer_ids(self) -> list[int]:
        """Admins and managers who should get new-report alerts (not suspended, have started the bot)."""
        rows = self.conn.execute(
            "SELECT telegram_id, role FROM users WHERE is_suspended = 0"
        ).fetchall()
        return [
            r["telegram_id"]
            for r in rows
            if r["role"] in ("admin", "manager") or r["telegram_id"] in config.SUPER_ADMIN_IDS
        ]

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

    def _range_where(self, user_id: int | None, start: str | None, end: str | None):
        where, args = [], []
        if user_id is not None:
            where.append("user_id = ?"); args.append(user_id)
        if start:
            where.append("created_at >= ?"); args.append(start)
        if end:
            where.append("created_at < ?"); args.append(end)
        return (" WHERE " + " AND ".join(where)) if where else "", args

    def count_reports(self, user_id: int | None, start: str | None, end: str | None) -> int:
        """Reports in [start, end) from one user, or from everyone when user_id is None."""
        where, args = self._range_where(user_id, start, end)
        return self.conn.execute(f"SELECT COUNT(*) FROM reports{where}", args).fetchone()[0]

    def reports_newest_first(self, user_id: int | None, start: str | None, end: str | None,
                             limit: int, offset: int) -> list[Report]:
        where, args = self._range_where(user_id, start, end)
        rows = self.conn.execute(
            f"SELECT * FROM reports{where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        return [Report(**dict(r)) for r in rows]

    # ---------- editing ----------
    def report_for_message(self, user_id: int, message_id: int) -> Report | None:
        row = self.conn.execute(
            "SELECT * FROM reports WHERE user_id = ? AND message_id = ?", (user_id, message_id)
        ).fetchone()
        return Report(**dict(row)) if row else None

    def update_report(self, report_id: int, kind: str, text: str | None, file_id: str | None,
                      file_name: str | None, message_id: int | None = None) -> None:
        """Replace a report's content. Admins' read marks are cleared so it shows as unread again."""
        self.conn.execute(
            """UPDATE reports SET kind = ?, text = ?, file_id = ?, file_name = ?, edited_at = ?,
                      message_id = COALESCE(?, message_id)
               WHERE id = ?""",
            (kind, text, file_id, file_name, _now(), message_id, report_id),
        )
        self.conn.execute("DELETE FROM report_reads WHERE report_id = ?", (report_id,))
        self.conn.commit()

    # ---------- deleting ----------
    def get_report(self, report_id: int) -> Report | None:
        row = self.conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return Report(**dict(row)) if row else None

    def album_report_ids(self, report_id: int) -> list[int]:
        """The report and every other item of the same album (same sender and media group)."""
        r = self.get_report(report_id)
        if not r:
            return []
        if not r.media_group_id:
            return [r.id]
        rows = self.conn.execute(
            "SELECT id FROM reports WHERE user_id = ? AND media_group_id = ? ORDER BY id",
            (r.user_id, r.media_group_id),
        ).fetchall()
        return [row["id"] for row in rows]

    def delete_reports(self, report_ids: list[int]) -> int:
        """Permanently delete reports (and everyone's read marks for them). Returns how many were deleted."""
        if not report_ids:
            return 0
        marks = ",".join("?" * len(report_ids))
        cur = self.conn.execute(f"DELETE FROM reports WHERE id IN ({marks})", report_ids)
        self.conn.commit()
        return cur.rowcount

    def delete_user(self, telegram_id: int) -> int:
        """Permanently delete a user and all their reports. Returns how many reports were deleted."""
        if telegram_id in config.SUPER_ADMIN_IDS:
            raise ValueError("Super admins can't be removed.")
        cur = self.conn.execute("DELETE FROM reports WHERE user_id = ?", (telegram_id,))
        self.conn.execute("DELETE FROM report_reads WHERE admin_id = ?", (telegram_id,))
        self.conn.execute("DELETE FROM users WHERE telegram_id = ?", (telegram_id,))
        self.conn.commit()
        return cur.rowcount

    # ---------- settings ----------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---------- backup ----------
    def copy_to(self, path: str) -> None:
        """Consistent copy of the whole database file (safe while the bot is running)."""
        target = sqlite3.connect(path)
        try:
            self.conn.backup(target)
        finally:
            target.close()

    def export_users(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            """SELECT u.*, (SELECT COUNT(*) FROM reports r WHERE r.user_id = u.telegram_id) AS total_reports
               FROM users u ORDER BY u.created_at""")]

    def export_reports(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            """SELECT r.*, u.full_name, u.username
               FROM reports r LEFT JOIN users u ON u.telegram_id = r.user_id ORDER BY r.created_at, r.id""")]

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
    # Super admins are never suspended, whatever the database says.
    data["is_suspended"] = bool(data["is_suspended"]) and data["telegram_id"] not in config.SUPER_ADMIN_IDS
    data["is_approved"] = bool(data["is_approved"])
    return User(**data)


db = Database(config.DATABASE_PATH)
