"""
Хранение записей в файле zapisi.csv.

Формат подобран под Excel: разделитель «;» и кодировка с BOM,
чтобы русские буквы открывались без «кракозябр».
"""

from __future__ import annotations

import csv
import os
import threading
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timedelta

FIELDS_RU = {
    "id": "Номер",
    "start": "Начало",
    "service": "Услуга",
    "minutes": "Минут",
    "price": "Цена",
    "client_name": "Имя",
    "phone": "Телефон",
    "user_id": "Telegram ID",
    "status": "Статус",
    "created": "Создана",
    "reminded": "Напомнили",
}

ACTIVE = "активна"
CANCELLED = "отменена"


@dataclass
class Booking:
    id: str
    start: datetime
    service: str
    minutes: int
    price: int
    client_name: str
    phone: str
    user_id: int
    status: str
    created: datetime
    reminded: bool = False

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=self.minutes)


class SlotTaken(Exception):
    """Пока клиент выбирал, это время кто-то занял."""


class Storage:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    # --- чтение и запись файла ---

    def load(self) -> list[Booking]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            by_ru = {v: k for k, v in FIELDS_RU.items()}
            result = []
            for row in reader:
                row = {by_ru.get(k, k): v for k, v in row.items()}
                result.append(Booking(
                    id=row["id"],
                    start=datetime.fromisoformat(row["start"]),
                    service=row["service"],
                    minutes=int(row["minutes"]),
                    price=int(row["price"] or 0),
                    client_name=row["client_name"],
                    phone=row["phone"],
                    user_id=int(row["user_id"]),
                    status=row["status"],
                    created=datetime.fromisoformat(row["created"]),
                    reminded=row["reminded"] == "да",
                ))
            return result

    def _save(self, bookings: list[Booking]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(FIELDS_RU[fl.name] for fl in fields(Booking))
            for b in sorted(bookings, key=lambda x: x.start):
                row = asdict(b)
                row["start"] = b.start.isoformat(timespec="minutes")
                row["created"] = b.created.isoformat(timespec="minutes")
                row["reminded"] = "да" if b.reminded else "нет"
                writer.writerow(row[fl.name] for fl in fields(Booking))
        os.replace(tmp, self.path)  # подмена целиком: файл не испортится при сбое

    # --- операции ---

    def busy(self) -> list[tuple[datetime, datetime]]:
        return [(b.start, b.end) for b in self.load() if b.status == ACTIVE]

    def add(self, start: datetime, service: dict, client_name: str, phone: str,
            user_id: int, now: datetime) -> Booking:
        with self._lock:
            bookings = self.load()
            end = start + timedelta(minutes=service["minutes"])
            for b in bookings:
                if b.status == ACTIVE and start < b.end and b.start < end:
                    raise SlotTaken()
            booking = Booking(
                id=uuid.uuid4().hex[:6], start=start, service=service["name"],
                minutes=service["minutes"], price=service["price"],
                client_name=client_name, phone=phone, user_id=user_id,
                status=ACTIVE, created=now,
            )
            bookings.append(booking)
            self._save(bookings)
            return booking

    def _update(self, booking_id: str, **changes) -> Booking | None:
        with self._lock:
            bookings = self.load()
            for b in bookings:
                if b.id == booking_id:
                    for key, value in changes.items():
                        setattr(b, key, value)
                    self._save(bookings)
                    return b
            return None

    def get(self, booking_id: str) -> Booking | None:
        return next((b for b in self.load() if b.id == booking_id), None)

    def cancel(self, booking_id: str) -> Booking | None:
        return self._update(booking_id, status=CANCELLED)

    def mark_reminded(self, booking_id: str) -> None:
        self._update(booking_id, reminded=True)

    def between(self, start: datetime, end: datetime) -> list[Booking]:
        return sorted((b for b in self.load()
                       if b.status == ACTIVE and start <= b.start < end),
                      key=lambda b: b.start)

    def upcoming_for_user(self, user_id: int, now: datetime) -> list[Booking]:
        return sorted((b for b in self.load()
                       if b.status == ACTIVE and b.user_id == user_id and b.start > now),
                      key=lambda b: b.start)

    def need_reminder(self, now: datetime, hours: int) -> list[Booking]:
        """
        Кому пора напомнить: визит в ближайшие `hours` часов, ещё не напоминали,
        и запись сделана заранее (кто записался за 3 часа, тому напоминать незачем).
        """
        window = timedelta(hours=hours)
        return [b for b in self.load()
                if b.status == ACTIVE and not b.reminded
                and now < b.start <= now + window
                and b.start - b.created > window]
