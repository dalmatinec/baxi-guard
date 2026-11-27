"""Поиск ссылок и скам-признаков."""

from guard.links import (classify, domain_allowed, extract_links, has_card, has_wallet, link_violation,
                         looks_suspicious, normalize_domain, scam_reason)


def kinds(text, entity_urls=None):
    return [(link.kind, link.domain, link.tg_name) for link in extract_links(text, entity_urls)]


def test_normalize_domain():
    assert normalize_domain("https://www.Example.com/path?x=1") == "example.com"
    assert normalize_domain("sub.example.org") == "sub.example.org"
    assert normalize_domain("youtube.com:443") == "youtube.com"
    assert normalize_domain("сайт.рф") == "сайт.рф"
    assert normalize_domain("не домен") == ""
    assert normalize_domain("localhost") == ""


def test_domain_allowed_with_subdomains():
    assert domain_allowed("youtube.com", ["youtube.com"])
    assert domain_allowed("m.youtube.com", ["youtube.com"])
    assert not domain_allowed("notyoutube.com", ["youtube.com"])


def test_extract_from_text():
    assert kinds("зайди на https://example.com/page") == [("site", "example.com", "")]
    assert kinds("смотри www.example.org") == [("site", "example.org", "")]
    assert kinds("голый домен spam-site.ru тут") == [("site", "spam-site.ru", "")]
    assert kinds("версия 1.5 и файл readme.md, т.е. ничего") == []
    assert kinds("почта vasya@mail.ru") == []


def test_extract_from_entities():
    assert kinds("тык", ["https://example.com"]) == [("site", "example.com", "")]
    assert kinds("https://example.com", ["https://example.com"]) == [("site", "example.com", "")]


def test_telegram_links():
    assert classify("https://t.me/+AbCdEf123").kind == "invite"
    assert classify("t.me/joinchat/AAAA").kind == "invite"
    assert classify("https://t.me/addlist/xyz").kind == "invite"
    link = classify("https://t.me/some_channel/123")
    assert (link.kind, link.tg_name) == ("telegram", "some_channel")
    assert classify("t.me/s/news_channel").tg_name == "news_channel"
    assert classify("https://cool.t.me").tg_name == "cool"
    assert classify("tg://resolve?domain=spam_bot").tg_name == "spam_bot"
    assert classify("telegram.me/spam_bot").kind == "telegram"


def test_violation_rules():
    site = extract_links("https://example.com")
    assert link_violation(site, allowed_domains=[]) == "ссылка на сторонний ресурс"
    assert link_violation(site, allowed_domains=["example.com"]) == ""
    invite = extract_links("https://t.me/+abc")
    assert link_violation(invite, allowed_domains=[]) == "приглашение в сторонний чат"
    channel = extract_links("t.me/other_channel")
    assert link_violation(channel, allowed_domains=[]) == "реклама стороннего канала или бота"
    own = extract_links("t.me/our_chat/55")
    assert link_violation(own, allowed_domains=[], own_username="Our_Chat") == ""
    assert link_violation(channel, allowed_domains=["t.me"]) == ""


def test_newbie_any_link():
    allowed = extract_links("https://youtube.com/watch?v=1")
    assert link_violation(allowed, allowed_domains=["youtube.com"]) == ""
    assert link_violation(allowed, allowed_domains=["youtube.com"], newbie=True) == "ссылка в первые сутки после вступления"
    assert link_violation([], allowed_domains=[], newbie=True) == ""


def test_scam_phrases():
    assert scam_reason("Заработок от 5000 руб в день, без вложений") == "предложение заработка"
    assert scam_reason("доход 300$ в день") == "предложение заработка"
    assert scam_reason("Кому интересно пишите в лс") == "приглашение в личные сообщения"
    assert scam_reason("пиши мне в личку") == "приглашение в личные сообщения"
    assert scam_reason("бесплатно раздаю USDT") == "сомнительная раздача"
    assert scam_reason("зaрaбoтoк в день") == "предложение заработка"  # латинские двойники
    assert scam_reason("я сегодня заработал на работе") == ""
    assert scam_reason("привет всем") == ""


def test_wallets():
    assert has_wallet("кидай на 0x52908400098527886E0F7030069857D2E4169EE7")
    assert has_wallet("TRC20: TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t")
    assert has_wallet("UQBmzW4wYlFW0tiBgtJ5c6V0dOcZSAOaZZ8NTHhTX3XcWBDW")
    assert has_wallet("bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    assert scam_reason("кидай на 0x52908400098527886E0F7030069857D2E4169EE7") == "реквизиты криптокошелька"
    assert not has_wallet("обычный текст без адресов 12345")


def test_cards():
    assert has_card("карта 4111 1111 1111 1111")
    assert has_card("5555555555554444")
    assert not has_card("4111 1111 1111 1112")  # не проходит проверку Луна
    assert not has_card("телефон 8 777 123 45 67")
    assert scam_reason("переводи на 4111-1111-1111-1111") == "банковские реквизиты"


def test_weak_signals():
    assert looks_suspicious("есть подработка на дому")
    assert looks_suspicious("инвестиции в крипту")
    assert not looks_suspicious("пошли гулять")
