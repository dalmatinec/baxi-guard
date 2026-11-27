"""Игры со списком: общее ядро (список, ФК, топы) и сами игры, каждая отдельным модулем.

  core.py      список игры: запись, выписка, закрыть и открыть, ФК, рендер живого сообщения, рандом
  tops.py      топ-участники: постоянные в чате и временные (на игру или на срок), у них по 2 записи
  roulette.py  рулетка: по хэштегу или обычная (кнопка, @ или свой юзернейм)

Новая игра: модуль с GAME = Game(...) и строка в GAMES."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Game:
    kind: str          # ключ в базе
    title: str         # как называется в меню и в списке
    icon: str
    modes: tuple[tuple[str, str], ...]   # (код режима, название кнопки)


GAMES: dict[str, Game] = {}


def register(game: Game) -> Game:
    GAMES[game.kind] = game
    return game


from guard.gamecore import roulette  # noqa: E402,F401  регистрирует рулетку
