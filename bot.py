"""Telegram bot: users send reports (text / images), admins read today's reports and manage users."""
import asyncio
import html
import logging
from datetime import timedelta

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from db import Report, User, day_range_utc, db, local_today, to_local

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
REPORTS_PER_PAGE = 20

# Inline button callback data. <pos> is a users-list position: filter letter + page, e.g. "p0".
READ_NEXT = "read_next"
MENU_READ = "menu:read"
MENU_STATUS = "menu:status"
USERS_PAGE = "users:"    # users:<pos>
USER_CARD = "user:"      # user:<telegram_id>:<pos>
USER_ACTION = "act:"     # act:<telegram_id>:<action>:<pos>   (then shows the user card)
QUICK_ACTION = "qa:"     # qa:<telegram_id>:<action>:<pos>    (then shows the users list)
REPORTS_MENU = "rpm:"    # rpm:<telegram_id>   0 = all users
REPORTS = "rp:"          # rp:<telegram_id>:<period>:<offset>
USERS_PER_PAGE = 10

# Users list filters: letter -> (tab label, test)
FILTERS = {
    "a": ("All", lambda u: True),
    "p": ("⏳ Pending", lambda u: u.is_pending),
    "c": ("✅ Active", lambda u: not u.is_suspended and not u.is_pending),
    "s": ("⛔ Suspended", lambda u: u.is_suspended),
}
# Report periods: letter -> label
PERIODS = {"t": "Today", "w": "This week", "m": "This month", "a": "All"}

# Reply keyboard (the buttons under the text box)
BTN_SEND = "📝 Send report"
BTN_MINE = "📂 My reports"
BTN_READ = "📖 Read reports"
BTN_STATUS = "📊 Status"
BTN_USERS = "👥 List users"
BTN_ALL = "🗂 All reports"
OLD_BTN_USERS = "👥 Manage users"  # label used by earlier versions; still on some users' keyboards

USER_COMMANDS = [
    BotCommand("start", "Start / help"),
    BotCommand("mine", "My reports"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("read", "Read today's reports (next user)"),
    BotCommand("status", "Today's report summary"),
    BotCommand("users", "List and manage users"),
    BotCommand("reports", "All users' reports"),
]

# action -> (button label, note sent to the user)
ACTIONS = {
    "approve": ("✅ Accept", "✅ An admin accepted your account. You can send reports now."),
    "make_admin": ("🛡 Make admin", "🛡 You are now an <b>admin</b>. Use the buttons below to read reports."),
    "remove_admin": ("Remove admin", "You are no longer an admin."),
    "block": ("⛔ Suspend", "⛔ Your account has been suspended. Your reports will not be saved."),
    "unblock": ("▶️ Activate", "▶️ Your account is active again. You can send reports."),
}

PENDING_TEXT = ("⏳ Your account is waiting for an admin to accept it. "
                "You'll get a message here as soon as you can send reports.")
BLOCKED_TEXT = "⛔ Your account is suspended. Contact an admin."


def main_keyboard(user: User) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(BTN_SEND), KeyboardButton(BTN_MINE)]]
    if user.is_admin and not user.is_suspended:
        rows += [[KeyboardButton(BTN_READ), KeyboardButton(BTN_STATUS)],
                 [KeyboardButton(BTN_USERS), KeyboardButton(BTN_ALL)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 Read", callback_data=MENU_READ),
        InlineKeyboardButton("📊 Status", callback_data=MENU_STATUS),
        InlineKeyboardButton("👥 Users", callback_data=f"{USERS_PAGE}a0"),
    ]])


# ---------- helpers ----------
def _register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> User:
    tg = update.effective_user
    is_new = db.get_user(tg.id) is None
    user = db.upsert_user(tg.id, tg.username, tg.full_name)
    if is_new and user.is_pending:
        context.application.create_task(_announce_new_user(context.application, user))
    return user


async def _announce_new_user(app: Application, user: User) -> None:
    """Ask every admin to accept or suspend a new user."""
    text = (
        f"🙋 <b>New user</b> wants to send reports:\n{html.escape(user.display_name)} · "
        f"<code>{user.telegram_id}</code>"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(ACTIONS["approve"][0], callback_data=f"{USER_ACTION}{user.telegram_id}:approve:p0"),
        InlineKeyboardButton(ACTIONS["block"][0], callback_data=f"{USER_ACTION}{user.telegram_id}:block:p0"),
    ]])
    for admin_id in db.active_admin_ids():
        await notify(app, admin_id, text, reply_markup=keyboard)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _header(user: User, report: Report) -> str:
    local = to_local(report.created_at)
    when = local.strftime("%H:%M" if local.date() == local_today() else "%a %d %b %Y, %H:%M")
    return f"👤 <b>{html.escape(user.display_name)}</b> · 🕒 {when}"


def _caption(user: User, report: Report, limit: int) -> str:
    header = _header(user, report)
    raw = report.text or ""
    # Shorten the raw text (not the escaped HTML) so we never cut an entity in half.
    while True:
        text = header + (f"\n\n{html.escape(raw)}" if raw else "")
        if len(text) <= limit:
            return text
        raw = _truncate(raw, max(1, len(raw) - (len(text) - limit) - 1))


async def _answer_tap(update: Update, clear_buttons: bool, text: str | None = None) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer(text)
    if clear_buttons:
        # Remove the buttons from the tapped message so old menus can't be pressed twice.
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass


async def _admin_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             clear_buttons: bool = True) -> User | None:
    """Register the caller and answer the button tap, if any. Returns the admin, or None
    (after telling the caller) if they're not an active admin."""
    await _answer_tap(update, clear_buttons)
    user = _register(update, context)
    if not user.is_admin or user.is_suspended:
        await context.bot.send_message(update.effective_chat.id, "This is only for admins.",
                                       reply_markup=main_keyboard(user))
        return None
    return user


async def set_commands_for(app: Application, user: User) -> None:
    """Show admin commands in the menu only for admins."""
    try:
        await app.bot.set_my_commands(
            ADMIN_COMMANDS if user.is_admin else USER_COMMANDS,
            scope=BotCommandScopeChat(user.telegram_id),
        )
    except TelegramError as e:
        log.warning("Could not set commands for %s: %s", user.telegram_id, e)


async def notify(app: Application, telegram_id: int, text: str, reply_markup=None) -> None:
    try:
        await app.bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
    except TelegramError as e:
        log.warning("Could not notify %s: %s", telegram_id, e)


async def apply_user_change(app: Application, telegram_id: int, action: str) -> User:
    """Change a user's role or status, then update their command menu and keyboard and tell them.
    Used by both the bot and the web admin page."""
    user = change_user(telegram_id, action)
    await set_commands_for(app, user)
    await notify(app, telegram_id, ACTIONS[action][1], reply_markup=main_keyboard(user))
    return user


def change_user(telegram_id: int, action: str) -> User:
    """Database part of apply_user_change."""
    if telegram_id in config.SUPER_ADMIN_IDS and action in ("block", "remove_admin"):
        raise ValueError("Super admins can't be suspended or removed.")
    if action == "approve":
        db.set_approved(telegram_id, True)
    elif action == "make_admin":
        db.set_role(telegram_id, "admin")
        db.set_approved(telegram_id, True)
    elif action == "remove_admin":
        db.set_role(telegram_id, "user")
    elif action == "block":
        db.set_suspended(telegram_id, True)
    elif action == "unblock":
        db.set_suspended(telegram_id, False)
        db.set_approved(telegram_id, True)
    else:
        raise ValueError(f"Unknown action {action!r}")
    return db.get_user(telegram_id)


# ---------- start / send report ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    await set_commands_for(context.application, user)
    if user.is_suspended:
        await update.message.reply_text(BLOCKED_TEXT, reply_markup=main_keyboard(user))
        return
    if user.is_pending:
        await update.message.reply_text(f"Hi {html.escape(update.effective_user.first_name or '')} 👋\n\n"
                                        + PENDING_TEXT, reply_markup=main_keyboard(user))
        return

    text = (
        f"Hi {html.escape(update.effective_user.first_name or '')} 👋\n\n"
        f"{BTN_SEND} – how to send a report (just send me a <b>message</b>, <b>photo</b> or any <b>file</b>)\n"
        f"{BTN_MINE} – see the reports you've sent (today, this week, this month or all)"
    )
    if user.is_admin:
        text += (
            "\n\n🛡 <b>You are an admin.</b>\n"
            f"{BTN_READ} – read today's reports, one user at a time\n"
            f"{BTN_STATUS} – summary of today's reports\n"
            f"{BTN_USERS} – accept new users, suspend, activate, make admins, see a user's reports\n"
            f"{BTN_ALL} – everyone's reports for today, this week, this month or all time"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(user))


async def send_report_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    if user.is_suspended:
        await update.message.reply_text(BLOCKED_TEXT)
        return
    if user.is_pending:
        await update.message.reply_text(PENDING_TEXT)
        return
    await update.message.reply_text(
        "📝 Send your report now: type a <b>text message</b>, or send a <b>photo</b>, <b>video</b>, "
        "<b>file</b> (PDF, Word, Excel, …), <b>voice message</b> or <b>audio</b>. Albums work too. "
        "Add a caption to describe it. Every message you send is saved as a report.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user),
    )


# ---------- reports by period (own reports, or any user's / everyone's for admins) ----------
def _period_range(period: str) -> tuple[str | None, str | None]:
    """UTC [start, end) for a period in the configured timezone; (None, None) for all time."""
    today = local_today()
    first = {"t": today, "w": today - timedelta(days=today.weekday()), "m": today.replace(day=1)}.get(period)
    if first is None:
        return None, None
    return day_range_utc(first)[0], day_range_utc(today)[1]


def _period_buttons(target_id: int, current: str | None = None) -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(("• " if p == current else "") + label, callback_data=f"{REPORTS}{target_id}:{p}:0")
        for p, label in PERIODS.items()
    ]


def _can_view(viewer: User, target_id: int) -> bool:
    if target_id == viewer.telegram_id:
        return True
    return viewer.is_admin and not viewer.is_suspended


def _reports_title(viewer: User, target_id: int) -> str:
    if target_id == viewer.telegram_id:
        return "Your reports"
    if target_id == 0:
        return "All users' reports"
    target = db.get_user(target_id)
    return f"Reports from {html.escape(target.display_name if target else str(target_id))}"


async def _send_reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, viewer: User,
                             target_id: int) -> None:
    if not _can_view(viewer, target_id):
        await context.bot.send_message(update.effective_chat.id, "This is only for admins.")
        return
    rows = [_period_buttons(target_id)[:2], _period_buttons(target_id)[2:]]
    if target_id not in (0, viewer.telegram_id):
        rows.append([InlineKeyboardButton("◀ Back to user", callback_data=f"{USER_CARD}{target_id}:a0")])
    await context.bot.send_message(
        update.effective_chat.id,
        f"📂 <b>{_reports_title(viewer, target_id)}</b>\nWhich reports do you want to see?",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    await _send_reports_menu(update, context, user, user.telegram_id)


async def all_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = await _admin_from_update(update, context)
    if admin:
        await _send_reports_menu(update, context, admin, 0)


async def reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """rpm:<id> (also old rep:<id>:<day> buttons from earlier versions)."""
    viewer = _register(update, context)
    await _answer_tap(update, clear_buttons=False)
    await _send_reports_menu(update, context, viewer, int(update.callback_query.data.split(":")[1]))


async def reports_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """rp:<id>:<period>:<offset>: up to REPORTS_PER_PAGE reports, newest first."""
    _, target, period, offset = update.callback_query.data.split(":")
    target_id, offset = int(target), int(offset)
    viewer = _register(update, context)
    await _answer_tap(update, clear_buttons=True)
    chat_id = update.effective_chat.id
    bot = context.bot
    if not _can_view(viewer, target_id):
        await bot.send_message(chat_id, "This is only for admins.")
        return

    user_filter = target_id or None
    start, end = _period_range(period)
    total = db.count_reports(user_filter, start, end)
    page = db.reports_newest_first(user_filter, start, end, REPORTS_PER_PAGE, offset)

    title = f"📂 <b>{_reports_title(viewer, target_id)}</b> · {PERIODS[period]}"
    if not page:
        await bot.send_message(chat_id, f"{title}\nNo reports.", parse_mode=ParseMode.HTML)
    else:
        shown = ""
        if total > len(page):
            shown = (f" · showing the {len(page)} newest" if offset == 0
                     else f" · showing older ones {offset + 1}–{offset + len(page)}")
        await bot.send_message(chat_id, f"{title} · {total} report(s){shown}", parse_mode=ParseMode.HTML)
        reporters: dict[int, User] = {}
        # Oldest first within the page, so albums keep their order.
        for group in _group_albums(list(reversed(page))):
            reporter = reporters.setdefault(group[0].user_id, db.get_user(group[0].user_id))
            try:
                await _send_group(bot, chat_id, reporter, group)
            except TelegramError as e:
                log.warning("Could not show reports %s: %s", [r.id for r in group], e)
                await bot.send_message(chat_id, f"⚠️ Could not show a report: {html.escape(str(e))}")

    rows = []
    remaining = total - offset - len(page)
    if remaining > 0:
        rows.append([InlineKeyboardButton(f"Older ▶ ({remaining} more)",
                                          callback_data=f"{REPORTS}{target_id}:{period}:{offset + REPORTS_PER_PAGE}")])
    rows += [_period_buttons(target_id, period)[:2], _period_buttons(target_id, period)[2:]]
    if target_id not in (0, viewer.telegram_id):
        rows.append([InlineKeyboardButton("◀ Back to user", callback_data=f"{USER_CARD}{target_id}:a0")])
    await bot.send_message(chat_id, "Choose another period:" if not remaining else "More:",
                           reply_markup=InlineKeyboardMarkup(rows))


# ---------- admin: status ----------
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _admin_from_update(update, context)
    if not user:
        return
    s = db.today_stats(user.telegram_id)
    pending = db.unread_users_today(user.telegram_id)
    text = (
        "📊 <b>Today's reports</b>\n"
        f"Reports: {s['total']} from {s['reporters']} user(s)\n"
        f"Unread by you: {s['unread']} from {len(pending)} user(s)"
    )
    waiting = sum(1 for r in db.list_users() if r["user"].is_pending)
    if waiting:
        text += f"\n⏳ Waiting for approval: {waiting} (see {BTN_USERS})"
    if pending:
        text += "\n\n" + "\n".join(f"• {html.escape(u.display_name)} – {n}" for u, n in pending[:30])
        if len(pending) > 30:
            text += f"\n…and {len(pending) - 30} more"
    await context.bot.send_message(
        update.effective_chat.id, text, parse_mode=ParseMode.HTML, reply_markup=admin_menu()
    )


# ---------- admin: users ----------
def _badge(u: User) -> str:
    badge = "⭐" if u.is_super_admin else "🛡" if u.is_admin else "⏳" if u.is_pending else "👤"
    return badge + ("⛔" if u.is_suspended else "")


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                keyboard: InlineKeyboardMarkup) -> None:
    """Edit the tapped message in place, or send a new one."""
    query = update.callback_query
    if query:
        try:
            await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
            return
        except TelegramError:
            # e.g. the tapped message was a photo, or nothing changed: send a new one.
            pass
    await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML,
                                   reply_markup=keyboard)


def _parse_pos(token: str) -> tuple[str, int]:
    """'p2' -> ('p', 2); a bare page number (older buttons) means the 'All' filter."""
    if token and token[0] in FILTERS:
        return token[0], int(token[1:] or 0)
    return "a", int(token or 0)


def _quick_action(admin: User, target: User) -> str | None:
    """The one action shown next to a user in the list."""
    allowed = _allowed_actions(admin, target)
    return next((a for a in ("approve", "unblock", "block") if a in allowed), None)


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List users (filtered: all / pending / active / suspended), USERS_PER_PAGE at a time.
    Each row: the user (opens their card), 📂 their reports, and a quick Accept / Suspend / Activate."""
    query = update.callback_query
    admin = await _admin_from_update(update, context, clear_buttons=False)
    if not admin:
        return
    await _render_users(update, context, admin, query.data[len(USERS_PAGE):] if query else "a0")


async def _render_users(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: User, pos: str) -> None:
    flt, page = _parse_pos(pos)
    everyone = db.list_users()
    rows = [r for r in everyone if FILTERS[flt][1](r["user"])]
    pages = max(1, -(-len(rows) // USERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    pos = f"{flt}{page}"
    chunk = rows[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]

    counts = {f: sum(1 for r in everyone if test(r["user"])) for f, (_, test) in FILTERS.items()}
    lines = [f"👥 <b>Users · {FILTERS[flt][0]}</b>: {len(rows)}"]
    if pages > 1:
        lines[0] += f" · page {page + 1}/{pages}"
    for r in chunk:
        u: User = r["user"]
        lines.append(
            f"{_badge(u)} {html.escape(u.display_name)} · <code>{u.telegram_id}</code>\n"
            f"      today {r['today_reports']} · total {r['total_reports']}"
        )
    if not chunk:
        lines.append("Nobody here.")
    lines.append("\n⭐ super admin · 🛡 admin · 👤 user · ⏳ pending · ⛔ suspended\n"
                 "Tap a name for details, 📂 for their reports.")

    kb = [[InlineKeyboardButton(("• " if f == flt else "") + f"{label} {counts[f]}",
                                callback_data=f"{USERS_PAGE}{f}0")
           for f, (label, _) in FILTERS.items()]]
    for r in chunk:
        u = r["user"]
        row = [
            InlineKeyboardButton(f"{_badge(u)} {_truncate(u.full_name or str(u.telegram_id), 18)}",
                                 callback_data=f"{USER_CARD}{u.telegram_id}:{pos}"),
            InlineKeyboardButton("📂", callback_data=f"{REPORTS_MENU}{u.telegram_id}"),
        ]
        action = _quick_action(admin, u)
        if action:
            row.append(InlineKeyboardButton(ACTIONS[action][0],
                                            callback_data=f"{QUICK_ACTION}{u.telegram_id}:{action}:{pos}"))
        kb.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"{USERS_PAGE}{flt}{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"{USERS_PAGE}{flt}{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🗂 All users' reports", callback_data=f"{REPORTS_MENU}0")])
    await _show(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))


def _allowed_actions(admin: User, target: User) -> list[str]:
    """Any admin can accept, suspend / activate and make admin. Only super admins can remove an
    admin or suspend one. Nobody can change a super admin or themselves."""
    if target.is_super_admin or target.telegram_id == admin.telegram_id:
        return []
    actions = []
    if target.is_pending:
        actions.append("approve")
    if target.role == "admin":
        if admin.is_super_admin:
            actions.append("remove_admin")
    elif not target.is_suspended:
        actions.append("make_admin")
    if not target.is_admin or admin.is_super_admin:
        actions.append("unblock" if target.is_suspended else "block")
    return actions


async def _render_user_card(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: User,
                            target: User, pos: str) -> None:
    today, total = db.user_report_counts(target.telegram_id)
    role = "Super admin" if target.is_super_admin else "Admin" if target.is_admin else "User"
    status_ = "⛔ Suspended" if target.is_suspended else "⏳ Pending" if target.is_pending else "✅ Active"
    text = (
        f"{_badge(target)} <b>{html.escape(target.display_name)}</b>\n"
        f"ID: <code>{target.telegram_id}</code>\n"
        f"Role: {role}\n"
        f"Status: {status_}\n"
        f"Reports: today {today} · total {total}\n"
        f"Joined: {to_local(target.created_at):%Y-%m-%d} · last seen {to_local(target.last_seen_at):%Y-%m-%d %H:%M}"
    )
    actions = _allowed_actions(admin, target)
    if target.is_super_admin:
        text += "\n\nSuper admins are set in SUPER_ADMIN_IDS and can't be changed here."
    elif target.telegram_id == admin.telegram_id:
        text += "\n\nThis is you."
    elif not actions:
        text += "\n\nOnly super admins can remove or suspend other admins."

    kb = [[InlineKeyboardButton(ACTIONS[a][0], callback_data=f"{USER_ACTION}{target.telegram_id}:{a}:{pos}")
           for a in actions]] if actions else []
    kb.append([InlineKeyboardButton("📂 Reports", callback_data=f"{REPORTS_MENU}{target.telegram_id}")])
    kb.append([InlineKeyboardButton("◀ Back to users", callback_data=f"{USERS_PAGE}{pos}")])
    await _show(update, context, text, InlineKeyboardMarkup(kb))


async def user_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = await _admin_from_update(update, context, clear_buttons=False)
    if not admin:
        return
    _, target_id, pos = update.callback_query.data.split(":")
    target = db.get_user(int(target_id))
    if not target:
        await context.bot.send_message(update.effective_chat.id, "User not found.")
        return
    await _render_user_card(update, context, admin, target, pos)


async def user_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """act:… (from a user card or a new-user alert) and qa:… (from the users list)."""
    query = update.callback_query
    kind, target_id, action, pos = query.data.split(":")
    from_list = f"{kind}:" == QUICK_ACTION
    admin = _register(update, context)
    target = db.get_user(int(target_id))
    if not admin.is_admin or admin.is_suspended:
        await query.answer("Only for admins.", show_alert=True)
        return
    if not target or action not in _allowed_actions(admin, target):
        # The list / card was out of date (someone else changed this user); show the current one.
        await query.answer("Not allowed or already changed.", show_alert=True)
    else:
        target = await apply_user_change(context.application, target.telegram_id, action)
        log.info("Admin %s: %s -> %s", admin.telegram_id, action, target.telegram_id)
        label = ACTIONS[action][0].split(" ", 1)[-1]
        await query.answer(f"{target.full_name or target.telegram_id}: {label} done.")
    if from_list:
        await _render_users(update, context, admin, pos)
    elif target:
        await _render_user_card(update, context, admin, target, pos)


# ---------- admin: read ----------
async def read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the next user's unread reports for today, then mark them read."""
    admin = await _admin_from_update(update, context)
    if not admin:
        return
    chat_id = update.effective_chat.id
    bot = context.bot

    pending = db.unread_users_today(admin.telegram_id)
    if not pending:
        await bot.send_message(chat_id, "✅ You have read all of today's reports.", reply_markup=admin_menu())
        return

    reporter, _ = pending[0]
    reports = db.unread_reports_today_for_user(admin.telegram_id, reporter.telegram_id)
    remaining_users = len(pending) - 1

    await bot.send_message(
        chat_id,
        f"📂 <b>{html.escape(reporter.display_name)}</b> — {len(reports)} report(s) today",
        parse_mode=ParseMode.HTML,
    )

    sent_ids: list[int] = []
    for group in _group_albums(reports):
        try:
            await _send_group(bot, chat_id, reporter, group)
            sent_ids.extend(r.id for r in group)
        except TelegramError as e:
            log.exception("Failed to deliver reports %s", [r.id for r in group])
            await bot.send_message(chat_id, f"⚠️ Could not show a report: {html.escape(str(e))}")
            sent_ids.extend(r.id for r in group)  # don't get stuck on a broken file

    db.mark_read(admin.telegram_id, sent_ids)

    if remaining_users:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"Next user ▶ ({remaining_users} left)", callback_data=READ_NEXT)]]
            + list(admin_menu().inline_keyboard)
        )
        await bot.send_message(
            chat_id, f"{remaining_users} more user(s) with unread reports.", reply_markup=keyboard
        )
    else:
        await bot.send_message(chat_id, "✅ That was the last one — all of today's reports are read.",
                               reply_markup=admin_menu())


# Report kinds that can be sent together as an album, and the InputMedia type for each.
ALBUM_MEDIA = {"photo": InputMediaPhoto, "video": InputMediaVideo,
               "document": InputMediaDocument, "audio": InputMediaAudio}


def _album_compatible(a: str, b: str) -> bool:
    """Telegram albums mix photos and videos; documents and audio only go with their own kind."""
    visual = {"photo", "video"}
    return a == b or (a in visual and b in visual)


def _group_albums(reports: list[Report]) -> list[list[Report]]:
    """Group consecutive items from the same album so they can be sent together."""
    groups: list[list[Report]] = []
    for r in reports:
        last = groups[-1][-1] if groups else None
        if (
            last
            and r.media_group_id
            and last.media_group_id == r.media_group_id
            and r.kind in ALBUM_MEDIA
            and last.kind in ALBUM_MEDIA
            and _album_compatible(last.kind, r.kind)
            and len(groups[-1]) < 10
        ):
            groups[-1].append(r)
        else:
            groups.append([r])
    return groups


async def _send_group(bot, chat_id: int, reporter: User, group: list[Report]) -> None:
    first = group[0]
    if len(group) > 1:
        media = [
            ALBUM_MEDIA[r.kind](
                r.file_id,
                caption=_caption(reporter, r, CAPTION_LIMIT) if (i == 0 or r.text) else None,
                parse_mode=ParseMode.HTML,
            )
            for i, r in enumerate(group)
        ]
        await bot.send_media_group(chat_id, media)
        return
    caption = _caption(reporter, first, CAPTION_LIMIT)
    senders = {
        "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
        "document": bot.send_document, "audio": bot.send_audio, "voice": bot.send_voice,
    }
    if first.kind in senders:
        await senders[first.kind](chat_id, first.file_id, caption=caption, parse_mode=ParseMode.HTML)
    elif first.kind == "video_note":
        # Round videos can't have a caption: say who sent it first.
        await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML)
        await bot.send_video_note(chat_id, first.file_id)
    else:
        await bot.send_message(chat_id, _caption(reporter, first, TEXT_LIMIT), parse_mode=ParseMode.HTML)


def _report_from_message(msg) -> tuple[str, str | None, str | None] | None:
    """(kind, text, file_id) for a message, or None if it isn't something we save."""
    # Animations (GIFs) also carry msg.document, so check them before documents.
    for kind, media in [("photo", msg.photo[-1] if msg.photo else None), ("video", msg.video),
                        ("animation", msg.animation), ("document", msg.document), ("audio", msg.audio),
                        ("voice", msg.voice), ("video_note", msg.video_note)]:
        if media:
            return kind, msg.caption, media.file_id
    if msg.text:
        return "text", msg.text, None
    return None


# ---------- report submission ----------
async def receive_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = _register(update, context)
    if not user.can_report:
        # Albums arrive as several messages; answer only once.
        refused: set = context.bot_data.setdefault("refused_albums", set())
        if msg.media_group_id in refused:
            return
        if msg.media_group_id:
            refused.add(msg.media_group_id)
        reason = BLOCKED_TEXT if user.is_suspended else PENDING_TEXT
        await msg.reply_text(f"{reason}\n\nYour report was not saved.", reply_markup=main_keyboard(user))
        return

    found = _report_from_message(msg)
    if not found:
        return
    kind, text, file_id = found
    report_id = db.add_report(user.telegram_id, kind, text, file_id, msg.media_group_id)

    # Every admin is told about every new report.
    _queue_live_delivery(context, user, report_id, msg.media_group_id)

    # Albums arrive as several messages; confirm only once per album.
    if msg.media_group_id:
        acked: set = context.bot_data.setdefault("acked_albums", set())
        if msg.media_group_id in acked:
            return
        acked.add(msg.media_group_id)
        if len(acked) > 1000:
            acked.clear()
    # The keyboard rides along so users who started the bot before it existed get it too.
    await msg.reply_text("✅ Report received. Thank you!", reply_markup=main_keyboard(user))


# ---------- live delivery to admins ----------
ALBUM_WAIT_SECONDS = 1.5


def _queue_live_delivery(context: ContextTypes.DEFAULT_TYPE, user: User, report_id: int,
                         media_group_id: str | None) -> None:
    """Push a new report to every admin right away. Album photos arrive as separate
    messages, so they're collected for a moment and sent together as one album."""
    app = context.application
    if not media_group_id:
        app.create_task(_deliver_live(app, user, [report_id]))
        return

    pending: dict = app.bot_data.setdefault("pending_albums", {})
    if media_group_id in pending:
        pending[media_group_id].append(report_id)
        return
    pending[media_group_id] = [report_id]

    async def flush() -> None:
        await asyncio.sleep(ALBUM_WAIT_SECONDS)
        await _deliver_live(app, user, pending.pop(media_group_id, []))

    app.create_task(flush())


async def _retry(send):
    """Run one Telegram send, waiting and retrying once if Telegram says we're sending too fast."""
    try:
        return await send()
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after if isinstance(e.retry_after, (int, float))
                            else e.retry_after.total_seconds())
        return await send()


async def _deliver_live(app: Application, reporter: User, report_ids: list[int]) -> None:
    """Send a new report to every active admin (except the one who sent it)."""
    reports = db.get_reports(report_ids)
    if not reports:
        return
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📂 Their reports", callback_data=f"{REPORTS_MENU}{reporter.telegram_id}"),
        InlineKeyboardButton("👤 User", callback_data=f"{USER_CARD}{reporter.telegram_id}:a0"),
    ]])
    header = (f"🆕 <b>New report</b> from {html.escape(reporter.display_name)}"
              + (f" · {len(reports)} items" if len(reports) > 1 else ""))
    for admin_id in db.active_admin_ids():
        if admin_id == reporter.telegram_id:
            continue
        try:
            await _retry(lambda: app.bot.send_message(admin_id, header, parse_mode=ParseMode.HTML,
                                                      reply_markup=keyboard))
        except TelegramError as e:
            # e.g. the admin blocked the bot or never pressed /start. They'll still see it via Read.
            log.warning("Live delivery to admin %s failed: %s", admin_id, e)
            continue
        delivered: list[int] = []
        for group in _group_albums(reports):
            try:
                await _retry(lambda: _send_group(app.bot, admin_id, reporter, group))
                delivered.extend(r.id for r in group)
            except TelegramError as e:
                log.warning("Live delivery of %s to admin %s failed: %s", [r.id for r in group], admin_id, e)
        if config.LIVE_MARKS_READ and delivered:
            db.mark_read(admin_id, delivered)


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Please send your report as a message, photo, video, voice message or file (stickers, "
        "locations and contacts aren't saved)."
    )


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error while processing update", exc_info=context.error)


def build_application() -> Application:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set (see .env.example)")
    app = Application.builder().token(config.BOT_TOKEN).build()
    private = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CommandHandler("help", start, filters=private))
    app.add_handler(CommandHandler("mine", my_reports, filters=private))
    app.add_handler(CommandHandler("read", read, filters=private))
    app.add_handler(CommandHandler("status", status, filters=private))
    app.add_handler(CommandHandler("users", users, filters=private))
    app.add_handler(CommandHandler("reports", all_reports, filters=private))

    # Reply keyboard buttons. Registered before receive_report so their text isn't saved as a report.
    for texts, handler in [([BTN_SEND], send_report_prompt), ([BTN_MINE], my_reports), ([BTN_READ], read),
                           ([BTN_STATUS], status), ([BTN_USERS, OLD_BTN_USERS], users), ([BTN_ALL], all_reports)]:
        app.add_handler(MessageHandler(private & filters.Text(texts), handler))

    pos = r"[apcs]?\d+"  # users-list position; a bare number comes from older buttons
    app.add_handler(CallbackQueryHandler(read, pattern=f"^({READ_NEXT}|{MENU_READ})$"))
    app.add_handler(CallbackQueryHandler(status, pattern=f"^{MENU_STATUS}$"))
    app.add_handler(CallbackQueryHandler(users, pattern=rf"^{USERS_PAGE}{pos}$"))
    app.add_handler(CallbackQueryHandler(user_card, pattern=rf"^{USER_CARD}\d+:{pos}$"))
    app.add_handler(CallbackQueryHandler(user_action, pattern=rf"^({USER_ACTION}|{QUICK_ACTION})\d+:[a-z_]+:{pos}$"))
    app.add_handler(CallbackQueryHandler(reports_menu, pattern=rf"^({REPORTS_MENU}\d+|rep:\d+:\d{{8}})$"))
    app.add_handler(CallbackQueryHandler(reports_page, pattern=rf"^{REPORTS}\d+:[twma]:\d+$"))
    app.add_handler(
        MessageHandler(
            private & (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL
                       | filters.AUDIO | filters.VOICE | filters.VIDEO_NOTE | (filters.TEXT & ~filters.COMMAND)),
            receive_report,
        )
    )
    app.add_handler(MessageHandler(private & ~filters.COMMAND, unsupported))
    app.add_error_handler(on_error)
    return app
