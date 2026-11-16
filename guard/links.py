"""Ссылки, домены и признаки спама. Чистая логика без Telegram."""

import re

_DOMAIN = re.compile(r"^(?=.{3,253}$)([a-z0-9а-яё](?:[a-z0-9а-яё-]{0,61}[a-z0-9а-яё])?\.)+[a-zа-яё]{2,24}$")


def normalize_domain(raw: str) -> str:
    """'https://www.Example.com/path' -> 'example.com'. Не домен: пустая строка."""
    d = (raw or "").strip().lower()
    d = re.sub(r"^[a-z]+://", "", d)
    d = d.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    d = d.rsplit("@", 1)[-1].split(":", 1)[0].strip(".")
    if d.startswith("www."):
        d = d[4:]
    return d if _DOMAIN.match(d) else ""
