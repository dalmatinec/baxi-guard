"""Ссылки, домены и признаки спама. Чистая логика без Telegram."""

import re
from dataclasses import dataclass

from guard.normalize import base

_DOMAIN = re.compile(r"^(?=.{3,253}$)([a-z0-9а-яё](?:[a-z0-9а-яё-]{0,61}[a-z0-9а-яё])?\.)+[a-zа-яё]{2,24}$")
TELEGRAM_DOMAINS = {"t.me", "telegram.me", "telegram.dog"}
# доменные зоны, по которым узнаём ссылку без http:// (Telegram сам размечает ссылки, это запасной путь)
TLDS = ("com|net|org|ru|su|рф|рус|io|me|info|biz|xyz|top|site|online|club|shop|store|app|dev|pro|tv|cc|co|uk|ua|"
        "kz|by|uz|kg|am|ge|az|tj|de|us|eu|live|link|click|icu|buzz|fun|space|website|tech|cloud|ly|gg|to|bot|"
        "money|cash|bet|casino|win|vip|sale|work|cash|finance|group|world|today|news|digital|best|life|one")
_URL = re.compile(
    r"(?i)(?:https?://|tg://|www\.)[^\s<>\"']+"
    r"|(?<![@\w.-])(?:[a-z0-9а-яё](?:[a-z0-9а-яё-]{0,61}[a-z0-9а-яё])?\.)+(?:" + TLDS + r")\b(?:/[^\s<>\"']*)?")


def normalize_domain(raw: str) -> str:
    """'https://www.Example.com/path' -> 'example.com'. Не домен: пустая строка."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    d = d.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    if d.startswith("www."):
        d = d[4:]
    return d if _DOMAIN.match(d) else ""


def domain_allowed(domain: str, allowed: list[str] | set[str]) -> bool:
    """Домен из белого списка или его поддомен."""
    return any(domain == d or domain.endswith("." + d) for d in allowed)


@dataclass
class Link:
    url: str
    domain: str
    kind: str          # invite | telegram | site
    tg_name: str = ""  # для t.me/<имя>: имя канала, бота или чата


def classify(url: str) -> Link:
    raw = url.strip()
    low = raw.lower()
    if low.startswith("tg://"):
        m = re.search(r"domain=([a-z0-9_]+)", low)
        if "join" in low:
            return Link(raw, "t.me", "invite")
        return Link(raw, "t.me", "telegram", m.group(1) if m else "")
    domain = normalize_domain(raw)
    if domain in TELEGRAM_DOMAINS or domain.endswith(".t.me"):
        if domain.endswith(".t.me"):  # имя.t.me
            return Link(raw, "t.me", "telegram", domain[:-5])
        path = re.sub(r"^[a-z]+://", "", low).split("/", 1)
        path = path[1] if len(path) > 1 else ""
        path = path.split("?", 1)[0].strip("/")
        if path.startswith(("+", "joinchat", "addlist")) or path.startswith("%2b"):
            return Link(raw, "t.me", "invite")
        parts = [p for p in path.split("/") if p]
        if parts and parts[0] == "s" and len(parts) > 1:
            parts = parts[1:]
        return Link(raw, "t.me", "telegram", parts[0] if parts else "")
    return Link(raw, domain, "site")


def extract_links(text: str, entity_urls: list[str] | None = None) -> list[Link]:
    """Все ссылки: из разметки Telegram (url, text_link) и найденные в тексте сами."""
    seen, links = set(), []
    for url in list(entity_urls or []) + _URL.findall(text or ""):
        url = url.rstrip(".,;:!?)»\"'")
        key = url.lower()
        if not url or key in seen:
            continue
        seen.add(key)
        link = classify(url)
        if link.domain or link.kind != "site":
            links.append(link)
    return links


def link_violation(links: list[Link], *, allowed_domains: list[str] | set[str], own_username: str = "",
                   newbie: bool = False) -> str:
    """Причина, если ссылки нельзя. Пусто: можно."""
    own = (own_username or "").lower()
    for link in links:
        if newbie:
            return "ссылка от новичка"
        if link.kind == "invite":
            if not domain_allowed("t.me", allowed_domains):
                return "приглашение в чужой чат"
        elif link.kind == "telegram":
            if own and link.tg_name == own:
                continue
            if not domain_allowed("t.me", allowed_domains):
                return "ссылка на чужой канал или бота"
        elif not domain_allowed(link.domain, allowed_domains):
            return "ссылка на сайт"
    return ""


# ---------- скам ----------

_SCAM = [
    (re.compile(r"(заработ|зараба|доход|прибыл|профит|выплат)\w*.{0,60}?\b(в|за)\s*(день|сутки|неделю|час)\b"),
     "заработок в день"),
    (re.compile(r"\b(\d[\d\s]{1,9})\s*(\$|руб|р\b|тг|₸|тенге|usdt|долл|евро|€)\w*.{0,25}?\b(в|за)\s*(день|сутки)\b"),
     "заработок в день"),
    (re.compile(r"\b(пиши|пишите|напиши|напишите|стучи|стучите|жду|обращайтесь|обращаться)\w*\s+"
                r"(мне\s+|в\s+)?(в\s+)?(лс|личку|личные|личных|директ|пм|лич)\b"),
     "пиши в лс"),
    (re.compile(r"\b(в|во)\s+(лс|личку|личные)\s+(пиши|пишите|за\s+подробност|подробност)"), "пиши в лс"),
    (re.compile(r"бесплатно\s+разда|разда\w*\s+(бесплатно|даром)|раздач\w*\s+(денег|крипт|usdt|тон|ton|звезд|подарк)"),
     "бесплатно раздаю"),
]
_WALLETS = [
    re.compile(r"\b0x[a-fA-F0-9]{40}\b"),                          # ETH, BSC
    re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b"),                  # TRON
    re.compile(r"\b(?:EQ|UQ)[A-Za-z0-9_-]{46}\b"),                 # TON
    re.compile(r"\bbc1[ac-hj-np-z02-9]{25,59}\b"),                 # BTC bech32
    re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b"),            # BTC старые адреса
]
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")
_WEAK = re.compile(r"\b(подработ|заработ|доход|инвест|крипт|пассивн|удаленк|удалённ|удаленн|ставк|казино|"
                   r"схем|арбитраж|трейд|сигнал|вакан|набира|ищу\s+люд|требуют|лс\b|личк)")


def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def has_card(text: str) -> bool:
    for m in _CARD.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(0))
        if 16 <= len(digits) <= 19 and digits[0] in "23456" and luhn(digits):
            return True
    return False


def has_wallet(text: str) -> bool:
    return any(p.search(text or "") for p in _WALLETS)


def scam_reason(text: str) -> str:
    """Явные скам-признаки. Пусто: не нашли."""
    if not text:
        return ""
    plain = re.sub(r"\s+", " ", text.lower().replace("ё", "е"))
    twins = re.sub(r"\s+", " ", base(text))  # латинские двойники в кириллицу: «зaрaбoток»
    for pattern, why in _SCAM:
        if pattern.search(plain) or pattern.search(twins):
            return why
    if has_wallet(text):
        return "крипто-кошелёк"
    if has_card(text):
        return "номер карты"
    return ""


def looks_suspicious(text: str) -> bool:
    """Мягкие признаки: сами по себе не повод удалять, но можно спросить нейросеть."""
    text = text or ""
    return bool(_WEAK.search(text.lower().replace("ё", "е")) or _WEAK.search(base(text)))
