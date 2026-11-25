# Report Bot

A Telegram bot where users send reports (text, photos, videos, voice messages and any file), and admins read each day's reports, one user at a time. It comes with a web admin page for managing users.

## Features

**Amharic or English, per person.** The first time someone sends `/start`, the bot asks them to choose **🇪🇹 አማርኛ** or **🇬🇧 English**. They can switch any time with the **🌐 ቋንቋ / Language** button on their keyboard (or `/language`); their keyboard and command menu switch with them. Each message is in the language of the person receiving it, so an English-speaking admin and an Amharic-speaking user each see their own. People who used the bot before this was added are asked on their next `/start`; until then they see Amharic.

All texts live in [`texts_am.py`](texts_am.py) and [`texts_en.py`](texts_en.py), which define the same names; edit wording there. (The web admin page is in English. The button names below are the English ones.)

Everyone gets a keyboard under the text box. Users see **📝 Send report**, **📂 My reports** and **🌐 Language**; admins also get **📥 Read reports**, **📊 Summary**, **👥 Users** and **🗂 All reports**. The keyboard appears after `/start` (or with the reply to the next report sent). Buttons from older English keyboards keep working.

Menus (users list, a user's details, report periods) update the same message instead of sending new ones, to keep the chat tidy.

**Accepting new users**: new users are **pending** and can't send reports until an admin accepts them. When someone sends `/start` for the first time, every admin gets a message with **✅ Accept** and **⛔ Suspend** buttons, and the user is told they're waiting. Anything they send before that is not saved. Users who were already in the database when this was added stay active.

**Viewing reports**: **📂 My reports**, a user's **📂** button and **🗂 All reports** all ask which reports to show: **Today**, **This week** (from Monday), **This month** or **All**. The menu turns into a title, then the reports follow, 20 at a time (the newest first, **⬇️ older** for more), with the date on anything not from today. Viewing reports this way doesn't mark them as read.

**Users** (everyone is a user by default):
- Send a **text message**, **photo**, **video**, **GIF**, **file of any type** (PDF, Word, Excel, ZIP, …), **audio**, **voice message** or **round video**, and it's saved as a report. Albums are kept together and captions are kept. **📝 Send report** explains this. Stickers, locations and contacts aren't saved.
- Files stay on Telegram's servers; the bot only stores their Telegram file ID, so there's no size limit beyond Telegram's own.
- **📂 My reports** (or `/mine`): your own reports, by period. Uploaded files show their file name (📎) under the sender and time; this works for files sent after this was added.
- Suspended users get a message saying so, and their reports are not saved.

**Admins** (in Telegram):
- **Every new report is sent to all admins** the moment it arrives (always on), as "🆕 አዲስ ሪፖርት" (new report) with the sender's name and **📂 ሪፖርቶቹ** (their reports) and **👤 ተጠቃሚው** (the user) buttons, followed by the report itself. Albums arrive together as one album. The admin who sent a report doesn't get their own. A report delivered this way counts as read, so `/read` only shows reports you missed, for example ones that arrived while you had blocked the bot; set `LIVE_MARKS_READ=false` to have `/read` show everything anyway. Admins only receive these after they've sent `/start` to the bot once (Telegram doesn't let bots message people first).
- `/read`: shows all of **today's** unread reports from the **next user**, then marks them as read. Tap **➡️ ቀጣይ ተጠቃሚ** (next user) to keep going until everything is read. Each admin has their own read state.
- `/status`: today's totals, which users still have unread reports, and how many users are pending, with a button to start reading.
- **👥 List users** (or `/users`): tabs for **All**, **⏳ Pending**, **✅ Active** and **⛔ Suspended** (with counts), 10 users per page. Next to each user:
  - their name: opens their details with **Accept**, **Make admin / Remove admin**, **Suspend / Activate** and **📂 Reports**;
  - **📂**: their reports by period;
  - one quick button: **✅ Accept** (pending), **⛔ Suspend** (active) or **▶️ Activate** (suspended).

  The user gets a Telegram message about any change and their keyboard updates.
  - **Any admin** can accept, suspend, activate and make admins.
  - Only **super admins** (`SUPER_ADMIN_IDS`) can remove an admin or suspend one. Nobody can change a super admin or themselves.
- **🗂 All reports** (or `/reports`): everyone's reports by period, also at the bottom of the users list.

**Web admin page** (`http://<server>:8000`):
- Lists all users with their role, status and report counts. You can search them.
- **Accept**, **Make admin**, **Remove admin**, **Suspend**, **Activate**. The user gets a Telegram message about the change, and their command menu updates.
- Users listed in `SUPER_ADMIN_IDS` are always admins and can't be changed from the page.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env (local development only)
.venv/bin/python main.py
```

1. Create a bot with [@BotFather](https://t.me/BotFather) and put its token in `BOT_TOKEN`.
2. Put your own Telegram ID in `SUPER_ADMIN_IDS` (you can get it from @userinfobot).
3. Set `ADMIN_PASSWORD` and a random `SECRET_KEY`.
4. Run `main.py`. It starts the bot (long polling) and the admin page together.
5. Send `/start` to the bot. Users show up on the admin page after their first `/start` or message.

## Deploy with GitHub Actions

All production configuration lives in GitHub: **Settings → Secrets and variables → Actions**. Nothing is edited on the server.
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs on every push to `main` (or by hand: *Actions → Deploy → Run workflow*). It checks the code (compile, render the login page, build the image, validate the compose file), then over SSH it copies the code to the server, writes `.env` from the secrets and variables, backs up the database to `backups/` (keeps the 14 newest), runs `docker compose up -d --build`, and waits for "Bot started as @...".

The workflow overwrites the server's `.env` on every deploy. To change a setting, change it in GitHub and re-run the workflow.

### 1. Prepare the server (once)

```bash
# Docker + compose plugin + rsync (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sh
sudo apt-get install -y rsync
sudo usermod -aG docker <deploy user>     # log out and back in afterwards
```

### 2. Make a deploy key (on your computer)

```bash
ssh-keygen -t ed25519 -f report_bot_deploy -N '' -C github-deploy
ssh-copy-id -i report_bot_deploy.pub <deploy user>@<server ip>
ssh-keyscan -H <server ip>               # output goes in DEPLOY_KNOWN_HOSTS
```

### 3. Add secrets and variables

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | server IP or hostname |
| `DEPLOY_USER` | SSH user |
| `DEPLOY_SSH_KEY` | contents of `report_bot_deploy` (the private key) |
| `DEPLOY_KNOWN_HOSTS` | output of `ssh-keyscan -H <server ip>` (recommended) |
| `BOT_TOKEN` | token from @BotFather |
| `ADMIN_PASSWORD` | web admin page password |
| `SECRET_KEY` | random string that signs login cookies: `python3 -c "import secrets; print(secrets.token_hex(32))"` |

| Variable | Default | Value |
| --- | --- | --- |
| `SUPER_ADMIN_IDS` | (required) | your Telegram user ID(s), comma separated (get it from @userinfobot) |
| `ADMIN_USERNAME` | `admin` | web admin page username |
| `TIMEZONE` | `Africa/Addis_Ababa` | decides what "today" means |
| `LIVE_MARKS_READ` | `true` | reports delivered instantly count as read |
| `DOMAIN` | (none) | when set, Caddy serves the admin page at `https://DOMAIN` (DNS must point at the server, ports 80/443 open) |
| `ADMIN_BIND` | `127.0.0.1` | address the plain-HTTP admin page listens on (`0.0.0.0` = public, not recommended) |
| `ADMIN_PORT` | `8000` | port of the plain-HTTP admin page |
| `DEPLOY_PORT` | `22` | SSH port |
| `DEPLOY_PATH` | `report-bot` | folder in the SSH user's home |

### 4. Deploy

Push to `main`, or run the workflow from the Actions tab. Stop any other copy of the bot first (for example on your laptop), or Telegram reports `Conflict` errors: it allows one polling connection per token.

**Opening the admin page** when `DOMAIN` is not set: it listens only on the server's `127.0.0.1:8000`. Use an SSH tunnel: `ssh -L 8000:127.0.0.1:8000 user@server`, then open `http://localhost:8000`.

**Common tasks on the server** (in `~/report-bot`)

| Task | Command |
|---|---|
| Logs | `docker compose logs -f bot` |
| Restart | `docker compose restart bot` |
| Stop | `docker compose down` (data is kept in the `bot-data` volume) |
| Restore a backup | `docker compose stop bot && docker compose cp backups/<file>.db bot:/app/data/bot.db && docker compose start bot` |

## Files

| File | Purpose |
|---|---|
| `main.py` | Entry point: runs the bot and the web page in one process |
| `bot.py` | Telegram handlers (reports, `/read`, `/status`) |
| `texts_am.py`, `texts_en.py`, `texts.py` | Every text the bot shows in Telegram, in Amharic and English, and the language list |
| `web.py`, `templates/` | Web admin page |
| `db.py` | SQLite storage (`users`, `reports`, `report_reads`) |
| `config.py` | Settings from `.env` |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile` | Container deployment (optional HTTPS via Caddy) |

## Production notes
- Put the admin page behind HTTPS (for example nginx or Caddy as a reverse proxy).
- Back up the database regularly. It is a single file at `DATABASE_PATH`.
- Photos and files stay on Telegram's servers. Only their `file_id` is stored.
