"""Telegram bot: users send reports (text / images), admins read today's reports."""
import asyncio
import html
import logging

from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
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
from db import Report, User, db, to_local

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
TEXT_LIMIT = 4096
READ_NEXT = "read_next"

USER_COMMANDS = [
    BotCommand("start", "Start / help"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand("read", "Read today's reports (next user)"),
    BotCommand("status", "Today's report summary"),
]


# ---------- helpers ----------
def _register(update: Update) -> User:
    tg = update.effective_user
    return db.upsert_user(tg.id, tg.username, tg.full_name)


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


async def set_commands_for(app: Application, user: User) -> None:
    """Show admin commands in the menu only for admins."""
    try:
        await app.bot.set_my_commands(
            ADMIN_COMMANDS if user.is_admin else USER_COMMANDS,
            scope=BotCommandScopeChat(user.telegram_id),
        )
    except TelegramError as e:
        log.warning("Could not set commands for %s: %s", user.telegram_id, e)


async def notify(app: Application, telegram_id: int, text: str) -> None:
    try:
        await app.bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML)
    except TelegramError as e:
        log.warning("Could not notify %s: %s", telegram_id, e)


# ---------- commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update)
    await set_commands_for(context.application, user)
    if user.is_suspended:
        await update.message.reply_text("⛔ Your account is suspended. Contact an admin.")
        return

    text = (
        f"Hi {html.escape(update.effective_user.first_name or '')} 👋\n\n"
        "📝 To submit a report, just send me a <b>text message</b> or an <b>image</b> "
        "(add a caption to describe it). You can send as many as you need."
    )
    if user.is_admin:
        text += (
            "\n\n🛡 <b>You are an admin.</b>\n"
            "/read – read today's reports, one user at a time\n"
            "/status – summary of today's reports"
        )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = _register(update)
    if not user.is_admin or user.is_suspended:
        await update.message.reply_text("This command is only for admins.")
        return
    s = db.today_stats(user.telegram_id)
    pending = db.unread_users_today(user.telegram_id)
    text = (
        "📊 <b>Today's reports</b>\n"
        f"Reports: {s['total']} from {s['reporters']} user(s)\n"
        f"Unread by you: {s['unread']} from {len(pending)} user(s)"
    )
    if pending:
        text += "\n\n" + "\n".join(f"• {html.escape(u.display_name)} – {n}" for u, n in pending[:30])
        if len(pending) > 30:
            text += f"\n…and {len(pending) - 30} more"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def read(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the next user's unread reports for today, then mark them read."""
    query = update.callback_query
    if query:
        await query.answer()
        # Remove the "Next" button from the message that was tapped.
        try:
            await query.edit_message_reply_markup(None)
        except TelegramError:
            pass

    admin = _register(update)
    chat_id = update.effective_chat.id
    bot = context.bot

    if not admin.is_admin or admin.is_suspended:
        await bot.send_message(chat_id, "This command is only for admins.")
        return

    pending = db.unread_users_today(admin.telegram_id)
    if not pending:
        await bot.send_message(chat_id, "✅ You have read all of today's reports.")
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
        )
        await bot.send_message(
            chat_id, f"{remaining_users} more user(s) with unread reports.", reply_markup=keyboard
        )
    else:
        await bot.send_message(chat_id, "✅ That was the last one — all of today's reports are read.")


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
    user = _register(update)
    if user.is_suspended:
        await msg.reply_text("⛔ Your account is suspended. Your report was not saved.")
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
    await msg.reply_text("✅ Report received. Thank you!")


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
    app.add_handler(CommandHandler("read", read, filters=private))
    app.add_handler(CommandHandler("status", status, filters=private))
    app.add_handler(CallbackQueryHandler(read, pattern=f"^{READ_NEXT}$"))
    app.add_handler(
        MessageHandler(
            private & (filters.PHOTO | filters.Document.IMAGE | (filters.TEXT & ~filters.COMMAND)),
            receive_report,
        )
    )
    app.add_handler(MessageHandler(private & ~filters.COMMAND, unsupported))
    app.add_error_handler(on_error)
    return app
