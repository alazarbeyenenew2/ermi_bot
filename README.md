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
- **Editing a report (within 12 hours)**: a user can change a report they sent for **12 hours** after sending it, in two ways:
  - edit the message in Telegram (long-press → Edit; works for text and for photo / file captions), or
  - tap **✏️ Edit** on the report in **📂 My reports**, then send the new version (text, photo or file), which replaces the old one. Albums get **✏️ 1**, **✏️ 2**, … buttons for each item.

  The bot confirms "Report updated", the report shows "✏️ edited", and every admin gets a "✏️ Report edited" alert with the new version. After 12 hours the ✏️ button disappears and edits are refused ("can only be edited within 12 hours"); the original stays as it was. Users can only edit their own reports, and suspended users can't edit. Reports sent before this was added can be edited with ✏️ (if they're less than 12 hours old) but not by editing the old message.
- Suspended users get a message saying so, and their reports are not saved.

**Admins** (in Telegram):
- **Every new report is sent to all admins** the moment it arrives (always on), as "🆕 አዲስ ሪፖርት" (new report) with the sender's name and **📂 ሪፖርቶቹ** (their reports) and **👤 ተጠቃሚው** (the user) buttons, followed by the report itself. Albums arrive together as one album. The admin who sent a report doesn't get their own. A report delivered this way counts as read, so `/read` only shows reports you missed, for example ones that arrived while you had blocked the bot; set `LIVE_MARKS_READ=false` to have `/read` show everything anyway. Admins only receive these after they've sent `/start` to the bot once (Telegram doesn't let bots message people first).
- `/read`: shows all of **today's** unread reports from the **next user**, then marks them as read. Tap **➡️ ቀጣይ ተጠቃሚ** (next user) to keep going until everything is read. Each admin has their own read state.
- `/status`: today's totals, which users still have unread reports, and how many users are pending, with a button to start reading.
- **👥 Users** (or `/users`): tabs for **All**, **⏳ Pending**, **✅ Active** and **⛔ Suspended** (with counts), 10 users per page. The list is grouped into sections (waiting for approval, admins, managers, users, suspended), each user numbered with their reports today / total. Every row in a tab has the same buttons, so the list lines up:
  - **All / Active**: the user's name (opens their details) and **📂** (their reports);
  - **Pending**: the name, **✅** accept and **⛔** suspend;
  - **Suspended**: the name, **▶️** activate and **📂**.

  A user's details have **Accept**, **Make admin / Remove admin**, **Make manager / Remove manager**, **Suspend / Activate** and **📂 Reports**. The user gets a Telegram message about any change and their keyboard updates.
  - **Any admin** can accept, suspend, activate, make admins and make or remove managers.
  - Only **super admins** (`SUPER_ADMIN_IDS`) can remove an admin or suspend one. Nobody can change a super admin or themselves.
- **🗂 All reports** (or `/reports`): everyone's reports by period, also at the bottom of the users list.
- **🗑 Delete reports permanently**: every report an admin sees (Read, All reports, a user's reports, new-report alerts) has a **🗑 Delete** button; albums get one **Delete album** button after them. Deleting asks for confirmation, then removes the report from the database for everyone. Users don't get delete buttons.

**Managers** (📋) are made by an admin or super admin from a user's details. A manager can **read reports** (📥 Read reports, 📊 Summary, 🗂 All reports, a user's reports) and **gets the new-report and edited-report alerts**, but can't manage users: no 👥 Users screen, no accepting, suspending or changing roles, no deleting reports, no new-user alerts and no backups. Managers can still send their own reports.

**Super admins** also get:
- **🗑 Remove user** on a user's details: after confirmation, the user and all their reports are deleted permanently. If they start the bot again they're a new, pending user. Super admins can't be removed.
- **💾 Backup** (or `/backup`): a zipped backup is sent to **every super admin every night at 00:00 Ethiopian time** (Africa/Addis_Ababa). The backup menu shows when the next and last backups are, and has **📦 Back up now** (sent to you) and a button to turn the nightly backup off or on (remembered across restarts). The zip contains:
  - `bot.db`: a full copy of the database (to restore, stop the bot and put it back as the database file),
  - `users.csv` and `reports.csv`: everything in spreadsheet form (opens in Excel with Amharic intact),
  - `README.txt`.

  Photos and files stay on Telegram's servers; the backup keeps their file IDs, which the bot can still send with the same bot token. A super admin only receives backups after sending `/start` once. Telegram limits bot uploads to 50 MB; a larger backup has to be copied from the server (the deploy workflow also keeps copies in `backups/`).

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
| `db.py` | SQLite storage (`users`, `reports`, `report_reads`, `settings`) |
| `backup.py` | Builds the zipped backup (database copy + CSV exports) |
| `config.py` | Settings from `.env` |
| `Dockerfile`, `docker-compose.yml`, `Caddyfile` | Container deployment (optional HTTPS via Caddy) |

## Production notes
- Put the admin page behind HTTPS (for example nginx or Caddy as a reverse proxy).
- Back up the database regularly. It is a single file at `DATABASE_PATH`.
- Photos and files stay on Telegram's servers. Only their `file_id` is stored.
