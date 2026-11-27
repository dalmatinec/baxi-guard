"""Игры со списком в чате (рулетка и следующие): /game, запись, закрыть список, рандом, топы.

Всё запускает тот, у кого право 🎮 игры (создатель чата всегда). Порядок: /game → игра → режим → ФК (0, 10, 20, 50 или своё) → список.
  по хэштегу:  бот просит хэштег, админ присылает его одним словом (#рулетка), бот закрепляет живой список,
               в него попадает каждый, кто пришлёт этот хэштег;
  обычная:     в списке кнопка «Записаться», ещё можно прислать «@» или свой @юзернейм (чужой не записывает).
Дальше: «закрыть список», «открыть список», «рандом 36» (только число), «закончить игру»,
ответом: «+топ» (постоянный), «+топ игра» (на эту игру), «+топ 3д» (на срок), «-топ», «+в список»;
«убрать 5» убирает пятый номер."""

import logging
import re

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from guard import db, perms, texts
from guard.gamecore import GAMES, core, tops
from guard.handlers.moderation import real_reply
from guard.out import esc, full_name, mention, say, thread_of

log = logging.getLogger(__name__)
router = Router(name="listgames")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

FK_CHOICES = (0, 10, 20, 50)
_CLOSE = re.compile(r"^\s*(закрыть список|/close)(@\w+)?\s*$", re.I)
_OPEN = re.compile(r"^\s*(открыть список|/open)(@\w+)?\s*$", re.I)
_END = re.compile(r"^\s*(закончить игру|сбросить игру|/endgame)(@\w+)?\s*$", re.I)
_FK = re.compile(r"^\s*(?:/fk(?:@\w+)?(?:\s+(\d{1,5}))?|фк\s+(\d{1,5}))\s*$", re.I)   # «фк» только с числом
_LIST = re.compile(r"^\s*/list(@\w+)?\s*$", re.I)
_LEAVE = re.compile(r"^\s*(/leave(@\w+)?|выписаться)\s*$", re.I)
# у каждой русской команды есть латинская пара: +топ / +top (или /top, /untop), +в список / /add, убрать 3 / /remove 3
_TOP = re.compile(r"^\s*(?:([+-])\s*(?:топ|top)|/(un)?top(?:@\w+)?)(?:\s+(игра|game|навсегда|forever|\d{1,3}\s*[мчдmhd]))?\s*$",
                  re.I)
_ADD = re.compile(r"^\s*(?:\+\s*в\s+список|/add(?:@\w+)?)\s*$", re.I)
_REMOVE = re.compile(r"^\s*(?:убрать|/remove(?:@\w+)?)\s+(\d{1,4})\s*$", re.I)
_UNITS = {"м": 60, "m": 60, "ч": 3600, "h": 3600, "д": 86400, "d": 86400}


class GameFk(StatesGroup):
    value = State()   # своё ФК: ждём число от того, кто запускает


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row]
                                                 for row in rows])


def name_of(user) -> str:
    return full_name(user)


def allowed_chat(allowed: bool) -> str:
    """Почему нельзя играть в этом чате. Пусто: можно."""
    return "" if allowed else "игры в этом чате не активны (нужна подписка)"


async def admin_ok(bot: Bot, message: Message) -> bool:
    """Вести игры может тот, у кого есть право 🎮 игры (создатель всегда)."""
    return await perms.message_has(message, perms.R_GAMES)


# ---------- запуск: /game ----------

def games_menu() -> tuple[str, InlineKeyboardMarkup]:
    # игра и способ записи сразу одной кнопкой: «🎰 Рулетка · #️⃣ по хэштегу»
    def label(g, title: str) -> str:
        icon, _, name = title.partition(" ")
        return f"{g.icon} {g.title} · {icon} {name.lower()}"
    rows = [[(label(g, title), f"lg:mode:{g.kind}:{code}")] for g in GAMES.values() for code, title in g.modes]
    return "🎮 <b>Игры</b>\n➖➖➖➖➖\nВ какую играем?", kb(rows + [[("✖️ Отмена", "lg:cancel")]])


@router.message(Command("game"))
async def cmd_game(message: Message, bot: Bot, state: FSMContext, allowed: bool = True) -> None:
    if not await admin_ok(bot, message):
        return
    why = allowed_chat(allowed)
    if why:
        await say(bot, message.chat.id, f"🎮 {why}", reply_to=message)
        return
    await state.clear()
    game = await core.active(message.chat.id)
    if game:
        n = await core.count(game["id"])
        await say(bot, message.chat.id,
                  f"🎮 Уже идёт игра: <b>{GAMES[game['kind']].title}</b>, игроков {n}.\nНачать новую? Старая закончится.",
                  reply_to=message, markup=kb([[("🔄 Новая игра", "lg:new"), ("✖️ Отмена", "lg:cancel")]]))
        return
    text, markup = games_menu()
    await say(bot, message.chat.id, text, reply_to=message, markup=markup)


@router.callback_query(F.data.startswith("lg:"))
async def on_button(cb: CallbackQuery, bot: Bot, state: FSMContext) -> None:
    parts = cb.data.split(":")
    action = parts[1]
    chat_id = cb.message.chat.id
    user = cb.from_user

    if action in ("join", "leave", "full"):   # кнопки живого списка: для всех
        game = await core.get(int(parts[2]))
        if not game or game["status"] not in core.LIVE or game["chat_id"] != chat_id:
            return await cb.answer("Игра уже закончилась")
        if action == "full":
            await cb.answer()
            lines, fk = [], await core.fk_counts(game["id"]) if game["fk"] else {}
            for e in await core.entries(game["id"]):
                lines.append(core.entry_line(e, game["fk"], fk.get(e.user_id, 0)))
            chunk = []
            for line in lines:
                if sum(len(x) + 1 for x in chunk) + len(line) > 3800:
                    await say(bot, chat_id, "\n".join(chunk))
                    chunk = []
                chunk.append(line)
            if chunk:
                await say(bot, chat_id, "\n".join(chunk))
            return
        if action == "join":
            ok, note = await core.join(game, user.id, name_of(user), user.username or "")
        else:
            ok = await core.leave(game, user.id)
            note = "Выписал" if ok else ("Список закрыт" if game["status"] != "open" else "Тебя нет в списке")
        await cb.answer(note)
        if ok:
            core.refresh_later(bot, game["id"])
        return

    # дальше только те, у кого право 🎮 игры
    if not await perms.has(bot, chat_id, user.id, perms.R_GAMES):
        return await cb.answer("Это для модераторов и админов", show_alert=True)
    if action in ("close", "reopen"):
        game = await core.get(int(parts[2]))
        if not game or game["chat_id"] != chat_id or game["status"] not in ("open", "closed"):
            return await cb.answer("Игра уже закончилась")
        chat = await db.get_chat(chat_id)
        if action == "close" and game["status"] == "open":
            await core.update(game["id"], status="closed", closed_at=int(cb.message.date.timestamp()))
            await cb.answer("Список закрыт")
            await core.refresh(bot, game["id"], gap=0)
            await texts.send(bot, chat, "game_closed", ttl=0, thread=game["thread_id"] or None,
                             count=await core.count(game["id"]))
        elif action == "reopen" and game["status"] == "closed":
            await core.update(game["id"], status="open", closed_at=0)
            await cb.answer("Список снова открыт")
            await core.refresh(bot, game["id"], gap=0)
            await core.pin(bot, game)
        else:
            await cb.answer()
        return
    if action == "cancel":
        await cb.answer()
        await state.clear()
        try:
            await cb.message.delete()
        except Exception:
            pass
        return
    if action == "new":
        await core.finish(chat_id)
        text, markup = games_menu()
        await cb.answer()
        return await cb.message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    if action == "pick":
        g = GAMES[parts[2]]
        rows = [[(title, f"lg:mode:{g.kind}:{code}")] for code, title in g.modes]
        await cb.answer()
        return await cb.message.edit_text(f"{g.icon} <b>{g.title}</b>\n➖➖➖➖➖\nКак записываться?",
                                          parse_mode="HTML", reply_markup=kb(rows + [[("⬅️ Назад", "lg:new")]]))
    if action == "mode":   # ФК при запуске не спрашиваем: его ставят по желанию кнопкой 🎯 ФК или /fk
        await cb.answer()
        await start_game(bot, cb.message, parts[2], parts[3], 0, user.id)
        return
    if action == "setfk":
        game = await core.get(int(parts[2]))
        if not game or game["chat_id"] != chat_id or game["status"] not in ("open", "closed"):
            return await cb.answer("Игра уже закончилась")
        await state.set_state(GameFk.value)
        await state.update_data(game_id=game["id"])
        await cb.answer()
        await say(bot, chat_id, f"🎯 {mention(user.id, name_of(user))}, напиши ФК числом: сколько сообщений нужно "
                                "победителю с начала игры (0 убрать). Засчитываются сообщения от 4 букв.",
                  thread=game["thread_id"] or None)
        return
    if action == "fk":
        kind, mode, value = parts[2], parts[3], parts[4]
        if value == "own":
            await state.set_state(GameFk.value)
            await state.update_data(kind=kind, mode=mode, setup_msg=cb.message.message_id)
            await cb.answer()
            return await cb.message.edit_text("✏️ Напиши ФК числом, например 30.", reply_markup=None)
        await cb.answer()
        await start_game(bot, cb.message, kind, mode, int(value), user.id)
        return
    await cb.answer()


async def start_game(bot: Bot, setup_msg: Message, kind: str, mode: str, fk: int, by_id: int) -> None:
    game = await core.create(setup_msg.chat.id, kind, mode, fk, by_id, thread_of(setup_msg) or 0)
    g = GAMES[kind]
    fk_text = f" · ФК {fk}" if fk else ""
    if mode == "hashtag":
        await setup_msg.edit_text(f"{g.icon} <b>{g.title}</b>{fk_text}\n➖➖➖➖➖\n"
                                  "Отправь хэштег одним словом, например <b>#рулетка</b>. Я закреплю список, "
                                  "и все, кто пришлёт этот хэштег, попадут в него.", parse_mode="HTML",
                                  reply_markup=kb([[("✖️ Отмена", "lg:cancel")]]))
        return
    try:
        await setup_msg.delete()
    except Exception:
        pass
    await core.post_list(bot, game)


@router.message(GameFk.value)
async def on_own_fk(message: Message, bot: Bot, state: FSMContext) -> None:
    """Число после кнопки 🎯 ФК. Не число: ожидание снимается, сообщение идёт дальше как обычное."""
    from aiogram.dispatcher.event.bases import SkipHandler
    data = await state.get_data()
    text = (message.text or "").strip()
    await state.clear()
    if not text.isdigit() or not 0 <= int(text) <= 10000:
        raise SkipHandler()
    fk = int(text)
    if data.get("game_id"):
        game = await core.get(data["game_id"])
        if game and game["status"] in ("open", "closed"):
            await core.set_fk(game, fk)
            await core.refresh(bot, game["id"], gap=0)
            await say(bot, message.chat.id, f"🎯 ФК {fk}" if fk else "🎯 ФК убран", reply_to=message)
        return
    game = await core.create(message.chat.id, data["kind"], data["mode"], fk, message.from_user.id,
                             thread_of(message) or 0)
    try:
        await bot.delete_message(message.chat.id, data["setup_msg"])
    except Exception:
        pass
    if data["mode"] == "hashtag":
        g = GAMES[data["kind"]]
        await say(bot, message.chat.id, f"{g.icon} <b>{g.title}</b> · ФК {fk}\n➖➖➖➖➖\n"
                                        "Отправь хэштег одним словом, например <b>#рулетка</b>.", reply_to=message)
        return
    await core.post_list(bot, game)


# ---------- запись сообщениями ----------

async def live_game(message: Message, allowed: bool = True) -> dict | bool:
    """Фильтр: в чате идёт игра со списком (и модератор в чате работает)."""
    if not allowed or not message.from_user or message.from_user.is_bot or message.sender_chat:
        return False
    game = await core.active(message.chat.id)
    return {"game": game} if game else False


async def setup_hashtag(message: Message, bot: Bot, allowed: bool = True) -> dict | bool:
    if "#" not in (message.text or message.caption or ""):
        return False   # без решётки даже не смотрим в базу
    found = await live_game(message, allowed)
    if not found or found["game"]["status"] != "setup":
        return False
    tag = core.normalize_hashtag(message.text or message.caption or "")
    if not tag or not await admin_ok(bot, message):
        return False
    return {"game": found["game"], "tag": tag}


@router.message(setup_hashtag)
async def on_setup_hashtag(message: Message, bot: Bot, game: dict, tag: str) -> None:
    await core.update(game["id"], hashtag=tag, status="open")
    game = await core.get(game["id"])
    await core.post_list(bot, game)


async def join_by_text(message: Message, allowed: bool = True) -> dict | bool:
    """Запись сообщением: хэштег игры, «@» или свой @юзернейм. Чужой юзернейм не записывает."""
    raw = (message.text or message.caption or "").strip()
    if "#" not in raw and not raw.startswith("@"):
        return False   # обычные сообщения сюда не идут, база не трогается
    found = await live_game(message, allowed)
    if not found or found["game"]["status"] != "open":
        return False
    game, text = found["game"], (message.text or message.caption or "").strip()
    if game["mode"] == "hashtag":
        return found if game["hashtag"] and core.normalize_hashtag(text) == game["hashtag"] else False
    own = (message.from_user.username or "").lower()
    if text == "@" or (own and text.lower() == f"@{own}"):
        return found
    return False


async def leave_by_text(message: Message, allowed: bool = True) -> dict | bool:
    """/leave или «выписаться»: убрать свою последнюю запись (то же, что кнопка Выписаться)."""
    if not _LEAVE.match(message.text or ""):
        return False
    return await live_game(message, allowed)


@router.message(leave_by_text)
async def on_leave_text(message: Message, bot: Bot, game: dict) -> None:
    if await core.leave(game, message.from_user.id):
        core.refresh_later(bot, game["id"])
        await say(bot, message.chat.id, "❌ выписал из списка", reply_to=message, ttl=15)


@router.message(join_by_text)
async def on_join_text(message: Message, bot: Bot, game: dict) -> None:
    user = message.from_user
    ok, _note = await core.join(game, user.id, name_of(user), user.username or "")
    if ok:
        core.refresh_later(bot, game["id"])


# ---------- управление списком (админы) ----------

async def admin_command(message: Message, bot: Bot, chat_row: dict | None = None) -> dict | bool:
    text = message.text or ""
    name, m = "", None
    for cmd, rx in (("close", _CLOSE), ("open", _OPEN), ("end", _END), ("top", _TOP), ("add", _ADD),
                    ("remove", _REMOVE), ("fk", _FK), ("list", _LIST)):
        m = rx.match(text)
        if m:
            name = cmd
            break
    if not name:
        m = core.parse_random(text)
        if m is None:
            return False
        name = "random"
    if chat_row is None or not await admin_ok(bot, message):
        return False
    return {"cmd": name, "m": m}


async def state_for_fk(message: Message, game: dict, state: FSMContext) -> None:
    await state.set_state(GameFk.value)
    await state.update_data(game_id=game["id"])
    await say(message.bot, message.chat.id, "🎯 напиши ФК числом: сколько сообщений нужно победителю с начала игры "
                                            "(0 убрать). Засчитываются сообщения от 4 букв.", reply_to=message)


@router.message(admin_command)
async def on_admin_command(message: Message, bot: Bot, cmd: str, m, chat_row: dict, state: FSMContext,
                           allowed: bool = True) -> None:
    chat_id = message.chat.id
    if cmd == "random":   # отдельная команда, игра для неё не нужна
        lo, hi = m
        number = core.roll(lo, hi)
        await texts.send(bot, chat_row, "game_random", reply_to=message, ttl=0,
                         **{"from": lo, "to": hi, "number": number})
        return
    why = allowed_chat(allowed)
    if why:
        await say(bot, chat_id, f"🎮 {why}", reply_to=message)
        return
    game = await core.active(chat_id)
    if cmd == "top":
        await on_top(message, bot, m, game)
        return
    if not game or game["status"] == "setup":
        await say(bot, chat_id, "🎮 сейчас нет игры со списком. Запустить: /game", reply_to=message)
        return
    if cmd == "close":
        if game["status"] == "closed":
            return
        await core.update(game["id"], status="closed", closed_at=int(message.date.timestamp()))
        await core.refresh(bot, game["id"], gap=0)
        await texts.send(bot, chat_row, "game_closed", reply_to=message, ttl=0, count=await core.count(game["id"]))
    elif cmd == "open":
        await core.update(game["id"], status="open", closed_at=0)
        await core.refresh(bot, game["id"], gap=0)
        await core.pin(bot, game)
        await say(bot, chat_id, "🔓 Список снова открыт, записывайтесь", reply_to=message)
    elif cmd == "fk":
        value = m.group(1) or m.group(2)
        if value is None:   # /fk без числа: как кнопка
            await state_for_fk(message, game, state)
            return
        await core.set_fk(game, int(value))
        await core.refresh(bot, game["id"], gap=0)
        await say(bot, chat_id, f"🎯 ФК {int(value)}" if int(value) else "🎯 ФК убран", reply_to=message)
    elif cmd == "list":   # показать живой список заново (внизу чата) и закрепить
        old = game["list_msg_id"]
        await core.post_list(bot, game)
        if old:
            try:
                await bot.unpin_chat_message(chat_id, message_id=old)
            except Exception:
                pass
    elif cmd == "end":
        await core.finish(chat_id)
        if game["list_msg_id"]:
            try:
                await bot.unpin_chat_message(chat_id, message_id=game["list_msg_id"])
            except Exception:
                pass
        await say(bot, chat_id, f"🏁 Игра закончена. Игроков было: {await core.count(game['id'])}", reply_to=message)
    elif cmd == "add":
        rt = real_reply(message)
        if not rt or not rt.from_user or rt.from_user.is_bot:
            await say(bot, chat_id, "ответь «+в список» на сообщение человека", reply_to=message)
            return
        u = rt.from_user
        if game["status"] != "open":
            await core.update(game["id"], status="open")
            game = await core.get(game["id"])
            ok, note = await core.join(game, u.id, name_of(u), u.username or "")
            await core.update(game["id"], status="closed")
        else:
            ok, note = await core.join(game, u.id, name_of(u), u.username or "")
        await say(bot, chat_id, f"{'✅' if ok else '⚠️'} {mention(u.id, name_of(u))}: {esc(note.lower())}",
                  reply_to=message)
        core.refresh_later(bot, game["id"], gap=0)
    elif cmd == "remove":
        pos = int(m.group(1))
        if await core.remove_pos(game["id"], pos):
            await say(bot, chat_id, f"🗑 Номер {pos} убран, номера после него сдвинулись", reply_to=message)
            core.refresh_later(bot, game["id"], gap=0)
        else:
            await say(bot, chat_id, f"номера {pos} в списке нет", reply_to=message)


async def on_top(message: Message, bot: Bot, m, game: dict | None) -> None:
    chat_id = message.chat.id
    rt = real_reply(message)
    if not rt or not rt.from_user or rt.from_user.is_bot:
        await say(bot, chat_id, "ответь «+топ» (/top) или «-топ» (/untop) на сообщение человека.\n"
                                "+топ: постоянный · +топ игра: на эту игру · +топ 3д: на срок", reply_to=message)
        return
    u = rt.from_user
    who = mention(u.id, name_of(u))
    if m.group(1) == "-" or m.group(2):   # «-топ», «-top» или /untop
        n = await tops.take(chat_id, u.id)
        await say(bot, chat_id, f"⭐ {who} больше не топ" if n else f"{who} и так не топ", reply_to=message)
        return
    arg = (m.group(3) or "").lower().replace(" ", "")
    arg = {"game": "игра", "forever": "навсегда"}.get(arg, arg)
    if arg == "игра":
        if not game:
            await say(bot, chat_id, "сейчас нет игры, «+топ игра» выдаётся на текущую игру", reply_to=message)
            return
        await tops.give(chat_id, u.id, message.from_user.id if message.from_user else 0, game_id=game["id"])
        note = "на эту игру"
    elif arg and arg != "навсегда":
        seconds = int(arg[:-1]) * _UNITS[arg[-1]]
        await tops.give(chat_id, u.id, message.from_user.id if message.from_user else 0, seconds=seconds)
        note = f"на {arg[:-1]} {dict(м='мин', m='мин', ч='ч', h='ч', д='дн', d='дн')[arg[-1]]}"
    else:
        await tops.give(chat_id, u.id, message.from_user.id if message.from_user else 0)
        note = "постоянно"
    await say(bot, chat_id, f"⭐ {who} теперь топ {note}: может записаться в список два раза", reply_to=message)
