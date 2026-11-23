"""Ручные команды модерации в группе. Цель: ответом на сообщение, @username или ID после команды."""

import logging
from dataclasses import dataclass
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from guard import actions, config, db, out, perms, texts
from guard.actions import ActionError
from guard.handlers.util import done, drop_command
from guard.parse import FOREVER, Args, human_duration, parse_args

log = logging.getLogger(__name__)
router = Router(name="moderation")
router.message.filter(F.chat.type.in_({"group", "supergroup"}))

DEFAULT_MUTE = 3600
RESTRICT = "can_restrict_members"


@dataclass
class Target:
    user_id: int
    name: str


def real_reply(message: Message) -> Message | None:
    """Сообщение, на которое ответили. В форумах «ответ» на начало темы ответом не считаем."""
    rt = message.reply_to_message
    if rt is None or rt.forum_topic_created:
        return None
    if message.is_topic_message and rt.message_id == message.message_thread_id:
        return None
    return rt


def actor_id(message: Message) -> int:
    """Кто дал команду. Анонимный админ: 0."""
    if perms.is_anon_admin(message) or not message.from_user:
        return 0
    return message.from_user.id


def who(target: "Target") -> str:
    return out.mention(target.user_id, target.name)


def admin_of(message: Message) -> str:
    """Кто наказал, для шаблона: имя ссылкой или «анонимный админ»."""
    if perms.is_anon_admin(message) or not message.from_user:
        return "анонимный админ"
    return out.mention(message.from_user.id, out.full_name(message.from_user))


def reason_line(reason: str) -> str:
    return f"\nПричина: {out.esc(reason)}" if reason else ""


def link_of(message: Message | None) -> str:
    if message is None:
        return ""
    return out.message_link(message.chat.id, message.message_id, message.chat.username or "")


async def resolve(message: Message, with_time: bool) -> tuple[Args, Target | None, str]:
    """(аргументы, цель, ошибка). Ошибка пустая, если цель нашлась."""
    text = message.text or message.caption or ""
    rt = real_reply(message)
    if rt is not None:
        args = parse_args(text, has_reply=True, with_time=with_time)
        if rt.sender_chat or not rt.from_user:
            return args, None, "это сообщение от имени канала или группы, его можно только удалить: /del."
        return args, Target(rt.from_user.id, out.full_name(rt.from_user)), ""
    # упоминание человека без юзернейма (синяя ссылка на имя)
    for ent in message.entities or []:
        if ent.type == "text_mention" and ent.user:
            rest = text.replace(ent.extract_from(text), "", 1)
            args = parse_args(rest, has_reply=True, with_time=with_time)
            return args, Target(ent.user.id, out.full_name(ent.user)), ""
    args = parse_args(text, has_reply=False, with_time=with_time)
    if args.username:
        row = await db.find_by_username(message.chat.id, args.username)
        if row is None:
            return args, None, (f"не знаю @{out.esc(args.username)}: вижу только тех, кто писал при мне. "
                                "Ответьте на сообщение человека или укажите его ID.")
        return args, Target(row["user_id"], row["name"] or "@" + args.username), ""
    if args.user_id:
        name = await db.member_name(message.chat.id, args.user_id)
        if not name:
            m = await perms.member(message.bot, message.chat.id, args.user_id)
            name = out.full_name(m.user) if m else str(args.user_id)
        return args, Target(args.user_id, name), ""
    return args, None, "укажите, кого: ответьте на его сообщение, или @username, или ID после команды."


async def protected(bot: Bot, chat_id: int, user_id: int) -> str:
    """Кого нельзя трогать. Пусто: можно."""
    if user_id == bot.id:
        return "это я."
    if perms.is_owner(user_id):
        return "нельзя: это владелец бота."
    if perms.is_staff_member(await perms.member(bot, chat_id, user_id)):
        return "нельзя: это администратор."
    return ""


async def gate(message: Message, chat_row: dict, allowed: bool, need: int, *rights: str) -> bool:
    """Есть ли у автора команды право в боте (need), работает ли модератор в чате, хватает ли прав боту.
    Без прав в боте человек ответа не получает: админка в Telegram сама прав не даёт."""
    if perms.is_anon_admin(message):
        await done(message, chat_row, "анонимному админу бот прав не даёт, напишите от своего имени.")
        return False
    if not await perms.message_has(message, need):
        return False
    if not allowed and not perms.is_owner(actor_id(message)):
        await done(message, chat_row, "модерация в этом чате не активна.")
        return False
    if rights:
        problem = await perms.rights_problem(message.bot, message.chat.id, *rights)
        if problem:
            await done(message, chat_row, problem)
            return False
    return True


async def target_or_reply(message: Message, chat_row: dict, *, with_time: bool, protect: bool = True):
    args, target, error = await resolve(message, with_time)
    if target is None:
        await done(message, chat_row, error)
        return args, None
    if protect:
        problem = await protected(message.bot, message.chat.id, target.user_id)
        if problem:
            await done(message, chat_row, problem)
            return args, None
    return args, target


# ---------- мут ----------

@router.message(Command("mute"))
async def cmd_mute(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_MUTE, RESTRICT):
        return
    args, target = await target_or_reply(message, chat_row, with_time=True)
    if not target:
        return
    seconds = DEFAULT_MUTE if args.seconds is None else args.seconds
    try:
        await actions.mute(message.bot, chat_row, target.user_id, actor_id(message), seconds, args.reason,
                           link_of(real_reply(message)))
    except ActionError as exc:
        await done(message, chat_row, str(exc))
        return
    await texts.send(message.bot, chat_row, "mod_mute", reply_to=message, user=who(target),
                     duration=human_duration(seconds), reason=out.esc(args.reason), admin=admin_of(message))
    drop_command(message, chat_row)


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_MUTE, RESTRICT):
        return
    args, target = await target_or_reply(message, chat_row, with_time=False)
    if not target:
        return
    try:
        await actions.unmute(message.bot, chat_row, target.user_id, actor_id(message), args.reason)
    except ActionError as exc:
        await done(message, chat_row, str(exc))
        return
    await texts.send(message.bot, chat_row, "mod_unmute", reply_to=message, user=who(target), admin=admin_of(message))
    drop_command(message, chat_row)


# ---------- предупреждения ----------

@router.message(Command("warn"))
async def cmd_warn(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_WARN):
        return
    args, target = await target_or_reply(message, chat_row, with_time=False)
    if not target:
        return
    res = await actions.warn(message.bot, chat_row, target.user_id, actor_id(message), args.reason,
                             link_of(real_reply(message)))
    await texts.announce_warn(message.bot, chat_row, res, who(target), out.esc(args.reason), admin_of(message),
                              reply_to=message)
    if res.error:
        await done(message, chat_row, f"наказать на лимите не вышло: {res.error}")
    drop_command(message, chat_row)


def _date(ts: int) -> str:
    return datetime.fromtimestamp(ts, config.TZ).strftime("%d.%m")


@router.message(Command("warns"))
async def cmd_warns(message: Message, chat_row: dict, allowed: bool) -> None:
    """Ответом: предупреждения человека (для модераторов). Без ответа: свои."""
    if not allowed or not message.from_user:
        return
    is_mod = await perms.message_has(message, perms.R_WARN)
    rt = real_reply(message)
    if rt is not None and is_mod and rt.from_user and not rt.sender_chat:
        user_id, name = rt.from_user.id, out.full_name(rt.from_user)
    elif perms.is_anon_admin(message):
        return
    else:
        user_id, name = message.from_user.id, out.full_name(message.from_user)
    items = await db.active_warns(message.chat.id, user_id, chat_row["warn_ttl_days"])
    head = f"предупреждения {out.mention(user_id, name)}: {len(items)}/{chat_row['warn_limit']}"
    lines = []
    ttl = chat_row["warn_ttl_days"]
    for i, w in enumerate(items, 1):
        line = f"{i}. {_date(w['ts'])}"
        if w["reason"]:
            line += f" {out.esc(w['reason'])}"
        if ttl:
            line += f" (сгорит {_date(w['ts'] + ttl * 86400)})"
        lines.append(line)
    await done(message, chat_row, head + ("\n" + "\n".join(lines) if lines else ""))


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_WARN):
        return
    _args, target = await target_or_reply(message, chat_row, with_time=False, protect=False)
    if not target:
        return
    left = await actions.unwarn(message.bot, chat_row, target.user_id, actor_id(message))
    if left is None:
        await done(message, chat_row, f"у {out.mention(target.user_id, target.name)} нет предупреждений.")
        return
    await texts.send(message.bot, chat_row, "mod_unwarn", reply_to=message, user=who(target),
                     count=f"{left}/{chat_row['warn_limit']}", admin=admin_of(message))
    drop_command(message, chat_row)


# ---------- бан, разбан, кик ----------

@router.message(Command("ban"))
async def cmd_ban(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_BAN, RESTRICT):
        return
    args, target = await target_or_reply(message, chat_row, with_time=True)
    if not target:
        return
    seconds = FOREVER if args.seconds is None else args.seconds
    try:
        await actions.ban(message.bot, chat_row, target.user_id, actor_id(message), seconds, args.reason,
                          link_of(real_reply(message)))
    except ActionError as exc:
        await done(message, chat_row, str(exc))
        return
    await texts.send(message.bot, chat_row, "mod_ban", reply_to=message, user=who(target),
                     duration=human_duration(seconds), reason=out.esc(args.reason), admin=admin_of(message))
    drop_command(message, chat_row)


@router.message(Command("unban"))
async def cmd_unban(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_BAN, RESTRICT):
        return
    args, target = await target_or_reply(message, chat_row, with_time=False, protect=False)
    if not target:
        return
    try:
        await actions.unban(message.bot, chat_row, target.user_id, actor_id(message), args.reason)
    except ActionError as exc:
        await done(message, chat_row, str(exc))
        return
    await texts.send(message.bot, chat_row, "mod_unban", reply_to=message, user=who(target), admin=admin_of(message))
    drop_command(message, chat_row)


@router.message(Command("kick"))
async def cmd_kick(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_BAN, RESTRICT):
        return
    args, target = await target_or_reply(message, chat_row, with_time=False)
    if not target:
        return
    try:
        await actions.kick(message.bot, chat_row, target.user_id, actor_id(message), args.reason,
                           link_of(real_reply(message)))
    except ActionError as exc:
        await done(message, chat_row, str(exc))
        return
    await done(message, chat_row, f"{out.mention(target.user_id, target.name)}: исключение из чата без бана."
                                  + reason_line(args.reason))


# ---------- закреп, ссылка, удаление ----------

@router.message(Command("pin"))
async def cmd_pin(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_CLEAN, "can_pin_messages"):
        return
    rt = real_reply(message)
    if rt is None:
        await done(message, chat_row, "ответьте командой на сообщение, которое надо закрепить.")
        return
    try:
        await message.bot.pin_chat_message(message.chat.id, rt.message_id)
    except TelegramAPIError as exc:
        await done(message, chat_row, f"не вышло закрепить: {out.esc(exc.message)}")
        return
    drop_command(message, chat_row)


@router.message(Command("unpin"))
async def cmd_unpin(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_CLEAN, "can_pin_messages"):
        return
    rt = real_reply(message)
    try:
        await message.bot.unpin_chat_message(message.chat.id, message_id=rt.message_id if rt else None)
    except TelegramAPIError as exc:
        await done(message, chat_row, f"не вышло открепить: {out.esc(exc.message)}")
        return
    await done(message, chat_row, "откреплено." if rt else "последнее закреплённое откреплено.")


@router.message(Command("link"))
async def cmd_link(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_CLEAN, "can_invite_users"):
        return
    by = "анонимный админ" if perms.is_anon_admin(message) else out.full_name(message.from_user)
    try:
        link = await message.bot.create_chat_invite_link(message.chat.id, name=f"от {by}"[:32])
    except TelegramAPIError as exc:
        await done(message, chat_row, f"не вышло создать ссылку: {out.esc(exc.message)}")
        return
    await done(message, chat_row, f"ссылка-приглашение: {link.invite_link}")


@router.message(Command("del"))
async def cmd_del(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await gate(message, chat_row, allowed, perms.R_CLEAN, "can_delete_messages"):
        return
    rt = real_reply(message)
    if rt is None:
        await done(message, chat_row, "ответьте командой на сообщение, которое надо удалить.")
        return
    try:
        await message.bot.delete_messages(message.chat.id, [rt.message_id, message.message_id])
    except TelegramAPIError as exc:
        await done(message, chat_row, f"не вышло удалить: {out.esc(exc.message)}")
        return
    target = rt.from_user.id if rt.from_user and not rt.sender_chat else (rt.sender_chat.id if rt.sender_chat else 0)
    await actions.record(message.bot, chat_row, target, actor_id(message), "delete",
                         reason=(rt.text or rt.caption or "")[:100])


# ---------- режим атаки ----------

@router.message(Command("attack"))
async def cmd_attack(message: Message, chat_row: dict, allowed: bool) -> None:
    """Включить или выключить режим атаки. /attack 1h: на час (без времени: как в настройках)."""
    if not await gate(message, chat_row, allowed, perms.R_BAN, RESTRICT):
        return
    from guard import protect
    drop_command(message, chat_row)
    if chat_row["attack_until"] > db.now():
        await protect.attack_off(message.bot, message.chat.id, by_id=actor_id(message))
        return
    args = parse_args(message.text or "", has_reply=True, with_time=True)
    minutes = (args.seconds // 60) if args.seconds else 0
    await protect.attack_on(message.bot, message.chat.id, by_id=actor_id(message), minutes=min(minutes, 24 * 60))


# ---------- белый список и модераторы бота (только создатель чата и владелец) ----------

async def settings_gate(message: Message, chat_row: dict, allowed: bool, need: int) -> bool:
    if perms.is_anon_admin(message):
        await done(message, chat_row, "анонимному админу бот прав не даёт, напишите от своего имени.")
        return False
    if not message.from_user or not await perms.message_has(message, need):
        return False
    if not allowed and not perms.is_owner(message.from_user.id):
        await done(message, chat_row, "модерация в этом чате не активна.")
        return False
    return True


def people_list(rows: list[dict]) -> str:
    out_lines = []
    for i, r in enumerate(rows, 1):
        name = r["name"] or (("@" + r["username"]) if r["username"] else str(r["user_id"]))
        out_lines.append(f"{i}. {out.mention(r['user_id'], name)}")
    return "\n".join(out_lines)


@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, chat_row: dict, allowed: bool) -> None:
    if not await settings_gate(message, chat_row, allowed, perms.R_SETTINGS):
        return
    rt = real_reply(message)
    if rt is None or not rt.from_user or rt.sender_chat:
        rows = await db.whitelist(message.chat.id)
        text = ("белый список (им можно ссылки и пересылки):\n" + people_list(rows)) if rows else \
            "белый список пуст. Добавить: ответьте /whitelist на сообщение человека."
        await done(message, chat_row, text)
        return
    added = await db.toggle_whitelist(message.chat.id, rt.from_user.id, message.from_user.id)
    who_ = out.mention(rt.from_user.id, out.full_name(rt.from_user))
    await done(message, chat_row, f"{who_} {'теперь в белом списке' if added else 'больше не в белом списке'}.")


# ---------- права в боте ----------

RIGHTS_TTL = 180   # карточка прав в чате живёт три минуты


def rights_card(chat_id: int, user_id: int, name: str, f: int) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"🔑 <b>Права в боте</b> · {out.mention(user_id, name)}", "─────────────", perms.rights_text(f), "",
             "<i>Нажми, чтобы дать или забрать. Админка в Telegram сама прав в боте не даёт.</i>"]
    buttons = []
    for bit, icon, title in perms.RIGHT_NAMES:
        on = bool(f & bit) or (bit != perms.R_ADMIN and bool(f & perms.R_ADMIN))
        buttons.append(InlineKeyboardButton(text=f"{'✅' if on else '▫️'} {icon} {title}",
                                            callback_data=f"rt:{chat_id}:{user_id}:{bit}"))
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="🚫 Забрать все", callback_data=f"rt:{chat_id}:{user_id}:0")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command("mod"))
async def cmd_mod(message: Message, chat_row: dict, allowed: bool) -> None:
    """Права в боте. Ответом: галочки для этого человека. Без ответа: у кого какие права."""
    if not await settings_gate(message, chat_row, allowed, perms.R_GRANT):
        return
    rt = real_reply(message)
    if rt is None or not rt.from_user or rt.sender_chat or rt.from_user.is_bot:
        rows = await db.rights_list(message.chat.id)
        lines = [f"{i}. {out.mention(r['user_id'], r['name'] or str(r['user_id']))}: {perms.rights_text(r['flags'])}"
                 for i, r in enumerate(rows, 1)]
        text = ("права в боте:\n" + "\n".join(lines)) if lines else \
            "прав в боте пока ни у кого нет (кроме создателя). Выдать: ответьте /mod на сообщение человека."
        await done(message, chat_row, text)
        return
    u = rt.from_user
    await db.touch_member(message.chat.id, u.id, u.username or "", out.full_name(u), message=False)
    text, markup = rights_card(message.chat.id, u.id, out.full_name(u), await db.rights_of(message.chat.id, u.id))
    await out.answer(message, text, ttl=RIGHTS_TTL, markup=markup)
    drop_command(message, chat_row)


async def toggle_right(bot: Bot, chat_id: int, actor: int, user_id: int, bit: int) -> tuple[bool, str, int]:
    """Дать или забрать право. (получилось ли, пояснение, новые права)."""
    current = await db.rights_of(chat_id, user_id)
    if await perms.is_creator(bot, chat_id, user_id):
        return False, "У создателя чата и так все права", current
    if bit == 0:
        if not await perms.has(bot, chat_id, actor, perms.R_GRANT):
            return False, "Нужно право выдавать права", current
        if current & perms.R_ADMIN and not await perms.has(bot, chat_id, actor, perms.R_ADMIN):
            return False, "Админа бота может снять только создатель", current
        await db.set_rights(chat_id, user_id, 0, actor)
        return True, "Все права забраны", 0
    if not await perms.can_grant(bot, chat_id, actor, bit):
        return False, "Это право ты выдать не можешь", current
    new = current ^ bit
    if current & perms.R_ADMIN and bit != perms.R_ADMIN:   # снимаем одно право с админа бота: остальные остаются
        new = (perms.ALL_FLAGS & ~bit)
    await db.set_rights(chat_id, user_id, new, actor)
    perms.forget(chat_id, user_id)
    return True, "Готово", new


@router.callback_query(F.data.startswith("rt:"))
async def on_right(cb: CallbackQuery, bot: Bot) -> None:
    _, chat_id, user_id, bit = cb.data.split(":")
    chat_id, user_id, bit = int(chat_id), int(user_id), int(bit)
    ok, note, new = await toggle_right(bot, chat_id, cb.from_user.id, user_id, bit)
    await cb.answer(note, show_alert=not ok)
    if ok:
        name = await db.member_name(chat_id, user_id) or str(user_id)
        text, markup = rights_card(chat_id, user_id, name, new)
        try:
            await cb.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except TelegramAPIError:
            pass


# ---------- настройки ----------

async def baxi_here(bot: Bot, chat_id: int) -> bool:
    if not config.BAXI_BOT_ID:
        return False
    m = await perms.member(bot, chat_id, config.BAXI_BOT_ID)
    return m is not None and m.status in ("member", "administrator")


@router.message(Command("settings"))
async def cmd_settings(message: Message, chat_row: dict, command: CommandObject) -> None:
    """Кнопка в личку, сразу в настройки этого чата. Если в чате Бакси, голую /settings оставляем ему."""
    if not command.mention and await baxi_here(message.bot, message.chat.id):
        return
    if perms.is_anon_admin(message) or not await perms.message_has(message, perms.R_SETTINGS):
        return
    me = await message.bot.me()
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⚙️ Открыть настройки", url=f"https://t.me/{me.username}?start=s_{message.chat.id}", style="primary")]])
    await done(message, chat_row, "настройки модерации в личке с ботом.", markup=markup)
