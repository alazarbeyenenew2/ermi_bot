# Report Bot

A Telegram bot where users send reports (text and images), and admins read each day's reports, one user at a time. It comes with a web admin page for managing users.

## Features

**Users** (everyone is a user by default):
- Send any **text** or **image** (a photo, an album, or an image file; captions are kept) and it's saved as a report.
- Suspended users get a message saying so, and their reports are not saved.

**Admins** (in Telegram):
- **Live reports**: every new report is sent to all active admins the moment it arrives, marked "🆕 New report". Albums arrive together as one album. A report delivered this way counts as read, so `/read` only shows reports you missed, for example ones that arrived while you had blocked the bot. Set `LIVE_MARKS_READ=false` to have `/read` show everything anyway, or `LIVE_REPORTS=false` to turn live delivery off.
- `/read`: shows all of **today's** unread reports from the **next user**, then marks them as read. Tap **Next user ▶** to keep going until you get *"You have read all of today's reports."* Each admin has their own read state.
- `/status`: today's totals and which users still have unread reports.

**Web admin page** (`http://<server>:8000`):
- Lists all users with their role, status and report counts. You can search them.
- **Make admin**, **Remove admin**, **Suspend**, **Unsuspend**. The user gets a Telegram message about the change, and their command menu updates.
- Users listed in `SUPER_ADMIN_IDS` are always admins and can't be changed from the page.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env
.venv/bin/python main.py
```

1. Create a bot with [@BotFather](https://t.me/BotFather) and put its token in `BOT_TOKEN`.
2. Put your own Telegram ID in `SUPER_ADMIN_IDS` (you can get it from @userinfobot).
3. Set `ADMIN_PASSWORD` and a random `SECRET_KEY`.
4. Run `main.py`. It starts the bot (long polling) and the admin page together.
5. Send `/start` to the bot. Users show up on the admin page after their first `/start` or message.

## Deploy with Docker Compose

On the server (Docker and the Compose plugin must be installed):

```bash
git clone <your-repo> report-bot && cd report-bot   # or copy the folder with scp/rsync
cp .env.example .env && nano .env                     # fill in BOT_TOKEN, SUPER_ADMIN_IDS, ADMIN_PASSWORD, SECRET_KEY
docker compose up -d --build
docker compose logs -f bot                            # look for "Bot started as @..."
```

The database is stored in the `bot-data` Docker volume, so it survives rebuilds and restarts.

**Opening the admin page.** By default it's published only on the server's `127.0.0.1:8000`, so it is never exposed over plain HTTP. Choose one way in:

- **HTTPS with your own domain (recommended).** Point a DNS record (for example `admin.example.com`) at the server, open ports 80 and 443, set `DOMAIN=admin.example.com` in `.env`, then run:
  ```bash
  docker compose --profile https up -d --build
  ```
  Caddy gets a Let's Encrypt certificate automatically. Then open `https://admin.example.com`.
- **SSH tunnel (no domain needed).** On your own computer, run `ssh -L 8000:127.0.0.1:8000 user@server`, then open `http://localhost:8000`.
- **Public plain HTTP (not recommended).** Set `ADMIN_BIND=0.0.0.0` in `.env`.

**Common tasks**

| Task | Command |
|---|---|
| Update after code changes | `docker compose up -d --build` (add `--profile https` if you use it) |
| Restart | `docker compose restart bot` |
| Stop | `docker compose down` (data is kept) |
| Back up the database | `docker compose exec bot python -c "import sqlite3; sqlite3.connect('/app/data/bot.db').backup(sqlite3.connect('/app/data/backup.db'))" && docker compose cp bot:/app/data/backup.db ./backup-$(date +%F).db` |

Run only **one** copy of the bot. Telegram allows one polling connection per token, so a second copy (another server, or `main.py` still running on your laptop) causes `Conflict` errors.

## Deploy with GitHub Actions

`.github/workflows/deploy.yml` runs on every push to `main` (or by hand from the Actions tab). It checks the code (compile, renders the login page, builds the image), then over SSH it copies the code to the server, writes `.env`, backs up the database to `backups/` (keeps 14), runs `docker compose up -d --build`, and waits for "Bot started as @...".

The server needs Docker (usable by the SSH user without `sudo`), the Compose plugin and `rsync`. In the repository settings, set:

- **Secrets:** `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, `BOT_TOKEN`, `ADMIN_PASSWORD`, `SECRET_KEY`, and preferably `DEPLOY_KNOWN_HOSTS` (output of `ssh-keyscan -H <host>`).
- **Variables:** `SUPER_ADMIN_IDS` (required). Optional: `TIMEZONE`, `ADMIN_USERNAME`, `LIVE_REPORTS`, `LIVE_MARKS_READ`, `ADMIN_BIND`, `ADMIN_PORT`, `DEPLOY_PORT`, `DEPLOY_PATH` (default `report-bot`), and `DOMAIN` (when set, Caddy also serves the admin page over HTTPS).

The workflow overwrites the server's `.env` on every deploy, so change settings in GitHub, not on the server. Stop any other copy of the bot first (for example on your laptop), or Telegram will report `Conflict` errors.

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point: runs the bot and the web page in one process |
| `bot.py` | Telegram handlers (reports, `/read`, `/status`) |
| `web.py`, `templates/` | Web admin page |
| `db.py` | SQLite storage (`users`, `reports`, `report_reads`) |
| `config.py` | Settings from `.env` |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile` | Container deployment (optional HTTPS via Caddy) |

## Production notes
- Put the admin page behind HTTPS (for example nginx or Caddy as a reverse proxy).
- Back up the database regularly. It is a single file at `DATABASE_PATH`.
- Images stay on Telegram's servers. Only their `file_id` is stored.
