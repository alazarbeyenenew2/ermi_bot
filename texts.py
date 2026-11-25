"""Bot languages. Every text the bot shows lives in texts_am.py (Amharic) or texts_en.py (English);
both files define the same names. Change wording there, not in bot.py.
"""
import texts_am
import texts_en

LANGUAGES = {"am": texts_am, "en": texts_en}
DEFAULT_LANGUAGE = "am"  # used until a user picks one

# Shown before a language is chosen, so it's in both languages.
LANGUAGE_BUTTON = "🌐 ቋንቋ / Language"
LANGUAGE_PROMPT = "🌐 <b>ቋንቋ ይምረጡ</b>\n<b>Choose your language</b>"
LANGUAGE_NAMES = {"am": "🇪🇹 አማርኛ", "en": "🇬🇧 English"}

# Keyboard labels from earlier versions; still on some people's keyboards until they get the new one.
OLD_LABELS = {
    "BTN_SEND": ["📝 Send report"],
    "BTN_MINE": ["📂 My reports"],
    "BTN_READ": ["📖 Read reports"],
    "BTN_STATUS": ["📊 Status"],
    "BTN_USERS": ["👥 List users", "👥 Manage users"],
    "BTN_ALL": ["🗂 All reports"],
}


def get(language: str | None):
    """The texts module for a language code (falls back to the default)."""
    return LANGUAGES.get(language or DEFAULT_LANGUAGE, LANGUAGES[DEFAULT_LANGUAGE])


def labels(name: str) -> list[str]:
    """Every label a keyboard button has had, in every language, so any of them is recognised."""
    found = [getattr(module, name) for module in LANGUAGES.values()] + OLD_LABELS.get(name, [])
    return list(dict.fromkeys(found))
