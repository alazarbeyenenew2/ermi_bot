"""Builds a zipped backup of everything the bot stores.

The zip holds:
  bot.db       a full copy of the SQLite database (restore by putting it back as DATABASE_PATH)
  users.csv    every user, readable in Excel / Google Sheets
  reports.csv  every report, with sender, time, text and file name
  README.txt   what the files are

Photos and files themselves stay on Telegram's servers; the database keeps their Telegram file IDs,
which the bot can still send as long as the same bot token is used.
"""
import csv
import io
import os
import tempfile
import zipfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import config
from db import db, to_local

# Backups follow Ethiopian time, whatever TIMEZONE is set to.
ETHIOPIA = ZoneInfo("Africa/Addis_Ababa")
TELEGRAM_UPLOAD_LIMIT = 49 * 1024 * 1024  # bots can send files up to 50 MB

README = """Report Bot backup
=================

bot.db       Full copy of the database. To restore, stop the bot and replace the database file
             (DATABASE_PATH, /app/data/bot.db in Docker) with this file.
users.csv    Every user: role, status, language, dates and number of reports.
reports.csv  Every report: time, sender, kind, text, file name and Telegram file ID.

Times in the CSV files are in Ethiopian time (Africa/Addis_Ababa).
Photos and files stay on Telegram's servers; the bot can resend them from their file IDs.
"""


def now_ethiopia() -> datetime:
    return datetime.now(ETHIOPIA)


def next_midnight(after: datetime | None = None) -> datetime:
    """The next 00:00 in Ethiopia."""
    after = after or now_ethiopia()
    return datetime.combine(after.date() + timedelta(days=1), datetime.min.time(), tzinfo=ETHIOPIA)


def _local(iso: str | None) -> str:
    return to_local(iso).astimezone(ETHIOPIA).strftime("%Y-%m-%d %H:%M") if iso else ""


def _csv(rows: list[dict], columns: list[tuple[str, callable]]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([name for name, _ in columns])
    for row in rows:
        writer.writerow([get(row) for _, get in columns])
    # A BOM makes Excel read the file as UTF-8, so Amharic shows correctly.
    return ("﻿" + out.getvalue()).encode("utf-8")


def _role(u: dict) -> str:
    if u["telegram_id"] in config.SUPER_ADMIN_IDS:
        return "super_admin"
    return u["role"]


def _status(u: dict) -> str:
    if u["is_suspended"] and u["telegram_id"] not in config.SUPER_ADMIN_IDS:
        return "suspended"
    if not u["is_approved"] and u["role"] != "admin" and u["telegram_id"] not in config.SUPER_ADMIN_IDS:
        return "pending"
    return "active"


def make_backup() -> tuple[bytes, str, dict]:
    """Returns (zip bytes, file name, counts)."""
    users = db.export_users()
    reports = db.export_reports()
    users_csv = _csv(users, [
        ("telegram_id", lambda u: u["telegram_id"]),
        ("username", lambda u: u["username"] or ""),
        ("full_name", lambda u: u["full_name"] or ""),
        ("role", _role),
        ("status", _status),
        ("language", lambda u: u.get("language") or ""),
        ("reports", lambda u: u["total_reports"]),
        ("joined", lambda u: _local(u["created_at"])),
        ("last_seen", lambda u: _local(u["last_seen_at"])),
    ])
    reports_csv = _csv(reports, [
        ("id", lambda r: r["id"]),
        ("time", lambda r: _local(r["created_at"])),
        ("user_id", lambda r: r["user_id"]),
        ("full_name", lambda r: r["full_name"] or ""),
        ("username", lambda r: r["username"] or ""),
        ("kind", lambda r: r["kind"]),
        ("text", lambda r: r["text"] or ""),
        ("file_name", lambda r: r.get("file_name") or ""),
        ("file_id", lambda r: r["file_id"] or ""),
        ("album_id", lambda r: r["media_group_id"] or ""),
    ])

    stamp = now_ethiopia().strftime("%Y-%m-%d_%H%M")
    buffer = io.BytesIO()
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = os.path.join(tmp, "bot.db")
        db.copy_to(db_copy)
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, "bot.db")
            z.writestr("users.csv", users_csv)
            z.writestr("reports.csv", reports_csv)
            z.writestr("README.txt", README)
    return buffer.getvalue(), f"report-bot-backup_{stamp}.zip", {"users": len(users), "reports": len(reports)}
