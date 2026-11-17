"""Нормализация слов для стоп-слов."""

from guard.normalize import find_stopword, normalize_word, variants


def test_normalize_word():
    assert normalize_word("Плохоооой") == "плохой"
    assert normalize_word("ЁЛКА") == "елка"
    assert normalize_word("п.л.о.х.о") == "плохо"
    assert normalize_word("  Два   Слова ") == "два слова"


def test_latin_twins():
    # латинские a, o, p, e, c, x, y, k, m, t, h, b вместо русских
    assert normalize_word("плoxo") == "плохо"
    assert normalize_word("cyкa") == "сука"
    assert normalize_word("ТНВ") == normalize_word("thb")


def test_find_simple_and_forms():
    words = ["плохой", "дурак"]
    assert find_stopword("ты ПЛОХОЙ человек", words) == "плохой"
    assert find_stopword("какие дураки", words) == "дурак"
    assert find_stopword("всё хорошо", words) is None


def test_find_tricks():
    words = ["плохо"]
    assert find_stopword("это плохооооо", words) == "плохо"
    assert find_stopword("это п.л.о.х.о", words) == "плохо"
    assert find_stopword("это п л о х о", words) == "плохо"
    assert find_stopword("это п-л-о-х-о", words) == "плохо"
    assert find_stopword("это пл*охо", words) == "плохо"
    assert find_stopword("это плoхo", words) == "плохо"  # латинские o
    assert find_stopword("это ПлОхО!!!", words) == "плохо"


def test_yo():
    assert find_stopword("ёжик в тумане", ["ежик"]) == "ежик"
    assert find_stopword("ежик в тумане", ["ёжик"]) == "ёжик"


def test_short_words_only_whole():
    assert find_stopword("тыква вкусная", ["ты"]) is None
    assert find_stopword("ну ты даёшь", ["ты"]) == "ты"


def test_phrases():
    assert find_stopword("а он пиши в личку сразу", ["пиши в личку"]) == "пиши в личку"
    assert find_stopword("пиши мне в личку", ["пиши в личку"]) is None
    assert find_stopword("продам гаражи дешево", ["продам гараж"]) == "продам гараж"


def test_no_false_merge_of_normal_words():
    # обычные слова через запятую не склеиваются так, чтобы потерять второе слово
    assert find_stopword("привет,дурак", ["дурак"]) == "дурак"
    assert any("привет" in v for v in variants("привет, мир"))


def test_empty():
    assert find_stopword("", ["слово"]) is None
    assert find_stopword("слово", []) is None
    assert find_stopword("слово", ["...", ""]) is None
