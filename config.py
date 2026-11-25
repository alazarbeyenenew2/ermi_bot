"""Configuration loaded from environment variables (or a .env file)."""
import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int_list(value: str) -> set[int]:
    return {int(x) for x in value.replace(" ", "").split(",") if x}


BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# Telegram user IDs that are always admins ("super admins").
# They cannot be demoted or suspended from the admin page.
SUPER_ADMIN_IDS = _int_list(os.getenv("SUPER_ADMIN_IDS", ""))

# Web admin page login
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# Send every new report to all admins the moment it arrives.
LIVE_REPORTS = os.getenv("LIVE_REPORTS", "true").lower() in ("1", "true", "yes")
# If true, a report delivered live counts as read, so /read won't show it again.
LIVE_MARKS_READ = os.getenv("LIVE_MARKS_READ", "true").lower() in ("1", "true", "yes")

# "Today" is computed in this timezone.
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "UTC"))

DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bot.db")

WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
