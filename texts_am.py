"""Amharic texts. texts_en.py must define exactly the same names (see texts.py).

Texts are HTML (Telegram parse mode). Anything inserted from users (names, captions, file names) must
be escaped with html.escape before it is passed in.
"""

LINE = "──────────────"

# ---------- reply keyboard (the buttons under the text box) ----------
BTN_SEND = "📝 ሪፖርት ላክ"
BTN_MINE = "📂 የኔ ሪፖርቶች"
BTN_READ = "📥 ሪፖርቶችን አንብብ"
BTN_STATUS = "📊 ማጠቃለያ"
BTN_USERS = "👥 ተጠቃሚዎች"
BTN_ALL = "🗂 ሁሉም ሪፖርቶች"
KEYBOARD_PLACEHOLDER = "ሪፖርትዎን እዚህ ይጻፉ ወይም ፋይል ይላኩ…"

# ---------- command menu ----------
CMD_START = "መጀመሪያ / እገዛ"
CMD_MINE = "የኔ ሪፖርቶች"
CMD_READ = "የዛሬን ሪፖርቶች አንብብ"
CMD_STATUS = "የዛሬ ማጠቃለያ"
CMD_USERS = "ተጠቃሚዎችን አስተዳድር"
CMD_REPORTS = "የሁሉም ተጠቃሚዎች ሪፖርቶች"

# ---------- welcome ----------
def welcome(name: str) -> str:
    return f"👋 ሰላም {name}!\nወደ <b>ሪፖርት ቦት</b> እንኳን በደህና መጡ።\n{LINE}\n"


WELCOME_USER = (
    "📝 ሪፖርት ለመላክ <b>መልእክት</b>፣ <b>ፎቶ</b>፣ <b>ቪዲዮ</b>፣ <b>የድምፅ መልእክት</b> "
    "ወይም ማንኛውንም <b>ፋይል</b> ብቻ ይላኩልኝ።\n\n"
    f"<b>{BTN_SEND}</b>\n   ሪፖርት እንዴት እንደሚላክ ያሳያል\n"
    f"<b>{BTN_MINE}</b>\n   የላኳቸውን ሪፖርቶች ያሳያል"
)
WELCOME_ADMIN = (
    f"\n\n🛡 <b>እርስዎ አስተዳዳሪ ነዎት</b>\n{LINE}\n"
    f"<b>{BTN_READ}</b>\n   የዛሬን ሪፖርቶች በየተጠቃሚው አንድ በአንድ\n"
    f"<b>{BTN_STATUS}</b>\n   የዛሬ ሁኔታ በአጭሩ\n"
    f"<b>{BTN_USERS}</b>\n   መቀበል፣ ማገድ፣ ማንቃት፣ አስተዳዳሪ ማድረግ\n"
    f"<b>{BTN_ALL}</b>\n   የሁሉም ተጠቃሚዎች ሪፖርቶች"
)
PENDING = ("⏳ መለያዎ አስተዳዳሪ እስኪቀበለው <b>በመጠባበቅ ላይ</b> ነው።\n"
           "ሪፖርት መላክ ሲችሉ እዚሁ መልእክት ይደርስዎታል።")
BLOCKED = "⛔ መለያዎ <b>ታግዷል</b>።\nእባክዎ አስተዳዳሪን ያነጋግሩ።"

# ---------- sending reports ----------
SEND_PROMPT = (
    f"📝 <b>ሪፖርት ይላኩ</b>\n{LINE}\n"
    "አሁን ሪፖርትዎን ይላኩ፦\n"
    "✍️  የጽሑፍ መልእክት\n"
    "🖼  ፎቶ ወይም ቪዲዮ (አልበምም ይቻላል)\n"
    "📎  ማንኛውም ፋይል (PDF፣ Word፣ Excel …)\n"
    "🎤  የድምፅ መልእክት ወይም ኦዲዮ\n\n"
    "<i>መግለጫ (caption) ማከል ይችላሉ። የሚልኩት እያንዳንዱ መልእክት እንደ ሪፖርት ይቀመጣል።</i>"
)
REPORT_RECEIVED = "✅ ሪፖርትዎ ደርሷል። እናመሰግናለን!"
NOT_SAVED = "\n\n<i>ሪፖርትዎ አልተቀመጠም።</i>"
UNSUPPORTED = ("እባክዎ ሪፖርትዎን እንደ <b>መልእክት</b>፣ <b>ፎቶ</b>፣ <b>ቪዲዮ</b>፣ <b>የድምፅ መልእክት</b> "
               "ወይም <b>ፋይል</b> ይላኩ።\n<i>ስቲከር፣ አካባቢ እና የስልክ አድራሻ አይቀመጡም።</i>")

CMD_LANGUAGE = "ቋንቋ ቀይር"
LANGUAGE_SET = "✅ ቋንቋ ወደ <b>አማርኛ</b> ተቀይሯል።"

# ---------- general ----------
ONLY_ADMINS = "🔒 ይህ ለአስተዳዳሪዎች ብቻ ነው።"
USER_NOT_FOUND = "ተጠቃሚው አልተገኘም።"
NOT_ALLOWED = "አልተፈቀደም ወይም ቀደም ብሎ ተቀይሯል።"
BACK = "◀️ ተመለስ"
PREV = "◀️ ቀዳሚ"
NEXT = "ቀጣይ ▶️"
TODAY = "ዛሬ"


def could_not_show(error: str) -> str:
    return f"⚠️ ሪፖርቱን ማሳየት አልተቻለም፦ {error}"


def done(name: str, action_label: str) -> str:
    return f"{name}፦ {action_label} ✅"


# ---------- report captions ----------
def caption_header(name: str, when: str) -> str:
    return f"👤 <b>{name}</b>\n🕒 {when}"


def caption_file(file_name: str) -> str:
    return f"📎 <code>{file_name}</code>"


# ---------- user actions: button label, message sent to the user ----------
ACTIONS = {
    "approve": ("✅ ተቀበል", "✅ መለያዎ <b>ተቀባይነት አግኝቷል</b>።\nአሁን ሪፖርት መላክ ይችላሉ።"),
    "make_admin": ("🛡 አስተዳዳሪ አድርግ", "🛡 አሁን <b>አስተዳዳሪ</b> ነዎት።\nሪፖርቶችን ለማንበብ ከታች ያሉትን ቁልፎች ይጠቀሙ።"),
    "remove_admin": ("➖ ከአስተዳዳሪነት አንሳ", "ከአሁን በኋላ አስተዳዳሪ አይደሉም።"),
    "block": ("⛔ አግድ", "⛔ መለያዎ <b>ታግዷል</b>።\nየሚልኳቸው ሪፖርቶች አይቀመጡም።"),
    "unblock": ("▶️ አንቃ", "▶️ መለያዎ እንደገና <b>ንቁ</b> ሆኗል።\nሪፖርት መላክ ይችላሉ።"),
}


# ---------- alerts to admins ----------
def new_user_alert(name: str, telegram_id: int) -> str:
    return (f"🙋 <b>አዲስ ተጠቃሚ</b>\n{LINE}\n👤 {name}\n🆔 <code>{telegram_id}</code>\n\n"
            "ሪፖርት ለመላክ ፈቃድ ይጠይቃል።")


def new_report_alert(name: str, items: int) -> str:
    extra = f" · {items} ፋይሎች" if items > 1 else ""
    return f"🆕 <b>አዲስ ሪፖርት</b>{extra}\n👤 {name}"


ALERT_THEIR_REPORTS = "📂 ሪፖርቶቹ"
ALERT_USER = "👤 ተጠቃሚው"

# ---------- reports by period ----------
PERIODS = {"t": "📅 ዛሬ", "w": "🗓 በዚህ ሳምንት", "m": "📆 በዚህ ወር", "a": "♾ ሁሉም"}
TITLE_MINE = "የእርስዎ ሪፖርቶች"
TITLE_ALL = "የሁሉም ተጠቃሚዎች ሪፖርቶች"


def title_user(name: str) -> str:
    return f"{name} · ሪፖርቶች"


CHOOSE_PERIOD = "የትኞቹን ሪፖርቶች ማየት ይፈልጋሉ?"
NO_REPORTS = "ምንም ሪፖርት የለም።"
OTHER_PERIOD = "📅 ሌላ ጊዜ ይምረጡ"


def report_count(total: int) -> str:
    return f"📨 {total} ሪፖርቶች"


def showing_newest(n: int) -> str:
    return f"የቅርብ ጊዜዎቹ {n} ይታያሉ"


def showing_older(first: int, last: int) -> str:
    return f"የቆዩት {first}–{last} ይታያሉ"


def older_button(remaining: int) -> str:
    return f"⬇️ የቆዩ ሪፖርቶች ({remaining})"


# ---------- read ----------
def read_header(name: str, count: int) -> str:
    return f"📥 <b>{name}</b>\n📨 የዛሬ {count} ሪፖርቶች\n{LINE}"


READ_ALL_DONE = "✅ የዛሬን ሪፖርቶች በሙሉ አንብበዋል።"
READ_LAST_DONE = "✅ ይህ የመጨረሻው ነበር። የዛሬ ሪፖርቶች በሙሉ ተነበዋል።"


def read_more(users_left: int) -> str:
    return f"ያልተነበበ ሪፖርት ያላቸው <b>{users_left}</b> ተጠቃሚዎች ቀርተዋል።"


def next_user_button(users_left: int) -> str:
    return f"➡️ ቀጣይ ተጠቃሚ ({users_left})"


# ---------- status ----------
def status(total: int, reporters: int, unread: int, unread_users: int, waiting: int) -> str:
    text = (
        f"📊 <b>የዛሬ ማጠቃለያ</b>\n{LINE}\n"
        f"📨 ሪፖርቶች፦ <b>{total}</b> (ከ{reporters} ተጠቃሚዎች)\n"
        f"📭 ያላነበቧቸው፦ <b>{unread}</b> (ከ{unread_users} ተጠቃሚዎች)"
    )
    if waiting:
        text += f"\n⏳ ፈቃድ የሚጠብቁ፦ <b>{waiting}</b>  →  {BTN_USERS}"
    return text


STATUS_UNREAD_TITLE = "\n\n<b>ያልተነበቡ ሪፖርቶች</b>"


def status_more(n: int) -> str:
    return f"…እና ሌሎች {n}"


# ---------- users list ----------
FILTERS = {"a": "👥 ሁሉም", "p": "⏳ በመጠባበቅ", "c": "✅ ንቁ", "s": "⛔ የታገዱ"}


def users_title(filter_label: str, count: int, page: int, pages: int) -> str:
    text = f"<b>ተጠቃሚዎች · {filter_label}</b> ({count})"
    if pages > 1:
        text += f"\nገጽ {page}/{pages}"
    return text + f"\n{LINE}"


def users_row(n: int, badge: str, name: str, today: int, total: int) -> str:
    return f"{n}. {badge} {name}\n      ዛሬ {today} · ጠቅላላ {total}"


USERS_EMPTY = "እዚህ ማንም የለም።"
USERS_LEGEND = (f"{LINE}\n⭐ ዋና አስተዳዳሪ · 🛡 አስተዳዳሪ · 👤 ተጠቃሚ\n⏳ በመጠባበቅ ላይ · ⛔ የታገደ\n"
                "<i>ዝርዝር ለማየት ስሙን፣ ሪፖርቶቹን ለማየት 📂 ይጫኑ።</i>")
ALL_USERS_REPORTS = "🗂 የሁሉም ሪፖርቶች"

# ---------- user card ----------
ROLE_SUPER = "⭐ ዋና አስተዳዳሪ"
ROLE_ADMIN = "🛡 አስተዳዳሪ"
ROLE_USER = "👤 ተጠቃሚ"
STATE_SUSPENDED = "⛔ ታግዷል"
STATE_PENDING = "⏳ በመጠባበቅ ላይ"
STATE_ACTIVE = "✅ ንቁ"


def user_card(name: str, telegram_id: int, role: str, state: str, today: int, total: int,
              joined: str, last_seen: str) -> str:
    return (
        f"<b>{name}</b>\n{LINE}\n"
        f"🆔 መለያ ቁጥር፦ <code>{telegram_id}</code>\n"
        f"🎖 ሚና፦ {role}\n"
        f"📍 ሁኔታ፦ {state}\n"
        f"📨 ሪፖርቶች፦ ዛሬ {today} · ጠቅላላ {total}\n"
        f"📅 የተመዘገበው፦ {joined}\n"
        f"👁 ለመጨረሻ ጊዜ የታየው፦ {last_seen}"
    )


CARD_SUPER = "\n\n<i>ዋና አስተዳዳሪዎች በ SUPER_ADMIN_IDS ይወሰናሉ፤ እዚህ ሊቀየሩ አይችሉም።</i>"
CARD_SELF = "\n\n<i>ይህ እርስዎ ነዎት።</i>"
CARD_ADMIN_ONLY_SUPER = "\n\n<i>ሌላ አስተዳዳሪን ማንሳት ወይም ማገድ የሚችሉት ዋና አስተዳዳሪዎች ብቻ ናቸው።</i>"
CARD_REPORTS = "📂 ሪፖርቶች"
CARD_BACK = "◀️ ወደ ዝርዝሩ"
