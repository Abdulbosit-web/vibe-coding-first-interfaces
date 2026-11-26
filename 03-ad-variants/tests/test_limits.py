"""Проверки счётчика лимитов. Запуск: python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_limits as cl  # noqa: E402


def test_parse_variants_and_skip_analysis():
    text = "## Разбор\n- пункт\n\n## Вариант 1\nГипотеза: боль\n**Заголовок:** Привет\nТекст: Мир"
    vs = cl.parse(text)
    assert [v.title for v in vs] == ["Разбор", "Вариант 1"]
    assert vs[1].fields == {"Гипотеза": "боль", "Заголовок": "Привет", "Текст": "Мир"}


def test_yandex_punctuation_not_counted():
    n, _ = cl.length("Да, да! Да.", "yandex")
    assert n == 8  # 11 символов минус 3 узких знака
    n, note = cl.length("." * 20, "yandex")
    assert n == 5 and "больше 15" in note


def test_too_long_and_long_word_are_errors():
    v = cl.Variant("В", {"Заголовок": "а" * 57})
    _, errors = cl.check([v], "yandex")
    assert errors == 2  # длиннее 56 и слово длиннее 22
    v = cl.Variant("В", {"Текст": "а " * 80})
    _, errors = cl.check([v], "telegram")
    assert errors == 0  # 160 символов — ровно в лимит


def test_risky_words_reported():
    lines, _ = cl.check([cl.Variant("В", {"Текст": "Лучший сервис, №1 в городе"})], "vk")
    joined = "\n".join(lines)
    assert "Лучший" in joined and "№1" in joined
