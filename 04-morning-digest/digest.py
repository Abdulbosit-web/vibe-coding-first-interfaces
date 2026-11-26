"""
Утренняя сводка в Telegram.

Что делает:
  1. Читает почту по IMAP за последние сутки (только читает!).
  2. Отбирает важные письма: от нужных отправителей или со словами
     «счёт», «оплата», «срочно», «договор».
  3. Берёт встречи на сегодня из календаря по ссылке ICS.
  4. Собирает одно короткое сообщение и присылает его вам в Telegram.

Запуск:
  python digest.py            — собрать сводку и отправить в Telegram
  python digest.py --dry-run  — только показать сообщение в консоли, ничего не отправлять
  python digest.py --test-telegram — отправить тестовое «Привет», чтобы проверить бота

Все пароли и токены лежат в файле .env (см. .env.example).
"""

from __future__ import annotations

import argparse
import email
import html
import imaplib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.policy import default as default_policy
from email.utils import parseaddr, parsedate_to_datetime
from zoneinfo import ZoneInfo

# Готовые адреса IMAP-серверов популярных почт
IMAP_PRESETS = {
    "yandex": "imap.yandex.ru",
    "mailru": "imap.mail.ru",
    "gmail": "imap.gmail.com",
}

DEFAULT_KEYWORDS = ["счёт", "счет", "оплата", "оплат", "срочно", "договор"]

# Telegram не принимает сообщения длиннее 4096 символов
TELEGRAM_LIMIT = 4096


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    ics_urls: list[str]
    bot_token: str
    chat_id: str
    important_senders: list[str]
    keywords: list[str]
    tz: ZoneInfo
    hours_back: int = 24
    max_important: int = 15


def _split_list(value: str) -> list[str]:
    """«a, b; c» → ['a', 'b', 'c']"""
    return [part.strip() for part in re.split(r"[,;\n]", value or "") if part.strip()]


def load_settings() -> Settings:
    """Читаем настройки из .env (лежит рядом с этим файлом)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    except ImportError:
        pass  # без python-dotenv тоже работает, если переменные заданы в системе

    env = os.environ.get
    host = env("IMAP_HOST", "").strip()
    host = IMAP_PRESETS.get(host.lower(), host)  # можно писать просто «yandex»

    keywords = _split_list(env("KEYWORDS", "")) or DEFAULT_KEYWORDS

    return Settings(
        imap_host=host,
        imap_port=int(env("IMAP_PORT", "993")),
        imap_user=env("IMAP_USER", "").strip(),
        imap_password=env("IMAP_PASSWORD", "").strip(),
        ics_urls=_split_list(env("ICS_URL", "")),
        bot_token=env("TELEGRAM_BOT_TOKEN", "").strip(),
        chat_id=env("TELEGRAM_CHAT_ID", "").strip(),
        important_senders=[s.lower() for s in _split_list(env("IMPORTANT_SENDERS", ""))],
        keywords=[k.lower() for k in keywords],
        tz=ZoneInfo(env("TIMEZONE", "Europe/Moscow").strip()),
        hours_back=int(env("HOURS_BACK", "24")),
        max_important=int(env("MAX_IMPORTANT", "15")),
    )


# ---------------------------------------------------------------------------
# Почта
# ---------------------------------------------------------------------------

@dataclass
class Mail:
    sender_name: str
    sender_email: str
    subject: str
    received: datetime
    text: str = ""  # начало текста письма — для поиска ключевых слов


def _decode(value: str | None) -> str:
    """Расшифровываем заголовки вида =?utf-8?b?...?="""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def _body_snippet(msg: email.message.EmailMessage, limit: int = 3000) -> str:
    """Начало текста письма (обычный текст, а если его нет — HTML без тегов)."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        if part is None:
            return ""
        content = part.get_content()
        if part.get_content_subtype() == "html":
            content = re.sub(r"<[^>]+>", " ", content)
            content = html.unescape(content)
        return re.sub(r"\s+", " ", content)[:limit]
    except Exception:
        return ""


def parse_mail(raw: bytes) -> Mail:
    msg = email.message_from_bytes(raw, policy=default_policy)
    name, addr = parseaddr(_decode(msg.get("From")))
    try:
        received = parsedate_to_datetime(msg.get("Date"))
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except Exception:
        received = datetime.now(timezone.utc)
    return Mail(
        sender_name=name or addr,
        sender_email=addr.lower(),
        subject=_decode(msg.get("Subject")) or "(без темы)",
        received=received,
        text=_body_snippet(msg),
    )


def fetch_mails(s: Settings, now: datetime) -> list[Mail]:
    """
    Забираем письма из «Входящих» за последние сутки.

    Безопасность: папка открывается в режиме ТОЛЬКО ЧТЕНИЕ (readonly=True),
    а письма читаются через BODY.PEEK — так они не помечаются прочитанными.
    Ничего не удаляем, не перемещаем, не отвечаем.
    """
    since = now - timedelta(hours=s.hours_back)
    # IMAP умеет искать только по дате (без времени), поэтому берём с запасом в день
    # и потом точно отфильтровываем по времени.
    since_str = (since - timedelta(days=1)).strftime("%d-%b-%Y")

    mails: list[Mail] = []
    with imaplib.IMAP4_SSL(s.imap_host, s.imap_port) as imap:
        imap.login(s.imap_user, s.imap_password)
        imap.select("INBOX", readonly=True)
        status, data = imap.search(None, "SINCE", since_str)
        if status != "OK":
            raise RuntimeError(f"Почта не ответила на поиск писем: {status}")
        ids = data[0].split()
        for msg_id in ids:
            status, parts = imap.fetch(msg_id, "(BODY.PEEK[])")
            if status != "OK":
                continue
            for part in parts:
                if isinstance(part, tuple):
                    mail = parse_mail(part[1])
                    if mail.received >= since:
                        mails.append(mail)
        imap.logout()
    mails.sort(key=lambda m: m.received, reverse=True)
    return mails


def is_important(mail: Mail, senders: list[str], keywords: list[str]) -> bool:
    """Важное письмо: от нужного отправителя или со «стоп-словом» в теме/тексте."""
    who = f"{mail.sender_email} {mail.sender_name}".lower()
    for sender in senders:
        # Можно указать целиком адрес (buh@firma.ru), домен (@bank.ru) или имя
        if sender in who:
            return True
    haystack = f"{mail.subject} {mail.text}".lower()
    return any(word in haystack for word in keywords)


def split_mails(mails: list[Mail], s: Settings) -> tuple[list[Mail], list[Mail]]:
    important, other = [], []
    for m in mails:
        (important if is_important(m, s.important_senders, s.keywords) else other).append(m)
    return important, other


# ---------------------------------------------------------------------------
# Календарь
# ---------------------------------------------------------------------------

@dataclass
class Meeting:
    title: str
    start: datetime | None  # None — событие на весь день
    end: datetime | None
    location: str = ""


def download_ics(url: str) -> bytes:
    # Ссылки webcal:// — это тот же https://
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    req = urllib.request.Request(url, headers={"User-Agent": "morning-digest/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def meetings_for_day(ics_data: bytes, day: date, tz: ZoneInfo) -> list[Meeting]:
    """Встречи на указанный день, включая повторяющиеся."""
    import icalendar
    import recurring_ical_events

    cal = icalendar.Calendar.from_ical(ics_data)
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)

    result: list[Meeting] = []
    for event in recurring_ical_events.of(cal).between(start, end):
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            continue
        title = str(event.get("SUMMARY", "")).strip() or "(без названия)"
        location = str(event.get("LOCATION", "")).strip()
        dtstart = event.get("DTSTART").dt
        dtend = event.get("DTEND").dt if event.get("DTEND") else None

        if isinstance(dtstart, datetime):
            if dtstart.tzinfo is None:
                dtstart = dtstart.replace(tzinfo=tz)
            dtstart = dtstart.astimezone(tz)
            if isinstance(dtend, datetime):
                dtend = (dtend if dtend.tzinfo else dtend.replace(tzinfo=tz)).astimezone(tz)
            else:
                dtend = None
            result.append(Meeting(title, dtstart, dtend, location))
        else:
            result.append(Meeting(title, None, None, location))  # весь день

    # Сначала события «на весь день», потом по времени
    result.sort(key=lambda m: (m.start is not None, m.start or start))
    return result


# ---------------------------------------------------------------------------
# Сообщение
# ---------------------------------------------------------------------------

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _short(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_message(today: date, meetings: list[Meeting] | None, important: list[Mail],
                  other_count: int, max_important: int = 15,
                  problems: list[str] | None = None, mail_ok: bool = True) -> str:
    """Собираем текст сводки (разметка HTML для Telegram)."""
    e = html.escape
    lines = [f"☀️ <b>Доброе утро! {today.day} {MONTHS[today.month - 1]}, {WEEKDAYS[today.weekday()]}</b>", ""]

    # --- встречи ---
    if meetings is not None:
        lines.append("📅 <b>Встречи сегодня</b>")
        if not meetings:
            lines.append("Встреч нет — свободный день.")
        for m in meetings:
            if m.start is None:
                when = "весь день"
            elif m.end:
                when = f"{m.start:%H:%M}–{m.end:%H:%M}"
            else:
                when = f"{m.start:%H:%M}"
            place = f" · {e(_short(m.location, 60))}" if m.location else ""
            lines.append(f"• {when} — {e(_short(m.title, 80))}{place}")
        lines.append("")

    # --- письма ---
    lines.append("✉️ <b>Важные письма</b>")
    if not mail_ok:
        lines.append("Почту проверить не удалось (подробности ниже).")
    elif not important:
        lines.append("Важных писем нет.")
    for m in important[:max_important]:
        lines.append(f"• <b>{e(_short(m.sender_name, 40))}</b>: {e(_short(m.subject, 90))}")
    hidden = len(important) - max_important
    if hidden > 0:
        lines.append(f"…и ещё {hidden} {_plural(hidden, 'важное', 'важных', 'важных')}")
    if other_count:
        lines.append(f"И ещё {other_count} {_plural(other_count, 'письмо', 'письма', 'писем')}.")

    if problems:
        lines.append("")
        lines.append("⚠️ <b>Не получилось:</b>")
        lines.extend(f"• {e(p)}" for p in problems)

    return "\n".join(lines).strip()


def split_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Длинное сообщение режем по строкам, чтобы влезло в лимит Telegram."""
    chunks, current = [], ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line[:limit]
        else:
            current = candidate[:limit]
    if current:
        chunks.append(current)
    return chunks


def send_telegram(token: str, chat_id: str, text: str) -> None:
    for chunk in split_for_telegram(text):
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            with urllib.request.urlopen(url, data=payload, timeout=30) as resp:
                answer = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            answer = json.loads(err.read() or b"{}")
        if not answer.get("ok"):
            hint = ""
            if "chat not found" in str(answer.get("description", "")):
                hint = " — сначала откройте своего бота в Telegram и нажмите «Старт»."
            raise RuntimeError(f"Telegram не принял сообщение: {answer.get('description')}{hint}")


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def check_settings(s: Settings, need_telegram: bool) -> list[str]:
    missing = []
    if not s.imap_host:
        missing.append("IMAP_HOST")
    if not s.imap_user:
        missing.append("IMAP_USER")
    if not s.imap_password:
        missing.append("IMAP_PASSWORD")
    if need_telegram and not s.bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if need_telegram and not s.chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    return missing


def run(dry_run: bool = False) -> str:
    s = load_settings()
    missing = check_settings(s, need_telegram=not dry_run)
    if missing:
        raise SystemExit("В .env не заполнено: " + ", ".join(missing))

    now = datetime.now(s.tz)
    problems: list[str] = []

    # Почта
    mail_ok = True
    try:
        mails = fetch_mails(s, now)
    except imaplib.IMAP4.error as err:
        mails, mail_ok = [], False
        problems.append(f"почта: не пускает ({err}). Нужен пароль приложения и включённый IMAP.")
    except Exception as err:
        mails, mail_ok = [], False
        problems.append(f"почта: {err}")
    important, other = split_mails(mails, s)

    # Календарь
    meetings: list[Meeting] | None = None
    if s.ics_urls:
        meetings = []
        for url in s.ics_urls:
            try:
                meetings.extend(meetings_for_day(download_ics(url), now.date(), s.tz))
            except Exception as err:
                problems.append(f"календарь: {err}")
        meetings.sort(key=lambda m: (m.start is not None, m.start or now))

    text = build_message(now.date(), meetings, important, len(other), s.max_important, problems, mail_ok)

    if dry_run:
        print(text)
    else:
        send_telegram(s.bot_token, s.chat_id, text)
        print(f"Сводка отправлена в Telegram ({now:%d.%m.%Y %H:%M}).")
    return text


def main() -> None:
    parser = argparse.ArgumentParser(description="Утренняя сводка в Telegram")
    parser.add_argument("--dry-run", action="store_true", help="показать сообщение, не отправлять")
    parser.add_argument("--test-telegram", action="store_true", help="отправить тестовое сообщение")
    args = parser.parse_args()

    # Чтобы русские буквы нормально печатались в консоли Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.test_telegram:
        s = load_settings()
        send_telegram(s.bot_token, s.chat_id, "👋 Бот утренней сводки на связи.")
        print("Тестовое сообщение отправлено — проверьте Telegram.")
        return

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
