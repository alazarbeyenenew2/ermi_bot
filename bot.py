"""Telegram bot: users send reports (text, photos, files…), admins read them and manage users.

Every text shown in Telegram lives in texts_am.py / texts_en.py; each person sees their own language.
"""
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone

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

import backup
import config
import texts
from db import Report, User, day_range_utc, db, local_today, to_local

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
REPORTS_PER_PAGE = 20
USERS_PER_PAGE = 10

# Inline button callback data. <pos> is a users-list position: filter letter + page, e.g. "p0".
READ_NEXT = "read_next"
OLD_MENU_READ = "menu:read"      # buttons from earlier versions
OLD_MENU_STATUS = "menu:status"
USERS_PAGE = "users:"    # users:<pos>
USER_CARD = "user:"      # user:<telegram_id>:<pos>
USER_ACTION = "act:"     # act:<telegram_id>:<action>:<pos>   (then shows the user card)
QUICK_ACTION = "qa:"     # qa:<telegram_id>:<action>:<pos>    (then shows the users list)
REPORTS_MENU = "rpm:"    # rpm:<telegram_id>[:e]   0 = all users; ":e" = replace the tapped message
REPORTS = "rp:"          # rp:<telegram_id>:<period>:<offset>
SET_LANGUAGE = "lang:"   # lang:<code>
DELETE_REPORT = "del"    # del:<s|g>:<report_id>, delok:…, delno:…   (s = one report, g = its whole album)
REMOVE_USER = "rmu"      # rmu:<telegram_id>:<pos> (ask), rmuok:<telegram_id>:<pos> (do it)
BACKUP = "bk:"           # bk:now, bk:on, bk:off
EDIT_REPORT = "edit"     # edit:<report_id> (start), editno:<report_id> (cancel)
EDIT_WINDOW = timedelta(hours=12)  # users can edit a report this long after sending it

# Users list filters: letter -> test
FILTERS = {
    "a": lambda u: True,
    "p": lambda u: u.is_pending,
    "c": lambda u: not u.is_suspended and not u.is_pending,
    "s": lambda u: u.is_suspended,
}

# The actions an admin can take on a user (labels and messages are in the texts modules).
ACTIONS = tuple(texts.get(None).ACTIONS)


def tr(user: User | None):
    """The texts module for a person's language."""
    return texts.get(user.language if user else None)


def _commands(user: User) -> list[BotCommand]:
    L = tr(user)
    commands = [BotCommand("start", L.CMD_START), BotCommand("mine", L.CMD_MINE)]
    if user.is_admin:
        commands += [BotCommand("read", L.CMD_READ), BotCommand("status", L.CMD_STATUS),
                     BotCommand("users", L.CMD_USERS), BotCommand("reports", L.CMD_REPORTS)]
    elif user.is_manager:
        commands += [BotCommand("read", L.CMD_READ), BotCommand("status", L.CMD_STATUS),
                     BotCommand("reports", L.CMD_REPORTS)]
    if user.is_super_admin:
        commands.append(BotCommand("backup", L.CMD_BACKUP))
    return commands + [BotCommand("language", L.CMD_LANGUAGE)]


def main_keyboard(user: User) -> ReplyKeyboardMarkup:
    L = tr(user)
    rows = [[KeyboardButton(L.BTN_SEND), KeyboardButton(L.BTN_MINE)]]
    if user.is_admin and not user.is_suspended:
        rows += [[KeyboardButton(L.BTN_READ), KeyboardButton(L.BTN_STATUS)],
                 [KeyboardButton(L.BTN_USERS), KeyboardButton(L.BTN_ALL)]]
    elif user.can_view_reports:  # managers: reports, but no user management
        rows += [[KeyboardButton(L.BTN_READ), KeyboardButton(L.BTN_STATUS)], [KeyboardButton(L.BTN_ALL)]]
    if user.is_super_admin:
        rows.append([KeyboardButton(L.BTN_BACKUP), KeyboardButton(texts.LANGUAGE_BUTTON)])
    else:
        rows.append([KeyboardButton(texts.LANGUAGE_BUTTON)])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True,
                               input_field_placeholder=L.KEYBOARD_PLACEHOLDER)


def language_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(name, callback_data=f"{SET_LANGUAGE}{code}")
                                  for code, name in texts.LANGUAGE_NAMES.items()]])


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _pairs(buttons: list) -> list[list]:
    """Lay buttons out two per row."""
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


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
    for admin_id in db.active_admin_ids():
        L = tr(db.get_user(admin_id))
        keyboard = InlineKeyboardMarkup([[
            _button(L.ACTIONS["approve"][0], f"{USER_ACTION}{user.telegram_id}:approve:p0"),
            _button(L.ACTIONS["block"][0], f"{USER_ACTION}{user.telegram_id}:block:p0"),
        ]])
        await notify(app, admin_id, L.new_user_alert(html.escape(user.display_name), user.telegram_id),
                     reply_markup=keyboard)


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _when(iso: str, L) -> str:
    local = to_local(iso)
    if local.date() == local_today():
        return f"{L.TODAY} {local:%H:%M}"
    return f"{local:%d/%m/%Y %H:%M}"


def _caption(user: User, report: Report, limit: int, L) -> str:
    when = _when(report.created_at, L) + (f" · {L.EDITED_MARK}" if report.edited_at else "")
    header = L.caption_header(html.escape(user.display_name), when)
    if report.file_name:
        header += "\n" + L.caption_file(html.escape(_truncate(report.file_name, 200)))
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
        await context.bot.send_message(update.effective_chat.id, tr(user).ONLY_ADMINS,
                                       reply_markup=main_keyboard(user))
        return None
    return user


async def _viewer_from_update(update: Update, context: ContextTypes.DEFAULT_TYPE,
                              clear_buttons: bool = True) -> User | None:
    """Like _admin_from_update, but managers are allowed too (reading reports)."""
    await _answer_tap(update, clear_buttons)
    user = _register(update, context)
    if not user.can_view_reports:
        await context.bot.send_message(update.effective_chat.id, tr(user).ONLY_ADMINS,
                                       reply_markup=main_keyboard(user))
        return None
    return user


async def _show(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str,
                keyboard: InlineKeyboardMarkup | None) -> None:
    """Replace the tapped message (keeps the chat tidy), or send a new one."""
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


async def set_commands_for(app: Application, user: User) -> None:
    """The command menu, in the user's language; admin commands only for admins."""
    try:
        await app.bot.set_my_commands(_commands(user), scope=BotCommandScopeChat(user.telegram_id))
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
    await notify(app, telegram_id, tr(user).ACTIONS[action][1], reply_markup=main_keyboard(user))
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
    elif action == "make_manager":
        db.set_role(telegram_id, "manager")
        db.set_approved(telegram_id, True)
    elif action == "remove_manager":
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
    if not user.language:
        # First time: ask for a language, then welcome them in it.
        await update.message.reply_text(texts.LANGUAGE_PROMPT, parse_mode=ParseMode.HTML,
                                        reply_markup=language_picker())
        return
    await set_commands_for(context.application, user)
    await _welcome(context, update.effective_chat.id, user, update.effective_user.first_name)


async def _welcome(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user: User, first_name: str | None) -> None:
    L = tr(user)
    text = L.welcome(html.escape(first_name or ""))
    if user.is_suspended:
        text += L.BLOCKED
    elif user.is_pending:
        text += L.PENDING
    else:
        text += L.WELCOME_USER + (L.WELCOME_ADMIN if user.is_admin else L.WELCOME_MANAGER if user.is_manager else "")
    await context.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(user))


async def choose_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The 🌐 button or /language: show the language picker."""
    _register(update, context)
    await update.message.reply_text(texts.LANGUAGE_PROMPT, parse_mode=ParseMode.HTML,
                                    reply_markup=language_picker())


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """lang:<code>: save the language, then show the welcome (and keyboard) in it."""
    query = update.callback_query
    code = query.data[len(SET_LANGUAGE):]
    if code not in texts.LANGUAGES:
        await query.answer()
        return
    user = _register(update, context)
    first_time = not user.language
    db.set_language(user.telegram_id, code)
    user = db.get_user(user.telegram_id)
    L = tr(user)
    await query.answer()
    # The picker becomes a record of the choice; the next message brings the keyboard in the new language.
    await _show(update, context, f"🌐 {texts.LANGUAGE_NAMES[code]}", None)
    await set_commands_for(context.application, user)
    if first_time:
        await _welcome(context, update.effective_chat.id, user, update.effective_user.first_name)
    else:
        await context.bot.send_message(update.effective_chat.id, L.LANGUAGE_SET, parse_mode=ParseMode.HTML,
                                       reply_markup=main_keyboard(user))


async def send_report_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    L = tr(user)
    text = L.BLOCKED if user.is_suspended else L.PENDING if user.is_pending else L.SEND_PROMPT + L.EDIT_HELP
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(user))


# ---------- reports by period (own reports, or any user's / everyone's for admins) ----------
def _period_range(period: str) -> tuple[str | None, str | None]:
    """UTC [start, end) for a period in the configured timezone; (None, None) for all time."""
    today = local_today()
    first = {"t": today, "w": today - timedelta(days=today.weekday()), "m": today.replace(day=1)}.get(period)
    if first is None:
        return None, None
    return day_range_utc(first)[0], day_range_utc(today)[1]


def _period_rows(L, target_id: int, current: str | None = None) -> list[list[InlineKeyboardButton]]:
    return _pairs([_button(("• " if p == current else "") + label, f"{REPORTS}{target_id}:{p}:0")
                   for p, label in L.PERIODS.items()])


def _can_view(viewer: User, target_id: int) -> bool:
    if target_id == viewer.telegram_id:
        return True
    return viewer.can_view_reports


def _reports_title(viewer: User, target_id: int) -> str:
    L = tr(viewer)
    if target_id == viewer.telegram_id:
        return L.TITLE_MINE
    if target_id == 0:
        return L.TITLE_ALL
    target = db.get_user(target_id)
    return L.title_user(html.escape(target.display_name if target else str(target_id)))


def _back_from_reports(viewer: User, target_id: int) -> list[list[InlineKeyboardButton]]:
    """A back button for reports reached from the users screens (admins only; managers have no users screens)."""
    if target_id == viewer.telegram_id or not viewer.is_admin:
        return []
    L = tr(viewer)
    if target_id == 0:
        return [[_button(L.BACK, f"{USERS_PAGE}a0")]]
    return [[_button(L.BACK, f"{USER_CARD}{target_id}:a0")]]


async def _reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, viewer: User,
                        target_id: int, in_place: bool) -> None:
    L = tr(viewer)
    if not _can_view(viewer, target_id):
        await context.bot.send_message(update.effective_chat.id, L.ONLY_ADMINS)
        return
    text = f"📂 <b>{_reports_title(viewer, target_id)}</b>\n{L.LINE}\n{L.CHOOSE_PERIOD}"
    rows = _period_rows(L, target_id) + (_back_from_reports(viewer, target_id) if in_place else [])
    if in_place:
        await _show(update, context, text, InlineKeyboardMarkup(rows))
    else:
        await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML,
                                       reply_markup=InlineKeyboardMarkup(rows))


async def my_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    await _reports_menu(update, context, user, user.telegram_id, in_place=False)


async def all_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    viewer = await _viewer_from_update(update, context)
    if viewer:
        await _reports_menu(update, context, viewer, 0, in_place=False)


async def reports_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """rpm:<id>[:e] (also old rep:<id>:<day> buttons from earlier versions)."""
    viewer = _register(update, context)
    await _answer_tap(update, clear_buttons=False)
    parts = update.callback_query.data.split(":")
    in_place = parts[0] == "rpm" and len(parts) == 3
    await _reports_menu(update, context, viewer, int(parts[1]), in_place)


async def reports_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """rp:<id>:<period>:<offset>: up to REPORTS_PER_PAGE reports, newest first."""
    _, target, period, offset = update.callback_query.data.split(":")
    target_id, offset = int(target), int(offset)
    viewer = _register(update, context)
    L = tr(viewer)
    await update.callback_query.answer()
    chat_id = update.effective_chat.id
    bot = context.bot
    if not _can_view(viewer, target_id):
        await bot.send_message(chat_id, L.ONLY_ADMINS)
        return

    user_filter = target_id or None
    start, end = _period_range(period)
    total = db.count_reports(user_filter, start, end)
    page = db.reports_newest_first(user_filter, start, end, REPORTS_PER_PAGE, offset)

    # The tapped menu becomes the title, so the chat reads: title, reports, then the next choices.
    title = f"📂 <b>{_reports_title(viewer, target_id)}</b>\n{L.PERIODS[period]}\n{L.LINE}\n"
    if not page:
        title += L.NO_REPORTS
    else:
        title += L.report_count(total)
        if total > len(page):
            title += "\n<i>" + (L.showing_newest(len(page)) if offset == 0
                                else L.showing_older(offset + 1, offset + len(page))) + "</i>"
    await _show(update, context, title, None)

    reporters: dict[int, User] = {}
    deletable = viewer.is_super_admin  # only super admins delete reports
    editable = target_id == viewer.telegram_id  # people edit only their own reports
    # Oldest first within the page, so albums keep their order.
    for group in _group_albums(list(reversed(page))):
        reporter = reporters.setdefault(group[0].user_id, db.get_user(group[0].user_id))
        try:
            await _send_group(bot, chat_id, reporter, group, L, deletable, editable)
        except TelegramError as e:
            log.warning("Could not show reports %s: %s", [r.id for r in group], e)
            await bot.send_message(chat_id, L.could_not_show(html.escape(str(e))))

    rows = []
    remaining = total - offset - len(page)
    if remaining > 0:
        rows.append([_button(L.older_button(remaining), f"{REPORTS}{target_id}:{period}:{offset + REPORTS_PER_PAGE}")])
    rows += _period_rows(L, target_id, period) + _back_from_reports(viewer, target_id)
    await bot.send_message(chat_id, L.OTHER_PERIOD, reply_markup=InlineKeyboardMarkup(rows))


# ---------- admin: status ----------
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = await _viewer_from_update(update, context)
    if not user:
        return
    L = tr(user)
    s = db.today_stats(user.telegram_id)
    pending = db.unread_users_today(user.telegram_id)
    # Only admins can accept people, so only they hear about who's waiting.
    waiting = sum(1 for r in db.list_users() if r["user"].is_pending) if user.is_admin else 0
    text = L.status(s["total"], s["reporters"], s["unread"], len(pending), waiting)
    if pending:
        text += L.STATUS_UNREAD_TITLE + "\n" + "\n".join(
            f"• {html.escape(u.display_name)} — {n}" for u, n in pending[:30])
        if len(pending) > 30:
            text += "\n" + L.status_more(len(pending) - 30)
    rows = [[_button(L.BTN_READ, READ_NEXT)]] if pending else []
    await context.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML,
                                   reply_markup=InlineKeyboardMarkup(rows) if rows else None)


# ---------- admin: users ----------
def _badge(u: User) -> str:
    badge = ("⭐" if u.is_super_admin else "🛡" if u.is_admin else "📋" if u.is_manager
             else "⏳" if u.is_pending else "👤")
    return badge + ("⛔" if u.is_suspended else "")


# Users-list sections, in display order.
SECTION_ORDER = ("pending", "admins", "managers", "users", "suspended")


def _section(u: User) -> str:
    if u.is_suspended:
        return "suspended"
    if u.is_pending:
        return "pending"
    return "admins" if u.is_admin else "managers" if u.is_manager else "users"


def _parse_pos(token: str) -> tuple[str, int]:
    """'p2' -> ('p', 2); a bare page number (older buttons) means the 'All' filter."""
    if token and token[0] in FILTERS:
        return token[0], int(token[1:] or 0)
    return "a", int(token or 0)


def _allowed_actions(admin: User, target: User) -> list[str]:
    """Any admin can accept, suspend / activate, make admins and make / remove managers.
    Only super admins can remove an admin or suspend one. Nobody can change a super admin or themselves."""
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
        actions.append("remove_manager" if target.role == "manager" else "make_manager")
    if not target.is_admin or admin.is_super_admin:
        actions.append("unblock" if target.is_suspended else "block")
    return actions


NOOP = "noop"  # an empty button that keeps rows lined up


def _blank() -> InlineKeyboardButton:
    return _button(" ", NOOP)


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Users list: grouped into sections (pending, admins, managers, users, suspended), filter tabs,
    USERS_PER_PAGE per page. Every row has the same buttons for the current tab, so the list lines up."""
    query = update.callback_query
    admin = await _admin_from_update(update, context, clear_buttons=False)
    if not admin:
        return
    await _render_users(update, context, admin, query.data[len(USERS_PAGE):] if query else "a0")


def _row_buttons(L, admin: User, u: User, flt: str, pos: str) -> list[InlineKeyboardButton]:
    """The buttons after a user's name: the same shape for every row of a tab."""
    def act(action: str, label: str) -> InlineKeyboardButton:
        if action in _allowed_actions(admin, u):
            return _button(label, f"{QUICK_ACTION}{u.telegram_id}:{action}:{pos}")
        return _blank()
    reports = _button("📂", f"{REPORTS_MENU}{u.telegram_id}:e")
    if flt == "p":
        return [act("approve", "✅"), act("block", "⛔")]
    if flt == "s":
        return [act("unblock", "▶️"), reports]
    return [reports]


async def _render_users(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: User, pos: str) -> None:
    L = tr(admin)
    flt, page = _parse_pos(pos)
    everyone = db.list_users()
    rows = sorted((r for r in everyone if FILTERS[flt](r["user"])),
                  key=lambda r: (SECTION_ORDER.index(_section(r["user"])),
                                 not r["user"].is_super_admin, (r["user"].full_name or "").casefold()))
    pages = max(1, -(-len(rows) // USERS_PER_PAGE))
    page = min(max(page, 0), pages - 1)
    pos = f"{flt}{page}"
    first = page * USERS_PER_PAGE
    chunk = rows[first:first + USERS_PER_PAGE]

    # Text: a header, then each section with its numbered users.
    lines = [L.users_title(L.FILTERS[flt], len(rows), page + 1, pages)]
    section = None
    for i, r in enumerate(chunk):
        u = r["user"]
        if _section(u) != section:
            section = _section(u)
            lines.append(("\n" if i else "") + L.SECTIONS[section])
        name = ("⭐ " if u.is_super_admin else "") + html.escape(_truncate(u.display_name, 30))
        lines.append(L.users_row(first + i + 1, name, r["today_reports"], r["total_reports"]))
    if not chunk:
        lines.append(L.USERS_EMPTY)
    lines.append(L.USERS_LEGEND + "\n<i>" + L.USERS_HINT[flt] + "</i>")

    # Buttons: filter tabs (2 x 2), one uniform row per user, a pager, everyone's reports.
    counts = {f: sum(1 for r in everyone if test(r["user"])) for f, test in FILTERS.items()}
    kb = _pairs([_button(("• " if f == flt else "") + f"{L.FILTERS[f]} {counts[f]}", f"{USERS_PAGE}{f}0")
                 for f in FILTERS])
    for i, r in enumerate(chunk):
        u = r["user"]
        name = _button(f"{first + i + 1}. {_badge(u)} {_truncate(u.full_name or str(u.telegram_id), 18)}",
                       f"{USER_CARD}{u.telegram_id}:{pos}")
        kb.append([name] + _row_buttons(L, admin, u, flt, pos))
    if pages > 1:
        kb.append([
            _button(L.PREV, f"{USERS_PAGE}{flt}{page - 1}") if page > 0 else _blank(),
            _button(L.page_label(page + 1, pages), NOOP),
            _button(L.NEXT, f"{USERS_PAGE}{flt}{page + 1}") if page < pages - 1 else _blank(),
        ])
    kb.append([_button(L.ALL_USERS_REPORTS, f"{REPORTS_MENU}0:e")])
    await _show(update, context, "\n".join(lines), InlineKeyboardMarkup(kb))


async def _render_user_card(update: Update, context: ContextTypes.DEFAULT_TYPE, admin: User,
                            target: User, pos: str, confirm_remove: bool = False) -> None:
    L = tr(admin)
    today, total = db.user_report_counts(target.telegram_id)
    role = (L.ROLE_SUPER if target.is_super_admin else L.ROLE_ADMIN if target.is_admin
            else L.ROLE_MANAGER if target.is_manager else L.ROLE_USER)
    state = L.STATE_SUSPENDED if target.is_suspended else L.STATE_PENDING if target.is_pending else L.STATE_ACTIVE
    text = L.user_card(
        html.escape(target.display_name), target.telegram_id, role, state, today, total,
        f"{to_local(target.created_at):%d/%m/%Y}", _when(target.last_seen_at, L),
    )
    actions = _allowed_actions(admin, target)
    if target.is_super_admin:
        text += L.CARD_SUPER
    elif target.telegram_id == admin.telegram_id:
        text += L.CARD_SELF
    elif not actions:
        text += L.CARD_ADMIN_ONLY_SUPER

    if confirm_remove:
        text += L.remove_user_confirm(html.escape(target.display_name), total)
        kb = [[_button(L.REMOVE_USER_YES, f"{REMOVE_USER}ok:{target.telegram_id}:{pos}")],
              [_button(L.CANCEL, f"{USER_CARD}{target.telegram_id}:{pos}")]]
        await _show(update, context, text, InlineKeyboardMarkup(kb))
        return

    kb = _pairs([_button(L.ACTIONS[a][0], f"{USER_ACTION}{target.telegram_id}:{a}:{pos}") for a in actions])
    if _can_remove(admin, target):
        kb.append([_button(L.REMOVE_USER, f"{REMOVE_USER}:{target.telegram_id}:{pos}")])
    kb.append([_button(L.CARD_REPORTS, f"{REPORTS_MENU}{target.telegram_id}:e"),
               _button(L.CARD_BACK, f"{USERS_PAGE}{pos}")])
    await _show(update, context, text, InlineKeyboardMarkup(kb))


def _can_remove(admin: User, target: User) -> bool:
    """Only super admins remove users, and never a super admin or themselves."""
    return (admin.is_super_admin and not admin.is_suspended and not target.is_super_admin
            and target.telegram_id != admin.telegram_id)


async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """rmu:<id>:<pos> asks for confirmation on the user card; rmuok:<id>:<pos> removes the user."""
    query = update.callback_query
    kind, target_id, pos = query.data.split(":")
    admin = _register(update, context)
    L = tr(admin)
    target = db.get_user(int(target_id))
    if not target:
        await query.answer(L.USER_NOT_FOUND, show_alert=True)
        await _render_users(update, context, admin, pos)
        return
    if not _can_remove(admin, target):
        await query.answer(L.ONLY_SUPER, show_alert=True)
        return
    if kind == REMOVE_USER:
        await query.answer()
        await _render_user_card(update, context, admin, target, pos, confirm_remove=True)
        return
    deleted = db.delete_user(target.telegram_id)
    log.info("Super admin %s removed user %s (%s reports)", admin.telegram_id, target.telegram_id, deleted)
    await query.answer(L.user_removed(target.full_name or str(target.telegram_id), deleted), show_alert=True)
    await _render_users(update, context, admin, pos)


async def user_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    admin = await _admin_from_update(update, context, clear_buttons=False)
    if not admin:
        return
    _, target_id, pos = update.callback_query.data.split(":")
    target = db.get_user(int(target_id))
    if not target:
        await context.bot.send_message(update.effective_chat.id, tr(admin).USER_NOT_FOUND)
        return
    await _render_user_card(update, context, admin, target, pos)


async def user_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """act:… (from a user card or a new-user alert) and qa:… (from the users list)."""
    query = update.callback_query
    kind, target_id, action, pos = query.data.split(":")
    from_list = f"{kind}:" == QUICK_ACTION
    admin = _register(update, context)
    L = tr(admin)
    target = db.get_user(int(target_id))
    if not admin.is_admin or admin.is_suspended:
        await query.answer(L.ONLY_ADMINS, show_alert=True)
        return
    if not target or action not in _allowed_actions(admin, target):
        # The list / card was out of date (someone else changed this user); show the current one.
        await query.answer(L.NOT_ALLOWED, show_alert=True)
    else:
        target = await apply_user_change(context.application, target.telegram_id, action)
        log.info("Admin %s: %s -> %s", admin.telegram_id, action, target.telegram_id)
        label = L.ACTIONS[action][0].split(" ", 1)[-1]
        await query.answer(L.done(target.full_name or str(target.telegram_id), label))
    if from_list:
        await _render_users(update, context, admin, pos)
    elif target:
        await _render_user_card(update, context, admin, target, pos)


# ---------- super admin: delete reports ----------
async def delete_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """del:… asks to confirm, delno:… puts the 🗑 button back, delok:… deletes permanently.
    Only super admins can delete reports."""
    query = update.callback_query
    kind, scope, report_id = query.data.split(":")
    report_id = int(report_id)
    admin = _register(update, context)
    L = tr(admin)
    if not admin.is_super_admin:
        await query.answer(L.ONLY_SUPER, show_alert=True)
        return
    ids = db.album_report_ids(report_id) if scope == "g" else ([report_id] if db.get_report(report_id) else [])
    if not ids:
        await query.answer(L.ALREADY_DELETED, show_alert=True)
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass
        return
    first = db.get_report(ids[0])
    if kind == DELETE_REPORT:
        await query.answer()
        await query.edit_message_reply_markup(InlineKeyboardMarkup([[
            _button(L.CONFIRM_DELETE, f"{DELETE_REPORT}ok:{scope}:{report_id}"),
            _button(L.CANCEL, f"{DELETE_REPORT}no:{scope}:{report_id}"),
        ]]))
        return
    if kind == f"{DELETE_REPORT}no":
        await query.answer()
        await query.edit_message_reply_markup(_delete_markup(L, first, len(ids) if scope == "g" else 1))
        return
    deleted = db.delete_reports(ids)
    log.info("Admin %s deleted reports %s", admin.telegram_id, ids)
    await query.answer(L.reports_deleted(deleted))
    if scope == "s":
        # Remove the report from this chat too (bots can delete their own messages for 48 hours).
        try:
            await query.message.delete()
            return
        except TelegramError:
            pass
    try:
        await query.edit_message_reply_markup(None)
        if scope == "g":
            await query.edit_message_text(L.reports_deleted(deleted))
    except TelegramError:
        pass


# ---------- super admin: backup ----------
NIGHTLY_SETTING = "backup_nightly"   # "on" / "off"
LAST_BACKUP_SETTING = "last_backup"  # ISO time of the last backup sent
LAST_NIGHTLY_SETTING = "last_nightly_backup_date"


def _backup_menu(user: User) -> tuple[str, InlineKeyboardMarkup]:
    L = tr(user)
    nightly_on = db.get_setting(NIGHTLY_SETTING, "on") == "on"
    last = db.get_setting(LAST_BACKUP_SETTING)
    last_text = datetime.fromisoformat(last).astimezone(backup.ETHIOPIA).strftime("%d/%m/%Y %H:%M") if last else L.NEVER
    next_text = backup.next_midnight().strftime("%d/%m/%Y %H:%M")
    toggle = (_button(L.NIGHTLY_TURN_OFF, f"{BACKUP}off") if nightly_on else _button(L.NIGHTLY_TURN_ON, f"{BACKUP}on"))
    return (L.backup_menu(nightly_on, next_text, last_text),
            InlineKeyboardMarkup([[_button(L.BACKUP_NOW, f"{BACKUP}now")], [toggle]]))


async def backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """💾 button or /backup (super admins only)."""
    user = _register(update, context)
    if not user.is_super_admin:
        await update.message.reply_text(tr(user).ONLY_SUPER)
        return
    text, keyboard = _backup_menu(user)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def backup_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """bk:now / bk:on / bk:off from the backup menu."""
    query = update.callback_query
    user = _register(update, context)
    L = tr(user)
    if not user.is_super_admin:
        await query.answer(L.ONLY_SUPER, show_alert=True)
        return
    action = query.data[len(BACKUP):]
    await query.answer()
    if action in ("on", "off"):
        db.set_setting(NIGHTLY_SETTING, action)
        log.info("Super admin %s turned nightly backup %s", user.telegram_id, action)
    else:
        await _show(update, context, L.BACKUP_WORKING, None)
        await send_backup(context.application, [user.telegram_id], nightly=False)
    text, keyboard = _backup_menu(user)
    await _show(update, context, text, keyboard)


async def send_backup(app: Application, recipients, nightly: bool) -> None:
    """Make one zipped backup and send it to each recipient (in their language)."""
    data, file_name, counts = await asyncio.to_thread(backup.make_backup)
    when = backup.now_ethiopia().strftime("%d/%m/%Y %H:%M")
    for chat_id in recipients:
        L = tr(db.get_user(chat_id))
        try:
            if len(data) > backup.TELEGRAM_UPLOAD_LIMIT:
                await app.bot.send_message(chat_id, L.BACKUP_TOO_BIG)
                continue
            await _retry(lambda: app.bot.send_document(
                chat_id, document=data, filename=file_name, parse_mode=ParseMode.HTML,
                caption=L.backup_caption(when, counts["users"], counts["reports"], nightly)))
        except TelegramError as e:
            # e.g. a super admin who never pressed /start.
            log.warning("Could not send backup to %s: %s", chat_id, e)
    db.set_setting(LAST_BACKUP_SETTING, backup.now_ethiopia().isoformat())
    log.info("Backup %s sent to %s (%s bytes)", file_name, list(recipients), len(data))


async def nightly_backup_loop(app: Application) -> None:
    """Every night at 00:00 Ethiopian time, send a backup to every super admin (unless turned off)."""
    while True:
        wait = (backup.next_midnight() - backup.now_ethiopia()).total_seconds()
        await asyncio.sleep(max(wait, 1))
        today = backup.now_ethiopia().date().isoformat()
        if db.get_setting(NIGHTLY_SETTING, "on") != "on" or db.get_setting(LAST_NIGHTLY_SETTING) == today:
            continue
        db.set_setting(LAST_NIGHTLY_SETTING, today)
        try:
            await send_backup(app, sorted(config.SUPER_ADMIN_IDS), nightly=True)
        except Exception:
            log.exception("Nightly backup failed")
        await asyncio.sleep(60)  # never fire twice around the same midnight


# ---------- admin: read ----------
async def read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the next user's unread reports for today, then mark them read (admins and managers)."""
    admin = await _viewer_from_update(update, context)
    if not admin:
        return
    L = tr(admin)
    chat_id = update.effective_chat.id
    bot = context.bot

    pending = db.unread_users_today(admin.telegram_id)
    if not pending:
        await bot.send_message(chat_id, L.READ_ALL_DONE)
        return

    reporter, _ = pending[0]
    reports = db.unread_reports_today_for_user(admin.telegram_id, reporter.telegram_id)
    remaining_users = len(pending) - 1

    await bot.send_message(chat_id, L.read_header(html.escape(reporter.display_name), len(reports)),
                           parse_mode=ParseMode.HTML)

    sent_ids: list[int] = []
    for group in _group_albums(reports):
        try:
            await _send_group(bot, chat_id, reporter, group, L, deletable=admin.is_super_admin)
            sent_ids.extend(r.id for r in group)
        except TelegramError as e:
            log.exception("Failed to deliver reports %s", [r.id for r in group])
            await bot.send_message(chat_id, L.could_not_show(html.escape(str(e))))
            sent_ids.extend(r.id for r in group)  # don't get stuck on a broken file

    db.mark_read(admin.telegram_id, sent_ids)

    if remaining_users:
        await bot.send_message(
            chat_id, L.read_more(remaining_users), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[_button(L.next_user_button(remaining_users), READ_NEXT)]]),
        )
    else:
        await bot.send_message(chat_id, L.READ_LAST_DONE)


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


def _delete_markup(L, report: Report, album_size: int = 1) -> InlineKeyboardMarkup:
    if album_size > 1:
        return InlineKeyboardMarkup([[_button(L.delete_album(album_size), f"{DELETE_REPORT}:g:{report.id}")]])
    return InlineKeyboardMarkup([[_button(L.DELETE, f"{DELETE_REPORT}:s:{report.id}")]])


def _can_edit(report: Report) -> bool:
    """Users can edit a report for EDIT_WINDOW (12 hours) after sending it."""
    return datetime.now(timezone.utc) - datetime.fromisoformat(report.created_at) < EDIT_WINDOW


async def _send_group(bot, chat_id: int, reporter: User, group: list[Report], L, deletable: bool = False,
                      editable: bool = False) -> None:
    """Send one report (or one album) to a chat; captions use the recipient's language L.
    deletable: add a 🗑 button (super admins only). editable: add ✏️ for reports still inside the edit window."""
    first = group[0]
    if len(group) > 1:
        media = [
            ALBUM_MEDIA[r.kind](
                r.file_id,
                caption=_caption(reporter, r, CAPTION_LIMIT, L) if (i == 0 or r.text or r.file_name) else None,
                parse_mode=ParseMode.HTML,
            )
            for i, r in enumerate(group)
        ]
        await bot.send_media_group(chat_id, media)
        # Albums can't carry buttons, so their buttons come right after.
        rows = list(_delete_markup(L, first, len(group)).inline_keyboard) if deletable else []
        edits = [_button(L.edit_item(i + 1), f"{EDIT_REPORT}:{r.id}")
                 for i, r in enumerate(group) if editable and _can_edit(r)]
        rows += [edits[i:i + 5] for i in range(0, len(edits), 5)]
        if rows:
            await bot.send_message(chat_id, L.EDIT_ALBUM_HINT if edits else L.ALBUM_ABOVE,
                                   reply_markup=InlineKeyboardMarkup(rows))
        return
    caption = _caption(reporter, first, CAPTION_LIMIT, L)
    buttons = list(_delete_markup(L, first).inline_keyboard[0]) if deletable else []
    if editable and _can_edit(first):
        buttons.append(_button(L.EDIT, f"{EDIT_REPORT}:{first.id}"))
    markup = InlineKeyboardMarkup([buttons]) if buttons else None
    senders = {
        "photo": bot.send_photo, "video": bot.send_video, "animation": bot.send_animation,
        "document": bot.send_document, "audio": bot.send_audio, "voice": bot.send_voice,
    }
    if first.kind in senders:
        await senders[first.kind](chat_id, first.file_id, caption=caption, parse_mode=ParseMode.HTML,
                                  reply_markup=markup)
    elif first.kind == "video_note":
        # Round videos can't have a caption: say who sent it first.
        await bot.send_message(chat_id, caption, parse_mode=ParseMode.HTML)
        await bot.send_video_note(chat_id, first.file_id, reply_markup=markup)
    else:
        await bot.send_message(chat_id, _caption(reporter, first, TEXT_LIMIT, L), parse_mode=ParseMode.HTML,
                               reply_markup=markup)


def _report_from_message(msg) -> tuple[str, str | None, str | None, str | None] | None:
    """(kind, text, file_id, file_name) for a message, or None if it isn't something we save."""
    # Animations (GIFs) also carry msg.document, so check them before documents.
    for kind, media in [("photo", msg.photo[-1] if msg.photo else None), ("video", msg.video),
                        ("animation", msg.animation), ("document", msg.document), ("audio", msg.audio),
                        ("voice", msg.voice), ("video_note", msg.video_note)]:
        if media:
            return kind, msg.caption, media.file_id, getattr(media, "file_name", None)
    if msg.text:
        return "text", msg.text, None, None
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
        L = tr(user)
        reason = L.BLOCKED if user.is_suspended else L.PENDING
        await msg.reply_text(reason + L.NOT_SAVED, parse_mode=ParseMode.HTML, reply_markup=main_keyboard(user))
        return

    found = _report_from_message(msg)
    if not found:
        return
    kind, text, file_id, file_name = found

    editing = context.user_data.pop("edit_report", None)
    if editing:
        # The user tapped ✏️ and this message is the new version.
        await _apply_edit(context, user, editing, found, msg.message_id, msg.reply_text)
        return

    report_id = db.add_report(user.telegram_id, kind, text, file_id, msg.media_group_id, file_name,
                              msg.message_id)

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
    await msg.reply_text(tr(user).REPORT_RECEIVED, reply_markup=main_keyboard(user))


# ---------- editing reports (own reports, within EDIT_WINDOW) ----------
async def _apply_edit(context: ContextTypes.DEFAULT_TYPE, user: User, report_id: int, found: tuple,
                      message_id: int | None, reply) -> None:
    L = tr(user)
    report = db.get_report(report_id)
    if not report or report.user_id != user.telegram_id:
        await reply(L.EDIT_GONE)
        return
    if not _can_edit(report):
        await reply(L.EDIT_TOO_LATE)
        return
    kind, text, file_id, file_name = found
    db.update_report(report_id, kind, text, file_id, file_name, message_id)
    log.info("User %s edited report %s", user.telegram_id, report_id)
    await reply(L.REPORT_EDITED)
    # Admins see the new version (and it counts as unread again).
    context.application.create_task(_deliver_live(context.application, user, [report_id], edited=True))


async def edit_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """edit:<id> asks for the new version; editno:<id> cancels."""
    query = update.callback_query
    kind, report_id = query.data.split(":")
    report_id = int(report_id)
    user = _register(update, context)
    L = tr(user)
    if kind == f"{EDIT_REPORT}no":
        if context.user_data.get("edit_report") == report_id:
            context.user_data.pop("edit_report")
        await query.answer()
        await _show(update, context, L.EDIT_CANCELLED, None)
        return
    report = db.get_report(report_id)
    if not report or report.user_id != user.telegram_id:
        await query.answer(L.EDIT_GONE, show_alert=True)
        return
    if not user.can_report:
        await query.answer()
        await context.bot.send_message(update.effective_chat.id, L.BLOCKED if user.is_suspended else L.PENDING,
                                       parse_mode=ParseMode.HTML)
        return
    if not _can_edit(report):
        await query.answer(L.EDIT_TOO_LATE, show_alert=True)
        return
    await query.answer()
    context.user_data["edit_report"] = report_id
    await context.bot.send_message(
        update.effective_chat.id, L.EDIT_PROMPT, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[_button(L.CANCEL, f"{EDIT_REPORT}no:{report_id}")]]),
    )


async def on_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The user edited a message they sent: update the matching report, within EDIT_WINDOW."""
    msg = update.edited_message
    user = _register(update, context)
    report = db.report_for_message(user.telegram_id, msg.message_id)
    found = _report_from_message(msg)
    if not report or not found or not user.can_report:
        return
    await _apply_edit(context, user, report.id, found, None, msg.reply_text)


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


async def _deliver_live(app: Application, reporter: User, report_ids: list[int], edited: bool = False) -> None:
    """Send a new report to every active admin (except the one who sent it)."""
    reports = db.get_reports(report_ids)
    if not reports:
        return
    for admin_id in db.report_viewer_ids():  # admins and managers
        if admin_id == reporter.telegram_id:
            continue
        recipient = db.get_user(admin_id)
        L = tr(recipient)
        buttons = [_button(L.ALERT_THEIR_REPORTS, f"{REPORTS_MENU}{reporter.telegram_id}")]
        if recipient.is_admin:
            buttons.append(_button(L.ALERT_USER, f"{USER_CARD}{reporter.telegram_id}:a0"))
        keyboard = InlineKeyboardMarkup([buttons])
        header = (L.edited_alert(html.escape(reporter.display_name)) if edited
                  else L.new_report_alert(html.escape(reporter.display_name), len(reports)))
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
                await _retry(lambda: _send_group(app.bot, admin_id, reporter, group, L,
                                                 deletable=recipient.is_super_admin))
                delivered.extend(r.id for r in group)
            except TelegramError as e:
                log.warning("Live delivery of %s to admin %s failed: %s", [r.id for r in group], admin_id, e)
        if config.LIVE_MARKS_READ and delivered:
            db.mark_read(admin_id, delivered)


async def unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update, context)
    await update.message.reply_text(tr(user).UNSUPPORTED, parse_mode=ParseMode.HTML)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("Unhandled error while processing update", exc_info=context.error)


def build_application() -> Application:
    if not config.BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is not set (see .env.example)")
    app = Application.builder().token(config.BOT_TOKEN).build()
    # New messages only; edits of earlier messages go to on_edited_message.
    private = filters.ChatType.PRIVATE & filters.UpdateType.MESSAGE

    app.add_handler(CommandHandler("start", start, filters=private))
    app.add_handler(CommandHandler("help", start, filters=private))
    app.add_handler(CommandHandler("mine", my_reports, filters=private))
    app.add_handler(CommandHandler("read", read, filters=private))
    app.add_handler(CommandHandler("status", status, filters=private))
    app.add_handler(CommandHandler("users", users, filters=private))
    app.add_handler(CommandHandler("reports", all_reports, filters=private))
    app.add_handler(CommandHandler("language", choose_language, filters=private))
    app.add_handler(CommandHandler("backup", backup_menu, filters=private))

    # Reply keyboard buttons (and their labels from earlier versions). Registered before
    # receive_report so their text isn't saved as a report.
    for name, handler in [("BTN_SEND", send_report_prompt), ("BTN_MINE", my_reports), ("BTN_READ", read),
                          ("BTN_STATUS", status), ("BTN_USERS", users), ("BTN_ALL", all_reports)]:
        app.add_handler(MessageHandler(private & filters.Text(texts.labels(name)), handler))
    app.add_handler(MessageHandler(private & filters.Text([texts.LANGUAGE_BUTTON]), choose_language))
    app.add_handler(MessageHandler(private & filters.Text(texts.labels("BTN_BACKUP")), backup_menu))

    pos = r"[apcs]?\d+"  # users-list position; a bare number comes from older buttons
    app.add_handler(CallbackQueryHandler(read, pattern=f"^({READ_NEXT}|{OLD_MENU_READ})$"))
    app.add_handler(CallbackQueryHandler(status, pattern=f"^{OLD_MENU_STATUS}$"))
    app.add_handler(CallbackQueryHandler(users, pattern=rf"^{USERS_PAGE}{pos}$"))
    app.add_handler(CallbackQueryHandler(user_card, pattern=rf"^{USER_CARD}\d+:{pos}$"))
    app.add_handler(CallbackQueryHandler(user_action, pattern=rf"^({USER_ACTION}|{QUICK_ACTION})\d+:[a-z_]+:{pos}$"))
    app.add_handler(CallbackQueryHandler(reports_menu, pattern=rf"^({REPORTS_MENU}\d+(:e)?|rep:\d+:\d{{8}})$"))
    app.add_handler(CallbackQueryHandler(reports_page, pattern=rf"^{REPORTS}\d+:[twma]:\d+$"))
    app.add_handler(CallbackQueryHandler(set_language, pattern=rf"^{SET_LANGUAGE}[a-z]+$"))
    app.add_handler(CallbackQueryHandler(delete_report, pattern=rf"^{DELETE_REPORT}(ok|no)?:[sg]:\d+$"))
    app.add_handler(CallbackQueryHandler(remove_user, pattern=rf"^{REMOVE_USER}(ok)?:\d+:{pos}$"))
    app.add_handler(CallbackQueryHandler(backup_action, pattern=rf"^{BACKUP}(now|on|off)$"))
    app.add_handler(CallbackQueryHandler(edit_report, pattern=rf"^{EDIT_REPORT}(no)?:\d+$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=f"^{NOOP}$"))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.UpdateType.EDITED_MESSAGE, on_edited_message))
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
