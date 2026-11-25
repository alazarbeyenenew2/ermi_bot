"""Telegram bot: users send reports (text / images), admins read today's reports and manage users."""
import asyncio
import html
import logging
from datetime import date, datetime

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from db import Report, User, db, local_today, to_local

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
MAX_REPORTS_SHOWN = 50  # per day in the report browser

# Inline button callback data
READ_NEXT = "read_next"
MENU_READ = "menu:read"
MENU_STATUS = "menu:status"
USERS_PAGE = "users:"   # users:<page>
USER_CARD = "user:"     # user:<telegram_id>:<page>
USER_ACTION = "act:"    # act:<telegram_id>:<action>:<page>
REPORTS_DAY = "rep:"    # rep:<telegram_id>:<YYYYMMDD>
USERS_PER_PAGE = 10

# Reply keyboard (the buttons under the text box)
BTN_SEND = "📝 Send report"
BTN_MINE = "📂 My reports"
BTN_READ = "📖 Read reports"
BTN_STATUS = "📊 Status"
BTN_USERS = "👥 Manage users"

USER_COMMANDS = [
    BotCommand("start", "Start / help"),
    BotCommand("mine", "My reports"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("read", "Read today's reports (next user)"),
    BotCommand("status", "Today's report summary"),
    BotCommand("users", "List and manage users"),
]

# action -> (button label, note sent to the user)
ACTIONS = {
    "approve": ("✅ Approve", "✅ An admin approved your account. You can send reports now."),
    "make_admin": ("🛡 Make admin", "🛡 You are now an <b>admin</b>. Use the buttons below to read reports."),
    "remove_admin": ("Remove admin", "You are no longer an admin."),
    "block": ("⛔ Block", "⛔ You have been blocked. Your reports will not be saved."),
    "unblock": ("✅ Unblock", "✅ You have been unblocked. You can send reports again."),
}

PENDING_TEXT = ("⏳ Your account is waiting for an admin to approve it. "
                "You'll get a message here as soon as you can send reports.")
BLOCKED_TEXT = "⛔ You are blocked. Contact an admin."


def main_keyboard(user: User) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(BTN_SEND), KeyboardButton(BTN_MINE)]]
    if user.is_admin and not user.is_suspended:
        rows += [[KeyboardButton(BTN_READ), KeyboardButton(BTN_STATUS)], [KeyboardButton(BTN_USERS)]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📖 Read", callback_data=MENU_READ),
        InlineKeyboardButton("📊 Status", callback_data=MENU_STATUS),
        InlineKeyboardButton("👥 Users", callback_data=f"{USERS_PAGE}0"),
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
    """Ask every admin to approve or block a new user."""
    text = (
        f"🙋 <b>New user</b> wants to send reports:\n{html.escape(user.display_name)} · "
        f"<code>{user.telegram_id}</code>"
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(ACTIONS["approve"][0], callback_data=f"{USER_ACTION}{user.telegram_id}:approve:0"),
        InlineKeyboardButton(ACTIONS["block"][0], callback_data=f"{USER_ACTION}{user.telegram_id}:block:0"),
    ]])
    for admin_id in db.active_admin_ids():
        await notify(app, admin_id, text, reply_markup=keyboard)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _header(user: User, report: Report) -> str:
    when = to_local(report.created_at).strftime("%H:%M")
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


def _fmt_day(day: date) -> str:
    return "Today" if day == local_today() else day.strftime("%a %d %b %Y")


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
        f"{BTN_SEND} – how to send a report (just send me a <b>text message</b> or an <b>image</b>)\n"
        f"{BTN_MINE} – see the reports you've sent"
    )
    if user.is_admin:
        text += (
            "\n\n🛡 <b>You are an admin.</b>\n"
            f"{BTN_READ} – read today's reports, one user at a time\n"
            f"{BTN_STATUS} – summary of today's reports\n"
            f"{BTN_USERS} – approve new users, make admins, block"
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
        "📝 Send your report now: type a <b>text message</b>, or send a <b>photo</b> / album / image file "
        "(add a caption to describe it). Every message you send is saved as a report.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(user),
    )


# ---------- report browser (own reports, or any user's for admins) ----------
async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    await _show_reports(update, context, user, user.telegram_id, local_today())


async def reports_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _, target, day = update.callback_query.data.split(":")
    viewer = _register(update, context)
    await _answer_tap(update, clear_buttons=True)
    await _show_reports(update, context, viewer, int(target), datetime.strptime(day, "%Y%m%d").date())


async def _show_reports(update: Update, context: ContextTypes.DEFAULT_TYPE, viewer: User,
                        target_id: int, day: date) -> None:
    chat_id = update.effective_chat.id
    bot = context.bot
    own = target_id == viewer.telegram_id
    if not own and (not viewer.is_admin or viewer.is_suspended):
        await bot.send_message(chat_id, "This is only for admins.")
        return
    target = db.get_user(target_id)
    if not target:
        await bot.send_message(chat_id, "User not found.")
        return

    reports = db.user_reports_on(target_id, day)
    days = db.user_report_days(target_id)
    who = "Your reports" if own else f"Reports from {html.escape(target.display_name)}"
    title = f"📂 <b>{who}</b> · {_fmt_day(day)}"
    if not reports:
        empty = "You haven't sent any reports" if own else "No reports"
        await bot.send_message(chat_id, f"{title}\n{empty} on this day.", parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(chat_id, f"{title} · {len(reports)} report(s)", parse_mode=ParseMode.HTML)
        for group in _group_albums(reports[:MAX_REPORTS_SHOWN]):
            try:
                await _send_group(bot, chat_id, target, group)
            except TelegramError as e:
                log.warning("Could not show reports %s: %s", [r.id for r in group], e)
                await bot.send_message(chat_id, f"⚠️ Could not show a report: {html.escape(str(e))}")
        if len(reports) > MAX_REPORTS_SHOWN:
            await bot.send_message(chat_id, f"…showing the first {MAX_REPORTS_SHOWN}.")

    # Jump straight to the nearest day that has reports.
    earlier = [d for d in days if d < day]
    later = [d for d in days if d > day]
    nav = []
    if earlier:
        nav.append(InlineKeyboardButton(f"◀ {earlier[-1]:%d %b}",
                                        callback_data=f"{REPORTS_DAY}{target_id}:{earlier[-1]:%Y%m%d}"))
    if later:
        nav.append(InlineKeyboardButton(f"{later[0]:%d %b} ▶",
                                        callback_data=f"{REPORTS_DAY}{target_id}:{later[0]:%Y%m%d}"))
    rows = [nav] if nav else []
    if day != local_today() and (not later or later[0] != local_today()):
        rows.append([InlineKeyboardButton("📅 Today",
                                          callback_data=f"{REPORTS_DAY}{target_id}:{local_today():%Y%m%d}")])
    if not own:
        rows.append([InlineKeyboardButton("◀ Back to user", callback_data=f"{USER_CARD}{target_id}:0")])
    footer = f"Days with reports: {len(days)}." if days else "No reports yet."
    await bot.send_message(chat_id, footer, reply_markup=InlineKeyboardMarkup(rows) if rows else None)


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


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all users, USERS_PER_PAGE at a time; tap one to manage them."""
    query = update.callback_query
    admin = await _admin_from_update(update, context, clear_buttons=False)
    if not admin:
        return
    page = int(query.data[len(USERS_PAGE):]) if query else 0

    rows = db.list_users()
    pages = max(1, -(-len(rows) // USERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    chunk = rows[page * USERS_PER_PAGE:(page + 1) * USERS_PER_PAGE]

    admins = sum(1 for r in rows if r["user"].is_admin)
    blocked = sum(1 for r in rows if r["user"].is_suspended)
    waiting = sum(1 for r in rows if r["user"].is_pending)
    lines = [f"👥 <b>Users</b>: {len(rows)} ({admins} admin, {waiting} waiting, {blocked} blocked)"]
    if pages > 1:
        lines[0] += f" · page {page + 1}/{pages}"
    for r in chunk:
        u: User = r["user"]
        lines.append(
            f"{_badge(u)} {html.escape(u.display_name)} · <code>{u.telegram_id}</code>\n"
            f"      today {r['today_reports']} · total {r['total_reports']}"
        )
    if not chunk:
        lines.append("No users yet.")
    lines.append("\n⭐ super admin · 🛡 admin · 👤 user · ⏳ waiting for approval · ⛔ blocked\nTap a user to manage them.")

    buttons = [
        InlineKeyboardButton(f"{_badge(r['user'])} {_truncate(r['user'].full_name or str(r['user'].telegram_id), 24)}",
                             callback_data=f"{USER_CARD}{r['user'].telegram_id}:{page}")
        for r in chunk
    ]
    kb = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀ Prev", callback_data=f"{USERS_PAGE}{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("Next ▶", callback_data=f"{USERS_PAGE}{page + 1}"))
    if nav:
        kb.append(nav)
    await _show(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))


def _allowed_actions(admin: User, target: User) -> list[str]:
    """Any admin can approve, block / unblock and make admin. Only super admins can remove an
    admin or block one. Nobody can change a super admin or themselves."""
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
                            target: User, page: int) -> None:
    today, total = db.user_report_counts(target.telegram_id)
    role = "Super admin" if target.is_super_admin else "Admin" if target.is_admin else "User"
    text = (
        f"{_badge(target)} <b>{html.escape(target.display_name)}</b>\n"
        f"ID: <code>{target.telegram_id}</code>\n"
        f"Role: {role}\n"
        f"Status: {'⛔ Blocked' if target.is_suspended else '⏳ Waiting for approval' if target.is_pending else 'Approved'}\n"
        f"Reports: today {today} · total {total}\n"
        f"Joined: {to_local(target.created_at):%Y-%m-%d} · last seen {to_local(target.last_seen_at):%Y-%m-%d %H:%M}"
    )
    actions = _allowed_actions(admin, target)
    if target.is_super_admin:
        text += "\n\nSuper admins are set in SUPER_ADMIN_IDS and can't be changed here."
    elif target.telegram_id == admin.telegram_id:
        text += "\n\nThis is you."
    elif not actions:
        text += "\n\nOnly super admins can remove or block other admins."

    kb = [[InlineKeyboardButton(ACTIONS[a][0], callback_data=f"{USER_ACTION}{target.telegram_id}:{a}:{page}")
           for a in actions]] if actions else []
    kb.append([InlineKeyboardButton("📂 Reports",
                                    callback_data=f"{REPORTS_DAY}{target.telegram_id}:{local_today():%Y%m%d}")])
    kb.append([InlineKeyboardButton("◀ Back to users", callback_data=f"{USERS_PAGE}{page}")])
    await _show(update, context, text, InlineKeyboardMarkup(kb))


async def user_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = await _admin_from_update(update, context, clear_buttons=False)
    if not admin:
        return
    _, target_id, page = update.callback_query.data.split(":")
    target = db.get_user(int(target_id))
    if not target:
        await context.bot.send_message(update.effective_chat.id, "User not found.")
        return
    await _render_user_card(update, context, admin, target, int(page))


async def user_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, target_id, action, page = query.data.split(":")
    admin = _register(update, context)
    target = db.get_user(int(target_id))
    if not admin.is_admin or admin.is_suspended:
        await query.answer("Only for admins.", show_alert=True)
        return
    if not target or action not in _allowed_actions(admin, target):
        # The card was out of date (someone else changed this user); show the current one.
        await query.answer("Not allowed or already changed.", show_alert=True)
        if target:
            await _render_user_card(update, context, admin, target, int(page))
        return
    target = await apply_user_change(context.application, target.telegram_id, action)
    log.info("Admin %s: %s -> %s", admin.telegram_id, action, target.telegram_id)
    await query.answer(f"{target.full_name or target.telegram_id}: {action.replace('_', ' ')} done.")
    await _render_user_card(update, context, admin, target, int(page))


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


def _group_albums(reports: list[Report]) -> list[list[Report]]:
    """Group consecutive photos from the same album so they can be sent together."""
    groups: list[list[Report]] = []
    for r in reports:
        if (
            groups
            and r.kind == "photo"
            and r.media_group_id
            and groups[-1][-1].kind == "photo"
            and groups[-1][-1].media_group_id == r.media_group_id
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
            InputMediaPhoto(
                r.file_id,
                caption=_caption(reporter, r, CAPTION_LIMIT) if (i == 0 or r.text) else None,
                parse_mode=ParseMode.HTML,
            )
            for i, r in enumerate(group)
        ]
        await bot.send_media_group(chat_id, media)
    elif first.kind == "photo":
        await bot.send_photo(chat_id, first.file_id, caption=_caption(reporter, first, CAPTION_LIMIT),
                             parse_mode=ParseMode.HTML)
    elif first.kind == "document":
        await bot.send_document(chat_id, first.file_id, caption=_caption(reporter, first, CAPTION_LIMIT),
                                parse_mode=ParseMode.HTML)
    else:
        await bot.send_message(chat_id, _caption(reporter, first, TEXT_LIMIT), parse_mode=ParseMode.HTML)


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

    if msg.photo:
        report_id = db.add_report(user.telegram_id, "photo", msg.caption, msg.photo[-1].file_id, msg.media_group_id)
    elif msg.document:
        report_id = db.add_report(user.telegram_id, "document", msg.caption, msg.document.file_id, msg.media_group_id)
    elif msg.text:
        report_id = db.add_report(user.telegram_id, "text", msg.text)
    else:
        return

    if config.LIVE_REPORTS:
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


async def _deliver_live(app: Application, reporter: User, report_ids: list[int]) -> None:
    reports = db.get_reports(report_ids)
    if not reports:
        return
    for admin_id in db.active_admin_ids():
        if admin_id == reporter.telegram_id:
            continue
        delivered: list[int] = []
        try:
            await app.bot.send_message(admin_id, "🆕 <b>New report</b>", parse_mode=ParseMode.HTML)
            for group in _group_albums(reports):
                await _send_group(app.bot, admin_id, reporter, group)
                delivered.extend(r.id for r in group)
        except TelegramError as e:
            # e.g. the admin blocked the bot or never pressed /start. They'll still see it via /read.
            log.warning("Live delivery to admin %s failed: %s", admin_id, e)
        if config.LIVE_MARKS_READ and delivered:
            db.mark_read(admin_id, delivered)


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Please send your report as text or an image.")


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

    # Reply keyboard buttons. Registered before receive_report so their text isn't saved as a report.
    for text, handler in [(BTN_SEND, send_report_prompt), (BTN_MINE, my_reports), (BTN_READ, read),
                          (BTN_STATUS, status), (BTN_USERS, users)]:
        app.add_handler(MessageHandler(private & filters.Text([text]), handler))

    app.add_handler(CallbackQueryHandler(read, pattern=f"^({READ_NEXT}|{MENU_READ})$"))
    app.add_handler(CallbackQueryHandler(status, pattern=f"^{MENU_STATUS}$"))
    app.add_handler(CallbackQueryHandler(users, pattern=rf"^{USERS_PAGE}\d+$"))
    app.add_handler(CallbackQueryHandler(user_card, pattern=rf"^{USER_CARD}\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(user_action, pattern=rf"^{USER_ACTION}\d+:[a-z_]+:\d+$"))
    app.add_handler(CallbackQueryHandler(reports_day, pattern=rf"^{REPORTS_DAY}\d+:\d{{8}}$"))
    app.add_handler(
        MessageHandler(
            private & (filters.PHOTO | filters.Document.IMAGE | (filters.TEXT & ~filters.COMMAND)),
            receive_report,
        )
    )
    app.add_handler(MessageHandler(private & ~filters.COMMAND, unsupported))
    app.add_error_handler(on_error)
    return app
