"""Проверки логики записи без Telegram. Запуск: python -m pytest tests"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402
from slots import bookable_days, free_slots  # noqa: E402
from storage import CANCELLED, SlotTaken, Storage  # noqa: E402

TZ = ZoneInfo(config.TIMEZONE)
MONDAY = date(2026, 11, 30)
SUNDAY = date(2026, 11, 29)
EARLY = datetime(2026, 11, 1, 9, 0, tzinfo=TZ)  # «сейчас» задолго до записи
SVC60 = {"name": "Маникюр", "price": 1200, "minutes": 60}


def at(d, hm):
    h, m = map(int, hm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def hhmm(slots):
    return [f"{s:%H:%M}" for s in slots]


def test_slots_respect_hours_and_break():
    slots = hhmm(free_slots(MONDAY, 60, [], EARLY, config))
    assert slots[0] == "10:00" and slots[-1] == "18:00"  # 18:00–19:00 — последнее
    assert "13:00" in slots and "13:30" not in slots      # 13:30–14:30 задевает перерыв
    assert "14:00" not in slots and "14:30" not in slots
    assert "15:00" in slots


def test_long_service_needs_room():
    slots = hhmm(free_slots(MONDAY, 90, [], EARLY, config))
    assert "17:30" in slots and "18:00" not in slots  # 18:00 + 90 мин > 19:00
    assert "12:30" in slots and "13:00" not in slots  # 13:00–14:30 задевает перерыв


def test_day_off_and_holiday(monkeypatch):
    assert free_slots(SUNDAY, 60, [], EARLY, config) == []
    monkeypatch.setattr(config, "DAYS_OFF", [MONDAY.isoformat()])
    assert free_slots(MONDAY, 60, [], EARLY, config) == []


def test_busy_slots_hidden():
    busy = [(at(MONDAY, "11:00"), at(MONDAY, "12:30"))]
    slots = hhmm(free_slots(MONDAY, 60, busy, EARLY, config))
    assert "10:00" in slots
    assert not {"10:30", "11:00", "11:30", "12:00"} & set(slots)
    assert "12:30" in slots


def test_min_notice():
    now = at(MONDAY, "11:10")
    slots = hhmm(free_slots(MONDAY, 60, [], now, config))
    assert slots[0] == "12:30"  # 11:10 + 60 минут → ближайшее окно по сетке 12:30


def test_bookable_days_skip_sunday():
    now = at(date(2026, 11, 28), "08:00")  # суббота
    days = bookable_days(60, [], now, config)
    assert SUNDAY not in days and MONDAY in days
    assert len(days) <= config.DAYS_AHEAD + 1


def test_storage_roundtrip_and_conflict(tmp_path):
    st = Storage(str(tmp_path / "zapisi.csv"))
    b = st.add(at(MONDAY, "10:00"), SVC60, "Анна", "+79001234567", 111, EARLY)
    loaded = st.get(b.id)
    assert loaded.client_name == "Анна" and loaded.start == b.start
    with pytest.raises(SlotTaken):
        st.add(at(MONDAY, "10:30"), SVC60, "Борис", "+79000000000", 222, EARLY)
    assert st.busy() == [(at(MONDAY, "10:00"), at(MONDAY, "11:00"))]

    st.cancel(b.id)
    assert st.get(b.id).status == CANCELLED
    assert st.busy() == []  # окно освободилось
    st.add(at(MONDAY, "10:30"), SVC60, "Борис", "+79000000000", 222, EARLY)


def test_csv_opens_in_excel(tmp_path):
    path = tmp_path / "zapisi.csv"
    Storage(str(path)).add(at(MONDAY, "10:00"), SVC60, "Анна", "+79001234567", 1, EARLY)
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM — Excel поймёт кириллицу
    header = raw.decode("utf-8-sig").splitlines()[0]
    assert header.startswith("Номер;Начало;Услуга")


def test_reminders(tmp_path):
    st = Storage(str(tmp_path / "zapisi.csv"))
    early = st.add(at(MONDAY, "10:00"), SVC60, "Анна", "1", 1, EARLY)
    late = st.add(at(MONDAY, "15:00"), SVC60, "Борис", "2", 2, at(MONDAY, "11:00"))
    # За двое суток — ещё рано
    assert st.need_reminder(at(MONDAY, "10:00") - timedelta(hours=48), 24) == []
    # За сутки — напоминаем Анне; Борис записался в тот же день, ему не нужно
    due = st.need_reminder(at(MONDAY, "10:00") - timedelta(hours=23), 24)
    assert [b.id for b in due] == [early.id]
    st.mark_reminded(early.id)
    assert st.need_reminder(at(MONDAY, "10:00") - timedelta(hours=22), 24) == []
    assert late.id  # запись Бориса существует, просто без напоминания


# --- обработчики бота на поддельных объектах Telegram ----------------------

class FakeMessage:
    def __init__(self, text="", contact=None):
        self.text, self.contact, self.replies = text, contact, []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class FakeQuery:
    def __init__(self, data):
        self.data, self.answers, self.edits = data, [], []

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_text(self, text, **kw):
        self.edits.append(text)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


@pytest.fixture
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    import bot as bot_module
    monkeypatch.setattr(bot_module, "storage", Storage(str(tmp_path / "zapisi.csv")))
    monkeypatch.setattr(bot_module, "ADMIN_ID", 999)
    monkeypatch.setattr(bot_module, "now", lambda: EARLY)
    return bot_module


def make_update(user_id, message=None, query=None):
    return SimpleNamespace(effective_user=SimpleNamespace(id=user_id, first_name="Анна"),
                           message=message, effective_message=message, callback_query=query)


def test_full_booking_and_admin_notification(bot):
    import asyncio
    ctx = SimpleNamespace(user_data={"service": 0, "day": MONDAY.isoformat()}, bot=FakeBot())

    async def flow():
        q = FakeQuery("time:10:00")
        q.message = FakeMessage()
        await bot.choose_time(make_update(1, query=q), ctx)
        await bot.ask_name(make_update(1, message=FakeMessage("Анна")), ctx)
        msg = FakeMessage("8 (900) 123-45-67")
        await bot.ask_phone(make_update(1, message=msg), ctx)
        return msg

    msg = asyncio.run(flow())
    assert "Вы записаны" in msg.replies[0]
    bookings = bot.storage.load()
    assert len(bookings) == 1 and bookings[0].phone == "+79001234567"
    assert ctx.bot.sent and ctx.bot.sent[0][0] == 999 and "Новая запись" in ctx.bot.sent[0][1]


def test_stranger_cannot_cancel(bot):
    import asyncio
    b = bot.storage.add(at(MONDAY, "10:00"), SVC60, "Анна", "1", 1, EARLY)
    ctx = SimpleNamespace(user_data={}, bot=FakeBot())
    q = FakeQuery(f"cancel:{b.id}")
    asyncio.run(bot.cancel_booking(make_update(2, query=q), ctx))
    assert bot.storage.get(b.id).status != CANCELLED
    q = FakeQuery(f"cancel:{b.id}")
    asyncio.run(bot.cancel_booking(make_update(1, query=q), ctx))
    assert bot.storage.get(b.id).status == CANCELLED
    assert "Отмена" in ctx.bot.sent[0][1]


def test_clean_phone(bot):
    assert bot.clean_phone("+7 900 123-45-67") == "+79001234567"
    assert bot.clean_phone("89001234567") == "+79001234567"
    assert bot.clean_phone("9001234567") == "+79001234567"
    assert bot.clean_phone("+998 90 123 45 67") == "+998901234567"
    assert bot.clean_phone("привет") is None
