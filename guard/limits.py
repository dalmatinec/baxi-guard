"""Лимиты, чтобы бот не утонул и Telegram его не душил.

Входящие: человек не может долбить кнопки и команды (Flood). Лишнее молча отбрасывается, на кнопку
приходит «Не так быстро». Владельцы бота без ограничений.

Исходящие: темп отправки под правила Telegram (pace). В одну группу не больше 20 сообщений в минуту, в одну
личку не больше одного в секунду, всего не больше 25 в секунду. Сообщение ждёт своей очереди вместо
ошибки 429 и паузы на весь бот."""

import asyncio
import time

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

from guard import config

# (сколько, за сколько секунд)
CALLBACKS = (20, 10)        # нажатий кнопок одним человеком (живой человек быстрее двух в секунду не жмёт)
PRIVATE = (15, 30)          # сообщений в личку бота
COMMANDS = (6, 20)          # команд одним участником в одном чате (команда бота и админы без лимита)

SEND_KINDS = {"SendMessage", "SendPhoto", "SendVideo", "SendAnimation", "SendSticker", "SendDocument", "SendVoice",
              "SendAudio", "SendVideoNote", "SendDice", "SendPoll", "SendMediaGroup", "CopyMessage", "ForwardMessage"}
GROUP_RATE = (20, 60.0)     # сообщений в группу за минуту (правило Telegram)
PRIVATE_RATE = (5, 5.0)     # в личку: короткая пачка, дальше раз в секунду
GLOBAL_RATE = (25, 1.0)     # всего в секунду


class Window:
    """Скользящее окно по ключу: не больше n событий за period секунд."""

    def __init__(self, n: int, period: float):
        self.n, self.period = n, period
        self.hits: dict = {}

    def allow(self, key) -> bool:
        now = time.monotonic()
        q = [t for t in self.hits.get(key, ()) if now - t < self.period]
        if len(q) >= self.n:
            self.hits[key] = q
            return False
        q.append(now)
        self.hits[key] = q
        if len(self.hits) > 20000:   # чистка, чтобы словарь не рос бесконечно
            self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < self.period}
        return True


class Flood(BaseMiddleware):
    def __init__(self):
        self.callbacks = Window(*CALLBACKS)
        self.private = Window(*PRIVATE)
        self.commands = Window(*COMMANDS)
        self.warned: dict[int, float] = {}

    async def __call__(self, handler, event, data: dict):
        user = getattr(event, "from_user", None)
        if user is None or user.id in config.OWNER_IDS:
            return await handler(event, data)
        if isinstance(event, CallbackQuery):
            if not self.callbacks.allow(user.id):
                now = time.monotonic()
                if now - self.warned.get(user.id, 0) > 5:
                    self.warned[user.id] = now
                    try:
                        await event.answer("Не так быстро, подождите пару секунд.")
                    except Exception:
                        pass
                return None
        elif isinstance(event, Message):
            if event.chat.type == "private":
                if not self.private.allow(user.id):
                    return None
            elif (event.text or "").startswith("/") and not self.commands.allow((event.chat.id, user.id)):
                from guard import perms   # модератор, разгребающий налёт, может банить подряд
                if not await perms.is_staff(event.bot, event.chat.id, user.id):
                    return None
        return await handler(event, data)


class Bucket:
    """Ведро жетонов: capacity штук, пополняется capacity за period секунд."""

    def __init__(self, capacity: int, period: float):
        self.capacity, self.rate = capacity, capacity / period
        self.tokens, self.at = float(capacity), time.monotonic()

    def wait_time(self) -> float:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        need = (1 - self.tokens) / self.rate
        self.tokens -= 1   # жетон занят заранее: следующий в очереди ждёт дольше
        return need


_chats: dict[int, Bucket] = {}
_global = Bucket(*GLOBAL_RATE)
MAX_WAIT = 90.0   # дольше не ждём: такое сообщение уже не нужно
ENABLED = True    # тесты выключают


async def pace(method) -> None:
    """Подождать своей очереди перед отправкой. Правки, удаления и ответы на кнопки не ждут."""
    if not ENABLED or type(method).__name__ not in SEND_KINDS:
        return
    chat_id = getattr(method, "chat_id", None)
    if not isinstance(chat_id, int):
        return
    bucket = _chats.get(chat_id)
    if bucket is None:
        bucket = Bucket(*(GROUP_RATE if chat_id < 0 else PRIVATE_RATE))
        _chats[chat_id] = bucket
        if len(_chats) > 20000:
            now = time.monotonic()
            for k in [k for k, b in _chats.items() if now - b.at > 120]:
                _chats.pop(k, None)
    delay = max(bucket.wait_time(), _global.wait_time())
    if delay > 0:
        await asyncio.sleep(min(delay, MAX_WAIT))
