"""Runs the Telegram bot (long polling) and the web admin page in one process."""
import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

import config
from bot import build_application, nightly_backup_loop
from web import create_web_app

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    tg_app = build_application()
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=False)
    app.state.telegram = tg_app
    log.info("Bot started as @%s", tg_app.bot.username)
    # Nightly zipped backup to the super admins, at 00:00 Ethiopian time.
    nightly = asyncio.create_task(nightly_backup_loop(tg_app))
    try:
        yield
    finally:
        nightly.cancel()
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()


app = create_web_app(lifespan=lifespan)

if __name__ == "__main__":
    if not config.ADMIN_PASSWORD:
        log.warning("ADMIN_PASSWORD is empty: the web admin page will refuse all logins.")
    uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT)
