"""Тексты с премиум-эмодзи в двух видах: для Telegram и для веб-админки.

Храним как HTML Telegram (то, что даёт message.html_text): <tg-emoji emoji-id="5368324170671202286">🔥</tg-emoji>.
В веб-админке тот же текст, но премиум-эмодзи видны как {emoji:5368324170671202286:🔥}: ID можно увидеть,
скопировать, вставить в другой текст. Остальная разметка (<b>, <i>, <a href>) одинаковая в обоих видах.

Как работают вместе: в вебе поменяли текст -> в боте открыли его, бот прислал сохранённый текст сообщением ->
скопировали, добавили премиум-эмодзи в Telegram, прислали боту -> сохранилось, и веб показывает их ID.

Кнопки: строка на ряд, в ряду через |, каждая «Текст - ссылка». Только владелец бота может добавить
цвет в конце (#синяя #зелёная #красная, или #blue #green #red) и премиум-значок в начале ({emoji:ID:🔥} Текст)."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser

_TG_EMOJI = re.compile(r'<tg-emoji\s+emoji-id="(\d+)"\s*>(.*?)</tg-emoji>', re.S)
_WEB_EMOJI = re.compile(r"\{emoji:(\d{5,25})(?::([^{}]{1,16}))?\}")
DEFAULT_ALT = "⭐"

# теги и атрибуты, которые понимает Telegram (parse_mode=HTML)
ALLOWED = {"b": (), "strong": (), "i": (), "em": (), "u": (), "ins": (), "s": (), "strike": (), "del": (),
           "a": ("href",), "code": ("class",), "pre": (), "tg-spoiler": (), "span": ("class",),
           "blockquote": ("expandable",), "tg-emoji": ("emoji-id",)}
VOID_OK = {"br"}

STYLES = {"синяя": "primary", "blue": "primary", "зелёная": "success", "зеленая": "success", "green": "success",
          "красная": "danger", "red": "danger"}
STYLE_TAG = {"primary": "#синяя", "success": "#зелёная", "danger": "#красная"}
STYLE_NAMES = {"primary": "🔵 синяя", "success": "🟢 зелёная", "danger": "🔴 красная", "": "без цвета"}


# ---------- премиум-эмодзи ----------

def to_web(html: str) -> str:
    """<tg-emoji emoji-id="1">🔥</tg-emoji> -> {emoji:1:🔥}"""
    return _TG_EMOJI.sub(lambda m: f"{{emoji:{m.group(1)}:{m.group(2) or DEFAULT_ALT}}}", html or "")


def from_web(text: str) -> str:
    """{emoji:1:🔥} -> <tg-emoji emoji-id="1">🔥</tg-emoji>. Без эмодзи-заглушки ставит ⭐."""
    return _WEB_EMOJI.sub(lambda m: f'<tg-emoji emoji-id="{m.group(1)}">{m.group(2) or DEFAULT_ALT}</tg-emoji>',
                          text or "")


def plain_web(text: str) -> str:
    """Убрать ID: {emoji:1:🔥} -> 🔥 (для тех, кому премиум-значки на кнопках не положены)."""
    return _WEB_EMOJI.sub(lambda m: m.group(2) or "", text or "")


def emoji_of(html: str) -> list[dict]:
    """Какие премиум-эмодзи в тексте: [{"id": "...", "alt": "🔥"}] без повторов."""
    seen, found = set(), []
    for m in _TG_EMOJI.finditer(html or ""):
        if m.group(1) not in seen:
            seen.add(m.group(1))
            found.append({"id": m.group(1), "alt": m.group(2)})
    return found


class _Checker(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.error = ""

    def handle_starttag(self, tag, attrs):
        if tag in VOID_OK:
            return
        if tag not in ALLOWED:
            self.error = self.error or f"Telegram не понимает тег <{tag}>"
            return
        for name, _ in attrs:
            if name not in ALLOWED[tag]:
                self.error = self.error or f"У <{tag}> не бывает {name}"
        if tag == "tg-emoji" and not dict(attrs).get("emoji-id", "").isdigit():
            self.error = self.error or "У премиум-эмодзи нет ID"
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID_OK:
            return
        if not self.stack or self.stack[-1] != tag:
            self.error = self.error or f"Лишний или не на месте </{tag}>"
            return
        self.stack.pop()


_LONE_AMP = re.compile(r"&(?!(?:[a-zA-Z]{2,8}|#\d{1,7}|#x[0-9a-fA-F]{1,6});)")


def normalize_html(html: str) -> str:
    """Одиночный & Telegram не принимает: превращаем в &amp;. Переносы строк Windows в обычные."""
    return _LONE_AMP.sub("&amp;", (html or "").replace("\r\n", "\n"))


def check_html(html: str) -> str:
    """Пустая строка, если Telegram такой HTML примет, иначе что не так."""
    p = _Checker()
    try:
        p.feed(html or "")
        p.close()
    except Exception as exc:   # HTMLParser почти не падает, но на всякий случай
        return f"Не разобрал HTML: {exc}"
    if p.error:
        return p.error
    if p.stack:
        return f"Не закрыт <{p.stack[-1]}>"
    return ""


# ---------- кнопки ----------

@dataclass
class Button:
    text: str
    url: str
    style: str = ""   # primary, success, danger или пусто
    icon: str = ""    # ID премиум-эмодзи значка

    def line(self) -> str:
        head = f"{{emoji:{self.icon}:{DEFAULT_ALT}}} " if self.icon else ""
        tail = f" {STYLE_TAG[self.style]}" if self.style in STYLE_TAG else ""
        return f"{head}{self.text} - {self.url}{tail}"


_BUTTON = re.compile(r"^\s*(.+?)\s+[-–—]\s+((?:https?://|tg://)\S+)\s*(?:#(\S+))?\s*$")
_ICON = re.compile(r"^\s*\{emoji:(\d{5,25})(?::[^{}]{0,16})?\}\s*")


def parse_buttons(raw: str, *, design: bool = False) -> tuple[list[list[Button]], list[str]]:
    """Разобрать кнопки. design=False: цвет и премиум-значок отбрасываются (их ставит только владелец бота).
    Возвращает (ряды, непонятые куски). До 8 рядов по 4 кнопки."""
    rows, bad = [], []
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        row = []
        for part in line.split("|"):
            m = _BUTTON.match(part)
            if not m:
                if part.strip():
                    bad.append(part.strip())
                continue
            text, url, tag = m.group(1), m.group(2), (m.group(3) or "").lower()
            icon = ""
            im = _ICON.match(text)
            if im:
                icon, text = im.group(1), text[im.end():] or DEFAULT_ALT
            text = plain_web(text).strip() or DEFAULT_ALT
            style = STYLES.get(tag, "")
            if not design:
                icon, style = "", ""
            row.append(Button(text[:64], url[:512], style, icon))
        if row:
            rows.append(row[:4])
    return rows[:8], bad


def buttons_text(rows: list[list[Button]]) -> str:
    return "\n".join(" | ".join(b.line() for b in row) for row in rows)
