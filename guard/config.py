"""Настройки из .env. Токен, ключи и ID живут только там, в коде их нет."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo


def _load_env(path: Path) -> None:
    """Простая загрузка .env без лишних библиотек. Уже заданные переменные не трогает."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


ROOT = Path(__file__).resolve().parent.parent
_load_env(Path(os.getenv("GUARD_ENV") or ROOT / ".env"))


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    return {int(x) for x in os.getenv(name, "").replace(" ", "").split(",") if x.lstrip("-").isdigit()}


def _flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "", "no", "off", "false")


# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# владельцы бота: только по Telegram ID, никогда по имени или юзернейму
OWNER_IDS = _ids("OWNER_IDS")

# Внутренний API между ботами (только 127.0.0.1)
GUARD_API_PORT = _int("GUARD_API_PORT", 8711)
BAXI_API_URL = os.getenv("BAXI_API_URL", "").strip().rstrip("/")
BAXI_API_KEY = os.getenv("BAXI_API_KEY", "").strip()
WEB_API_KEY = os.getenv("WEB_API_KEY", "").strip()
NICK_PRICE = int(os.getenv("NICK_PRICE", "5000") or 5000)   # свой ник, монет Бакси (и за смену столько же)   # для веб-админки (будет позже), свой ключ
# 1: работать только в чатах, где Бакси оплачен или на пробном (или есть своя подписка от владельца)
GUARD_NEEDS_BAXI_PAID = _flag("GUARD_NEEDS_BAXI_PAID", "0")
# ID бота Бакси (не секрет, но держим в .env): если он в чате, на голую /settings отвечает он, а не модератор
BAXI_BOT_ID = _int("BAXI_BOT_ID", 0)
API_TIMEOUT = 3.0
# куда приходят обращения в поддержку: ID группы разработчика (бот в ней админ). 0: в личку владельцам бота
SUPPORT_CHAT_ID = _int("SUPPORT_CHAT_ID", 0)

# Нейросеть для спорных случаев антиспама. Пусто в AI_API_KEY: нейросети нет
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://api.deepseek.com").rstrip("/")
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")
AI_DAILY_LIMIT = _int("AI_DAILY_LIMIT", 100)  # запросов в сутки на все чаты

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Almaty"))
DATA_DIR = ROOT / os.getenv("DATA_DIR", "data")
DB_PATH = DATA_DIR / "guard.db"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def check() -> None:
    if not BOT_TOKEN:
        raise SystemExit("В .env не задан BOT_TOKEN")
    if not OWNER_IDS:
        raise SystemExit("В .env не задан OWNER_IDS (твой Telegram ID)")
