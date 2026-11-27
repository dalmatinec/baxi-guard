"""Разбор команд без Telegram: время (30m, 2h, 3d, 1w), цель (@username или ID) и причина."""

import re
from dataclasses import dataclass

UNITS = {"m": 60, "h": 3600, "d": 86400, "w": 7 * 86400,
         "м": 60, "ч": 3600, "д": 86400, "н": 7 * 86400}
_DURATION = re.compile(r"^(\d{1,5})([mhdwмчдн])$", re.IGNORECASE)
# дольше 366 дней Telegram всё равно считает «навсегда»
MAX_SECONDS = 366 * 86400
FOREVER = 0


def parse_duration(token: str) -> int | None:
    """'30m' -> 1800. Не время или ноль: None. Больше года: 0 (навсегда)."""
    m = _DURATION.match((token or "").strip())
    if not m:
        return None
    seconds = int(m.group(1)) * UNITS[m.group(2).lower()]
    if seconds <= 0:
        return None
    return FOREVER if seconds > MAX_SECONDS else seconds


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def human_duration(seconds: int) -> str:
    """1800 -> '30 минут', 0 -> 'навсегда'. Для фраз «мут на ...»."""
    if not seconds:
        return "навсегда"
    for size, forms in ((7 * 86400, ("неделю", "недели", "недель")), (86400, ("день", "дня", "дней")),
                        (3600, ("час", "часа", "часов")), (60, ("минуту", "минуты", "минут"))):
        if seconds % size == 0:
            n = seconds // size
            return f"{n} {_plural(n, *forms)}"
    n = max(1, seconds // 60)
    return f"{n} {_plural(n, 'минуту', 'минуты', 'минут')}"


def human_for(seconds: int) -> str:
    """'на 30 минут' или 'навсегда'."""
    return "навсегда" if not seconds else f"на {human_duration(seconds)}"


@dataclass
class Args:
    username: str = ""        # без @, в нижнем регистре
    user_id: int = 0
    seconds: int | None = None  # None: время не указали
    reason: str = ""

    @property
    def has_target(self) -> bool:
        return bool(self.username or self.user_id)


def strip_command(text: str) -> str:
    """'/mute@bot 2h спам' -> '2h спам'."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return text
    parts = text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


_USERNAME = re.compile(r"^@([A-Za-z][A-Za-z0-9_]{3,31})$")


def parse_args(text: str, *, has_reply: bool, with_time: bool) -> Args:
    """Аргументы после команды. Цель указывают ответом (тогда её в тексте нет), либо первым словом:
    @username или числовой ID. Потом время (если команда его понимает), остальное причина."""
    rest = strip_command(text)
    args = Args()
    tokens = rest.split()
    i = 0
    if tokens and not has_reply:
        first = tokens[0]
        m = _USERNAME.match(first)
        if m:
            args.username = m.group(1).lower()
            i = 1
        elif first.isdigit() and len(first) >= 5:
            args.user_id = int(first)
            i = 1
    if with_time and i < len(tokens):
        seconds = parse_duration(tokens[i])
        if seconds is not None:
            args.seconds = seconds
            i += 1
    args.reason = " ".join(tokens[i:])[:300]
    return args
