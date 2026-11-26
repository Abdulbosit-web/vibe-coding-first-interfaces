"""Расчёт свободных окон по графику. Без Telegram: только даты и время."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _t(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def free_slots(day: date, minutes: int, busy: list[tuple[datetime, datetime]],
               now: datetime, cfg) -> list[datetime]:
    """
    Свободные окна на день для услуги длительностью `minutes`.

    busy — уже занятые промежутки (начало, конец).
    Окно подходит, если услуга целиком помещается в рабочие часы,
    не задевает перерыв и чужие записи и начинается не раньше,
    чем через MIN_NOTICE_MINUTES от текущего момента.
    """
    tz = ZoneInfo(cfg.TIMEZONE)
    if day.isoformat() in cfg.DAYS_OFF:
        return []
    hours = cfg.WORK_HOURS.get(day.weekday())
    if not hours:
        return []

    day_start = datetime.combine(day, _t(hours[0]), tzinfo=tz)
    day_end = datetime.combine(day, _t(hours[1]), tzinfo=tz)
    breaks = [(datetime.combine(day, _t(a), tzinfo=tz), datetime.combine(day, _t(b), tzinfo=tz))
              for a, b in cfg.BREAKS]
    earliest = now + timedelta(minutes=cfg.MIN_NOTICE_MINUTES)
    length = timedelta(minutes=minutes)
    step = timedelta(minutes=cfg.SLOT_STEP_MINUTES)

    result = []
    start = day_start
    while start + length <= day_end:
        end = start + length
        if (start >= earliest
                and not any(_overlaps(start, end, b0, b1) for b0, b1 in breaks)
                and not any(_overlaps(start, end, b0, b1) for b0, b1 in busy)):
            result.append(start)
        start += step
    return result


def bookable_days(minutes: int, busy: list[tuple[datetime, datetime]],
                  now: datetime, cfg) -> list[date]:
    """Дни на ближайшие DAYS_AHEAD дней, в которых есть хотя бы одно окно."""
    today = now.astimezone(ZoneInfo(cfg.TIMEZONE)).date()
    days = (today + timedelta(days=i) for i in range(cfg.DAYS_AHEAD + 1))
    return [d for d in days if free_slots(d, minutes, busy, now, cfg)]
