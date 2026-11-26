"""Проверки утренней сводки на выдуманных данных. Запуск: python -m pytest tests"""

import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import digest  # noqa: E402

MSK = ZoneInfo("Europe/Moscow")


def make_settings(**kw):
    base = dict(imap_host="imap.test", imap_port=993, imap_user="u", imap_password="p",
                ics_urls=[], bot_token="t", chat_id="1",
                important_senders=["buh@firma.test", "@bank.test"],
                keywords=digest.DEFAULT_KEYWORDS, tz=MSK)
    base.update(kw)
    return digest.Settings(**base)


def raw_mail(sender, subject, body="Привет", when=None):
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Date"] = (when or datetime.now(timezone.utc)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg.set_content(body)
    return msg.as_bytes()


def test_parse_mail_decodes_russian_headers():
    m = digest.parse_mail(raw_mail("Бухгалтерия <buh@firma.test>", "Акт сверки за ноябрь"))
    assert m.sender_name == "Бухгалтерия"
    assert m.sender_email == "buh@firma.test"
    assert m.subject == "Акт сверки за ноябрь"


def test_important_by_sender_domain_and_keyword():
    s = make_settings()
    by_sender = digest.parse_mail(raw_mail("buh@firma.test", "Привет"))
    by_domain = digest.parse_mail(raw_mail("noreply@bank.test", "Выписка"))
    by_subject = digest.parse_mail(raw_mail("x@y.test", "Счёт №15 на оплату"))
    by_body = digest.parse_mail(raw_mail("x@y.test", "Вопрос", "Нужно срочно подписать"))
    spam = digest.parse_mail(raw_mail("promo@shop.test", "Скидки недели"))
    important, other = digest.split_mails([by_sender, by_domain, by_subject, by_body, spam], s)
    assert len(important) == 4
    assert other == [spam]


def test_build_message_structure():
    meetings = [
        digest.Meeting("Планёрка", None, None),
        digest.Meeting("Созвон с клиентом", datetime(2026, 11, 26, 11, 0, tzinfo=MSK),
                       datetime(2026, 11, 26, 11, 30, tzinfo=MSK), "Zoom"),
    ]
    mail = digest.Mail("Банк <тест>", "b@bank.test", "Оплата & счёт", datetime.now(timezone.utc))
    text = digest.build_message(date(2026, 11, 26), meetings, [mail], other_count=7)
    assert "26 ноября, четверг" in text
    assert text.index("Встречи") < text.index("Важные письма")  # сначала встречи
    assert "• весь день — Планёрка" in text
    assert "11:00–11:30 — Созвон с клиентом · Zoom" in text
    assert "Банк &lt;тест&gt;" in text and "Оплата &amp; счёт" in text  # HTML экранирован
    assert "И ещё 7 писем." in text


def test_plural_forms():
    text1 = digest.build_message(date(2026, 1, 1), None, [], other_count=1)
    text3 = digest.build_message(date(2026, 1, 1), None, [], other_count=3)
    assert "И ещё 1 письмо." in text1
    assert "И ещё 3 письма." in text3
    assert "Встречи" not in text1  # календарь не подключён — блока нет


ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//test//RU
BEGIN:VEVENT
UID:1
DTSTART:20261126T070000Z
DTEND:20261126T080000Z
SUMMARY:Встреча с поставщиком
LOCATION:Офис
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;TZID=Europe/Moscow:20261102T090000
DTEND;TZID=Europe/Moscow:20261102T091500
RRULE:FREQ=WEEKLY;BYDAY=TH
SUMMARY:Еженедельная планёрка
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART;VALUE=DATE:20261126
DTEND;VALUE=DATE:20261127
SUMMARY:День рождения Анны
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART:20261127T070000Z
DTEND:20261127T080000Z
SUMMARY:Завтрашняя встреча
END:VEVENT
BEGIN:VEVENT
UID:5
DTSTART:20261126T120000Z
DTEND:20261126T130000Z
STATUS:CANCELLED
SUMMARY:Отменённая
END:VEVENT
END:VCALENDAR
""".replace("\n", "\r\n").encode()


def test_meetings_for_day_handles_recurring_allday_and_timezones():
    meetings = digest.meetings_for_day(ICS, date(2026, 11, 26), MSK)
    titles = [m.title for m in meetings]
    assert titles == ["День рождения Анны", "Еженедельная планёрка", "Встреча с поставщиком"]
    supplier = meetings[2]
    assert supplier.start.strftime("%H:%M") == "10:00"  # 07:00 UTC = 10:00 МСК


def test_split_for_telegram():
    text = "\n".join(f"строка {i} " + "x" * 100 for i in range(100))
    chunks = digest.split_for_telegram(text)
    assert len(chunks) > 1
    assert all(len(c) <= digest.TELEGRAM_LIMIT for c in chunks)
    assert "\n".join(chunks) == text


class FakeIMAP:
    """Поддельный почтовый сервер: запоминает, какие команды ему дали."""
    calls = []

    def __init__(self, host, port):
        self.mails = [
            raw_mail("buh@firma.test", "Счёт", when=datetime.now(timezone.utc) - timedelta(hours=2)),
            raw_mail("old@x.test", "Старое", when=datetime.now(timezone.utc) - timedelta(hours=40)),
        ]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def login(self, u, p):
        FakeIMAP.calls.append(("login", u))

    def select(self, box, readonly=False):
        FakeIMAP.calls.append(("select", box, readonly))
        return "OK", [b"2"]

    def search(self, charset, *criteria):
        return "OK", [b"1 2"]

    def fetch(self, msg_id, what):
        FakeIMAP.calls.append(("fetch", what))
        return "OK", [(b"header", self.mails[int(msg_id) - 1]), b")"]

    def logout(self):
        pass


def test_fetch_mails_is_read_only(monkeypatch):
    monkeypatch.setattr(digest.imaplib, "IMAP4_SSL", FakeIMAP)
    FakeIMAP.calls = []
    mails = digest.fetch_mails(make_settings(), datetime.now(MSK))
    assert [m.subject for m in mails] == ["Счёт"]  # письмо 40-часовой давности отброшено
    assert ("select", "INBOX", True) in FakeIMAP.calls  # папка открыта только на чтение
    fetches = [c[1] for c in FakeIMAP.calls if c[0] == "fetch"]
    assert fetches and all("PEEK" in f for f in fetches)  # письма не помечаются прочитанными
    # никаких STORE / EXPUNGE / COPY / APPEND
    assert {c[0] for c in FakeIMAP.calls} <= {"login", "select", "fetch"}
