"""Проверки генератора сайта. Запуск: python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build  # noqa: E402

DATA = """
[основное]
название: Салон <Тест>
телефон: 8 (900) 111-22-33
telegram: @salon_test
ссылка на карты: https://2gis.ru/x
подпись отзыва: отзыв из 2ГИС
[часы]
Пн–Пт: 10:00–20:00
[услуги]
Стрижка | 900 ₽ | мужская
[отзывы]
Ира | Всё "отлично" & быстро
"""


def test_builds_only_from_data():
    page = build.build(build.parse(DATA), [])
    assert 'href="tel:+79001112233"' in page
    assert 'href="https://t.me/salon_test"' in page
    assert 'href="https://2gis.ru/x"' in page
    assert "Салон &lt;Тест&gt;" in page and "<Тест>" not in page  # всё экранировано
    assert "&quot;отлично&quot; &amp; быстро" in page
    assert "отзыв из 2ГИС" in page
    assert "Демо-шаблон" not in page
    assert "Как у нас" not in page  # фото нет — блока нет


def test_missing_sections_are_skipped():
    page = build.build(build.parse("[основное]\nназвание: Мастерская"), [])
    assert "Отзывы" not in page and "Часы работы" not in page and "tel:" not in page


def test_photos_used():
    page = build.build(build.parse(DATA), ["фото/01.jpg", "фото/02.jpg"])
    assert "has-photo" in page and "фото/01.jpg" in page and 'src="фото/02.jpg"' in page
