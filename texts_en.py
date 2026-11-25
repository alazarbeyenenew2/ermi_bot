"""English texts. texts_am.py must define exactly the same names (see texts.py).

Texts are HTML (Telegram parse mode). Anything inserted from users (names, captions, file names) must
be escaped with html.escape before it is passed in.
"""

LINE = "──────────────"


def _n(count: int, word: str) -> str:
    """'1 report', '2 reports'."""
    return f"{count} {word}" + ("" if count == 1 else "s")


# ---------- reply keyboard (the buttons under the text box) ----------
BTN_SEND = "📝 Send report"
BTN_MINE = "📂 My reports"
BTN_READ = "📥 Read reports"
BTN_STATUS = "📊 Summary"
BTN_USERS = "👥 Users"
BTN_ALL = "🗂 All reports"
KEYBOARD_PLACEHOLDER = "Type your report or send a file…"

# ---------- command menu ----------
CMD_START = "Start / help"
CMD_MINE = "My reports"
CMD_READ = "Read today's reports"
CMD_STATUS = "Today's summary"
CMD_USERS = "Manage users"
CMD_REPORTS = "All users' reports"


# ---------- welcome ----------
def welcome(name: str) -> str:
    return f"👋 Hi {name}!\nWelcome to <b>Report Bot</b>.\n{LINE}\n"


WELCOME_USER = (
    "📝 To send a report, just send me a <b>message</b>, <b>photo</b>, <b>video</b>, "
    "<b>voice message</b> or any <b>file</b>.\n\n"
    f"<b>{BTN_SEND}</b>\n   shows how to send a report\n"
    f"<b>{BTN_MINE}</b>\n   shows the reports you've sent"
)
WELCOME_ADMIN = (
    f"\n\n🛡 <b>You are an admin</b>\n{LINE}\n"
    f"<b>{BTN_READ}</b>\n   today's reports, one user at a time\n"
    f"<b>{BTN_STATUS}</b>\n   today at a glance\n"
    f"<b>{BTN_USERS}</b>\n   accept, suspend, activate, make admins\n"
    f"<b>{BTN_ALL}</b>\n   everyone's reports"
)
WELCOME_MANAGER = (
    f"\n\n📋 <b>You are a manager</b>\n{LINE}\n"
    f"<b>{BTN_READ}</b>\n   today's reports, one user at a time\n"
    f"<b>{BTN_STATUS}</b>\n   today at a glance\n"
    f"<b>{BTN_ALL}</b>\n   everyone's reports\n"
    "<i>You get a message for every new report.</i>"
)
PENDING = ("⏳ Your account is <b>waiting</b> for an admin to accept it.\n"
           "You'll get a message here as soon as you can send reports.")
BLOCKED = "⛔ Your account is <b>suspended</b>.\nPlease contact an admin."

# ---------- sending reports ----------
SEND_PROMPT = (
    f"📝 <b>Send a report</b>\n{LINE}\n"
    "Send your report now:\n"
    "✍️  a text message\n"
    "🖼  a photo or video (albums work too)\n"
    "📎  any file (PDF, Word, Excel …)\n"
    "🎤  a voice message or audio\n\n"
    "<i>You can add a caption. Every message you send is saved as a report.</i>"
)
REPORT_RECEIVED = "✅ Report received. Thank you!"
NOT_SAVED = "\n\n<i>Your report was not saved.</i>"
UNSUPPORTED = ("Please send your report as a <b>message</b>, <b>photo</b>, <b>video</b>, "
               "<b>voice message</b> or <b>file</b>.\n<i>Stickers, locations and contacts aren't saved.</i>")

CMD_LANGUAGE = "Change language"
LANGUAGE_SET = "✅ Language changed to <b>English</b>."

# ---------- general ----------
ONLY_ADMINS = "🔒 This is only for admins."
USER_NOT_FOUND = "User not found."
NOT_ALLOWED = "Not allowed, or already changed."
BACK = "◀️ Back"
PREV = "◀️ Previous"
NEXT = "Next ▶️"
TODAY = "Today"


def could_not_show(error: str) -> str:
    return f"⚠️ Could not show a report: {error}"


def done(name: str, action_label: str) -> str:
    return f"{name}: {action_label} ✅"


# ---------- report captions ----------
def caption_header(name: str, when: str) -> str:
    return f"👤 <b>{name}</b>\n🕒 {when}"


def caption_file(file_name: str) -> str:
    return f"📎 <code>{file_name}</code>"


# ---------- user actions: button label, message sent to the user ----------
ACTIONS = {
    "approve": ("✅ Accept", "✅ Your account has been <b>accepted</b>.\nYou can send reports now."),
    "make_admin": ("🛡 Make admin", "🛡 You are now an <b>admin</b>.\nUse the buttons below to read reports."),
    "remove_admin": ("➖ Remove admin", "You are no longer an admin."),
    "make_manager": ("📋 Make manager", "📋 You are now a <b>manager</b>.\nYou can read everyone's reports, "
                                       "and you'll be told about each new one."),
    "remove_manager": ("➖ Remove manager", "You are no longer a manager."),
    "block": ("⛔ Suspend", "⛔ Your account has been <b>suspended</b>.\nYour reports will not be saved."),
    "unblock": ("▶️ Activate", "▶️ Your account is <b>active</b> again.\nYou can send reports."),
}


# ---------- alerts to admins ----------
def new_user_alert(name: str, telegram_id: int) -> str:
    return (f"🙋 <b>New user</b>\n{LINE}\n👤 {name}\n🆔 <code>{telegram_id}</code>\n\n"
            "Wants permission to send reports.")


def new_report_alert(name: str, items: int) -> str:
    extra = f" · {items} files" if items > 1 else ""
    return f"🆕 <b>New report</b>{extra}\n👤 {name}"


ALERT_THEIR_REPORTS = "📂 Their reports"
ALERT_USER = "👤 User"

# ---------- reports by period ----------
PERIODS = {"t": "📅 Today", "w": "🗓 This week", "m": "📆 This month", "a": "♾ All"}
TITLE_MINE = "Your reports"
TITLE_ALL = "All users' reports"


def title_user(name: str) -> str:
    return f"{name} · reports"


CHOOSE_PERIOD = "Which reports do you want to see?"
NO_REPORTS = "No reports."
OTHER_PERIOD = "📅 Choose another period"


def report_count(total: int) -> str:
    return f"📨 {_n(total, 'report')}"


def showing_newest(n: int) -> str:
    return f"Showing the {n} newest"


def showing_older(first: int, last: int) -> str:
    return f"Showing older ones {first}–{last}"


def older_button(remaining: int) -> str:
    return f"⬇️ Older reports ({remaining})"


# ---------- read ----------
def read_header(name: str, count: int) -> str:
    return f"📥 <b>{name}</b>\n📨 {_n(count, 'report')} today\n{LINE}"


READ_ALL_DONE = "✅ You have read all of today's reports."
READ_LAST_DONE = "✅ That was the last one. All of today's reports are read."


def read_more(users_left: int) -> str:
    return f"<b>{users_left}</b> more {'user' if users_left == 1 else 'users'} with unread reports."


def next_user_button(users_left: int) -> str:
    return f"➡️ Next user ({users_left})"


# ---------- status ----------
def status(total: int, reporters: int, unread: int, unread_users: int, waiting: int) -> str:
    text = (
        f"📊 <b>Today's summary</b>\n{LINE}\n"
        f"📨 Reports: <b>{total}</b> (from {_n(reporters, 'user')})\n"
        f"📭 Unread by you: <b>{unread}</b> (from {_n(unread_users, 'user')})"
    )
    if waiting:
        text += f"\n⏳ Waiting for approval: <b>{waiting}</b>  →  {BTN_USERS}"
    return text


STATUS_UNREAD_TITLE = "\n\n<b>Unread reports</b>"


def status_more(n: int) -> str:
    return f"…and {n} more"


# ---------- users list ----------
FILTERS = {"a": "👥 All", "p": "⏳ Pending", "c": "✅ Active", "s": "⛔ Suspended"}


def users_title(filter_label: str, count: int, page: int, pages: int) -> str:
    text = f"<b>Users · {filter_label}</b> ({count})"
    if pages > 1:
        text += f"\nPage {page}/{pages}"
    return text + f"\n{LINE}"


def users_row(n: int, name: str, today: int, total: int) -> str:
    return f"<code>{n:>2}.</code> {name}  ·  📨 {today}/{total}"


SECTIONS = {"pending": "⏳ <b>Waiting for approval</b>", "admins": "🛡 <b>Admins</b>",
            "managers": "📋 <b>Managers</b>", "users": "👤 <b>Users</b>", "suspended": "⛔ <b>Suspended</b>"}
USERS_EMPTY = "<i>Nobody here.</i>"
USERS_LEGEND = f"{LINE}\n<i>📨 = reports today / total · ⭐ = super admin</i>"
USERS_HINT = {"a": "Tap a name to manage, 📂 for reports.", "c": "Tap a name to manage, 📂 for reports.",
              "p": "✅ accept · ⛔ suspend · or tap the name.", "s": "▶️ activate · 📂 reports · or tap the name."}

USERS_HINT_REMOVE = " 🗑 removes a user."

def page_label(page: int, pages: int) -> str:
    return f"{page}/{pages}"
ALL_USERS_REPORTS = "🗂 Everyone's reports"

# ---------- user card ----------
ROLE_SUPER = "⭐ Super admin"
ROLE_ADMIN = "🛡 Admin"
ROLE_USER = "👤 User"
ROLE_MANAGER = "📋 Manager"
STATE_SUSPENDED = "⛔ Suspended"
STATE_PENDING = "⏳ Pending"
STATE_ACTIVE = "✅ Active"


def user_card(name: str, telegram_id: int, role: str, state: str, today: int, total: int,
              joined: str, last_seen: str) -> str:
    return (
        f"<b>{name}</b>\n{LINE}\n"
        f"🆔 ID: <code>{telegram_id}</code>\n"
        f"🎖 Role: {role}\n"
        f"📍 Status: {state}\n"
        f"📨 Reports: today {today} · total {total}\n"
        f"📅 Joined: {joined}\n"
        f"👁 Last seen: {last_seen}"
    )


CARD_SUPER = "\n\n<i>Super admins are set in SUPER_ADMIN_IDS and can't be changed here.</i>"
CARD_SELF = "\n\n<i>This is you.</i>"
CARD_ADMIN_ONLY_SUPER = "\n\n<i>Only super admins can remove or suspend other admins.</i>"
CARD_REPORTS = "📂 Reports"
CARD_BACK = "◀️ Back to list"

# ---------- deleting reports (admins) ----------
DELETE = "🗑 Delete"


def delete_album(n: int) -> str:
    return f"🗑 Delete album ({n})"


ALBUM_ABOVE = "⬆️ Album above"
CONFIRM_DELETE = "⚠️ Yes, delete"
CANCEL = "✖️ Cancel"
ALREADY_DELETED = "Already deleted."


def reports_deleted(n: int) -> str:
    return "🗑 Report deleted permanently." if n == 1 else f"🗑 {n} reports deleted permanently."


# ---------- removing users (super admins) ----------
ONLY_SUPER = "🔒 Only super admins can do this."
REMOVE_USER = "🗑 Remove user"
REMOVE_USER_YES = "⚠️ Yes, remove permanently"


def remove_user_confirm(name: str, reports: int) -> str:
    return (f"\n\n⚠️ <b>Remove {name} permanently?</b>\n"
            f"Their {_n(reports, 'report')} will be deleted too. This can't be undone.\n"
            "<i>If they start the bot again, they'll be a new user waiting for approval.</i>")


def user_removed(name: str, reports: int) -> str:
    return f"🗑 {name} removed ({_n(reports, 'report')} deleted)."


# ---------- backup (super admins) ----------
BTN_BACKUP = "💾 Backup"
CMD_BACKUP = "Backup"
BACKUP_NOW = "📦 Back up now"
NIGHTLY_TURN_ON = "▶️ Turn nightly backup on"
NIGHTLY_TURN_OFF = "⏸ Turn nightly backup off"
BACKUP_WORKING = "⏳ Making the backup…"
NEVER = "never"


def backup_menu(nightly_on: bool, next_run: str, last_run: str) -> str:
    state = f"✅ On · every night at <b>00:00</b> (Ethiopia time)\n⏭ Next: {next_run}" if nightly_on else "⏸ Off"
    return (
        f"💾 <b>Backup</b>\n{LINE}\n"
        f"🌙 Nightly backup: {state}\n"
        f"🕓 Last backup: {last_run}\n\n"
        "<i>The backup is a zip with the full database and users / reports spreadsheets, "
        "sent here to every super admin.</i>"
    )


def backup_caption(when: str, users: int, reports: int, nightly: bool) -> str:
    kind = "🌙 Nightly backup" if nightly else "💾 Backup"
    return f"<b>{kind}</b> · {when}\n👥 {_n(users, 'user')} · 📨 {_n(reports, 'report')}"


BACKUP_TOO_BIG = "⚠️ The backup is larger than Telegram's 50 MB limit, so it can't be sent here. Copy it from the server."

# ---------- editing reports (users, within 12 hours) ----------
EDIT = "✏️ Edit"


def edit_item(n: int) -> str:
    return f"✏️ {n}"


EDIT_ALBUM_HINT = "✏️ Edit an item of the album above:"
EDIT_PROMPT = ("✏️ <b>Edit report</b>\n"
               "Send the new version now (text, photo or file). It will replace the old one.\n"
               "<i>Reports can be edited for 12 hours after sending.</i>")
EDIT_CANCELLED = "✖️ Edit cancelled. The report wasn't changed."
REPORT_EDITED = "✅ Report updated."
EDIT_TOO_LATE = "⏰ Reports can only be edited within 12 hours of sending. This one wasn't changed."
EDIT_GONE = "This report no longer exists."
EDITED_MARK = "✏️ edited"
EDIT_HELP = "\n\n✏️ <i>To change a report within 12 hours, edit your message or use ✏️ in My reports.</i>"


def edited_alert(name: str) -> str:
    return f"✏️ <b>Report edited</b>\n👤 {name}"
