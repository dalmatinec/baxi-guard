"""Нейросеть для спорных случаев антиспама. По умолчанию выключена: нужен AI_API_KEY в .env и галка в чате.
Спрашиваем только про спорные сообщения и не больше AI_DAILY_LIMIT раз в сутки на все чаты."""

import logging
from datetime import datetime

import httpx

from guard import config, db

log = logging.getLogger(__name__)

PROMPT = ("Ты фильтр спама в Telegram-чате. Тебе дают одно сообщение участника. Ответь одним словом: SPAM, если это "
          "реклама, мошенничество, заманивание в личку, сомнительный заработок, крипто-схемы, казино или набор "
          "людей в схемы. Иначе ответь OK. Обычные разговоры, шутки и вопросы это OK.")


def enabled() -> bool:
    return bool(config.AI_API_KEY)


def _day_key() -> str:
    return "ai_used:" + datetime.now(config.TZ).strftime("%Y-%m-%d")


async def is_spam(text: str) -> bool | None:
    """True/False: ответ нейросети. None: не спрашивали (нет ключа, кончился лимит, ошибка)."""
    if not enabled() or not text.strip():
        return None
    key = _day_key()
    used = int(await db.kv_get(key, "0") or 0)
    if used >= config.AI_DAILY_LIMIT:
        return None
    await db.kv_set(key, str(used + 1))
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{config.AI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {config.AI_API_KEY}"},
                json={"model": config.AI_MODEL, "max_tokens": 3, "temperature": 0,
                      "messages": [{"role": "system", "content": PROMPT},
                                   {"role": "user", "content": text[:1500]}]})
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip().upper()
    except Exception as exc:
        log.warning("Нейросеть не ответила: %s", exc)
        return None
    return answer.startswith("SPAM")
