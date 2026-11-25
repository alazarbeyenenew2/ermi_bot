"""Web admin page: manage users (approve, make admin, remove admin, block, unblock)."""
import hmac
import secrets
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
from db import db, to_local

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["local"] = lambda iso: to_local(iso).strftime("%Y-%m-%d %H:%M")


def create_web_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="Report Bot Admin", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware, secret_key=config.SECRET_KEY, same_site="strict", max_age=60 * 60 * 12
    )

    def logged_in(request: Request) -> bool:
        return request.session.get("admin") is True

    def check_csrf(request: Request, token: str) -> None:
        if not hmac.compare_digest(token, request.session.get("csrf", "")):
            raise HTTPException(400, "Invalid form token, reload the page and try again.")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, q: str = "", msg: str = ""):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        rows = db.list_users(q)
        stats = {
            "users": len(rows),
            "admins": sum(1 for r in rows if r["user"].is_admin),
            "blocked": sum(1 for r in rows if r["user"].is_suspended),
            "waiting": sum(1 for r in rows if r["user"].is_pending),
            "today": sum(r["today_reports"] for r in rows),
        }
        return templates.TemplateResponse(
            request,
            "users.html",
            {"rows": rows, "q": q, "msg": msg, "stats": stats, "csrf": request.session["csrf"]},
        )

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        return templates.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login(request: Request, username: str = Form(...), password: str = Form(...)):
        ok = bool(config.ADMIN_PASSWORD) and hmac.compare_digest(
            username, config.ADMIN_USERNAME
        ) & hmac.compare_digest(password, config.ADMIN_PASSWORD)
        if not ok:
            return templates.TemplateResponse(
                request, "login.html", {"error": "Wrong username or password."}, status_code=401
            )
        request.session.clear()
        request.session["admin"] = True
        request.session["csrf"] = secrets.token_urlsafe(32)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.post("/users/{telegram_id}/{action}")
    async def user_action(
        request: Request, telegram_id: int, action: str, csrf: str = Form(...), q: str = Form("")
    ):
        if not logged_in(request):
            return RedirectResponse("/login", status_code=303)
        check_csrf(request, csrf)

        user = db.get_user(telegram_id)
        if not user:
            raise HTTPException(404, "User not found")
        if user.is_super_admin:
            return _back(q, "Super admins (set in SUPER_ADMIN_IDS) can't be changed here.")

        from bot import ACTIONS, apply_user_change, change_user

        if action not in ACTIONS:
            raise HTTPException(400, "Unknown action")
        tg_app = getattr(request.app.state, "telegram", None)
        if tg_app is not None:
            # Also updates the user's command menu and keyboard, and tells them.
            await apply_user_change(tg_app, telegram_id, action)
        else:
            change_user(telegram_id, action)

        return _back(q, f"{user.display_name}: {action.replace('_', ' ')} done.")

    return app


def _back(q: str, msg: str) -> RedirectResponse:
    from urllib.parse import urlencode

    return RedirectResponse("/?" + urlencode({"q": q, "msg": msg}), status_code=303)
