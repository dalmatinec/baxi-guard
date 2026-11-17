"""Разбор времени, цели и причины: python -m pytest -q"""

from guard.parse import FOREVER, human_duration, human_for, parse_args, parse_duration, strip_command


def test_durations():
    assert parse_duration("30m") == 1800
    assert parse_duration("2h") == 7200
    assert parse_duration("3d") == 3 * 86400
    assert parse_duration("1w") == 7 * 86400
    assert parse_duration("2H") == 7200
    assert parse_duration("15м") == 900
    assert parse_duration("2ч") == 7200


def test_not_durations():
    for token in ("", "30", "m", "2x", "0m", "1.5h", "спам", "-1h", "2 h"):
        assert parse_duration(token) is None, token


def test_longer_than_year_is_forever():
    assert parse_duration("400d") == FOREVER
    assert parse_duration("366d") == 366 * 86400


def test_human_duration():
    assert human_duration(60) == "1 минуту"
    assert human_duration(1800) == "30 минут"
    assert human_duration(3600) == "1 час"
    assert human_duration(7200) == "2 часа"
    assert human_duration(5 * 3600) == "5 часов"
    assert human_duration(86400) == "1 день"
    assert human_duration(3 * 86400) == "3 дня"
    assert human_duration(7 * 86400) == "1 неделю"
    assert human_duration(21 * 86400) == "3 недели"
    assert human_duration(11 * 60) == "11 минут"
    assert human_duration(21 * 60) == "21 минуту"
    assert human_for(0) == "навсегда"
    assert human_for(3600) == "на 1 час"


def test_strip_command():
    assert strip_command("/mute@guard_bot 2h спам") == "2h спам"
    assert strip_command("/mute") == ""
    assert strip_command("  /ban   1d  флуд ") == "1d  флуд"


def test_reply_time_and_reason():
    a = parse_args("/mute 2h флуд в чате", has_reply=True, with_time=True)
    assert (a.username, a.user_id, a.seconds, a.reason) == ("", 0, 7200, "флуд в чате")


def test_reply_without_time():
    a = parse_args("/mute флуд", has_reply=True, with_time=True)
    assert a.seconds is None and a.reason == "флуд"


def test_reply_ignores_target_in_text():
    a = parse_args("/ban @vasya 1d", has_reply=True, with_time=True)
    assert a.username == "" and a.seconds is None and a.reason == "@vasya 1d"


def test_username_target():
    a = parse_args("/ban @Vasya_Pupkin 3d реклама", has_reply=False, with_time=True)
    assert (a.username, a.seconds, a.reason) == ("vasya_pupkin", 3 * 86400, "реклама")
    assert a.has_target


def test_id_target():
    a = parse_args("/mute 123456789 30m", has_reply=False, with_time=True)
    assert (a.user_id, a.seconds, a.reason) == (123456789, 1800, "")


def test_short_number_is_not_id():
    a = parse_args("/warn 12 раз написал", has_reply=False, with_time=False)
    assert a.user_id == 0 and not a.has_target and a.reason == "12 раз написал"


def test_time_ignored_for_commands_without_time():
    a = parse_args("/warn @vasya 2h спам", has_reply=False, with_time=False)
    assert a.username == "vasya" and a.seconds is None and a.reason == "2h спам"


def test_bad_username_is_reason():
    a = parse_args("/mute @a 1h", has_reply=False, with_time=True)
    assert a.username == "" and a.seconds is None and a.reason == "@a 1h"


def test_no_args():
    a = parse_args("/ban", has_reply=False, with_time=True)
    assert not a.has_target and a.seconds is None and a.reason == ""
