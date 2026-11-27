"""Нормализация текста для стоп-слов. Чистая логика без Telegram.

Перед сравнением: нижний регистр, ё в е, латинские двойники букв в кириллицу, без повторов букв
(«плохоооой» -> «плохой»), без знаков между буквами («п.л.о.х.о» -> «плохо»).
Стоп-слова проходят ту же обработку, поэтому двойные буквы в них тоже схлопываются, и это не мешает."""

import re

# латинские буквы, похожие на русские: a o e p c x y k m t h b
LATIN_TWINS = str.maketrans("aoepcxykmthb", "аоерсхукмтнв")
_REPEATS = re.compile(r"(.)\1+")
_WORD = re.compile(r"[^\W_]+")
_INNER_MARKS = re.compile(r"(?<=[^\W_])[^\w\s]+(?=[^\W_])")


def base(text: str) -> str:
    return (text or "").lower().replace("ё", "е").translate(LATIN_TWINS)


def squash(word: str) -> str:
    return _REPEATS.sub(r"\1", word)


def normalize_word(word: str) -> str:
    """Стоп-слово или фраза в том виде, в каком сравниваем: 'Пл.оХоо   слово' -> 'плохо слово'."""
    b = _INNER_MARKS.sub("", base(word))
    return " ".join(squash(w) for w in _WORD.findall(b))


def _join_letter_runs(words: list[str]) -> list[str]:
    """'п л о х о' и 'п.л.о.х.о' дают по букве на слово: склеиваем подряд идущие одиночные буквы (от трёх)."""
    out, run = [], []
    for w in words + [""]:
        if len(w) == 1:
            run.append(w)
            continue
        if len(run) >= 3:
            out.append("".join(run))
        run = []
        if w:
            out.append(w)
    return out


def variants(text: str) -> list[list[str]]:
    """Несколько разборов текста на слова, каждый уже нормализован."""
    b = base(text)
    plain = _WORD.findall(b)
    joined = _join_letter_runs(plain)
    no_marks = _WORD.findall(_INNER_MARKS.sub("", b))
    result = []
    for words in (plain, joined, no_marks):
        squashed = [squash(w) for w in words]
        if squashed not in result:
            result.append(squashed)
    return result


def _word_hit(token: str, word: str) -> bool:
    # короткие слова только целиком, иначе «ты» ловилось бы в «тыква»; длинные и с окончаниями
    return token == word if len(word) <= 3 else token.startswith(word)


def find_stopword(text: str, stopwords: list[str]) -> str | None:
    """Первое стоп-слово, которое есть в тексте. Сравнение по нормализованным формам."""
    if not text or not stopwords:
        return None
    prepared = [(w, normalize_word(w)) for w in stopwords]
    prepared = [(w, n) for w, n in prepared if n]
    for words in variants(text):
        for original, norm in prepared:
            if " " in norm:
                if _phrase_prefix(words, norm.split()):
                    return original
            elif any(_word_hit(t, norm) for t in words):
                return original
    return None


def _phrase_prefix(words: list[str], phrase: list[str]) -> bool:
    """Фраза из нескольких слов подряд, последнее слово может быть с окончанием."""
    n = len(phrase)
    for i in range(len(words) - n + 1):
        if words[i:i + n - 1] == phrase[:-1] and _word_hit(words[i + n - 1], phrase[-1]):
            return True
    return False
