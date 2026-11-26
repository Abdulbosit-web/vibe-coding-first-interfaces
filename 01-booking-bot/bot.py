"""
Telegram-бот для записи клиентов.

Что умеет:
  • клиент выбирает услугу → день → свободное время → оставляет имя и телефон;
  • вам приходит уведомление о каждой новой записи и отмене;
  • /zapisi — ваши записи на сегодня и завтра (только для вас);
  • /moi — клиент видит свои записи и может отменить;
  • за день до визита бот напоминает клиенту, отменить можно кнопкой.

Запуск: python bot.py  (токен и ваш ID — в файле .env)
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          ConversationHandler, MessageHandler, filters)

import config
from slots import bookable_days, free_slots
from storage import SlotTaken, Storage

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)
TZ = ZoneInfo(config.TIMEZONE)

storage = Storage(os.path.join(HERE, "zapisi.csv"))

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)  # не печатать каждый запрос (там токен)
log = logging.getLogger("bot")

# Шаги разговора
CHOOSE_SERVICE, CHOOSE_DAY, CHOOSE_TIME, ASK_NAME, ASK_PHONE = range(5)

WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]


def now() -> datetime:
    return datetime.now(TZ)


def fmt_day(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}, {WEEKDAYS_SHORT[d.weekday()]}"


def fmt_booking(b, with_client: bool = False) -> str:
    e = html.escape
    text = f"<b>{fmt_day(b.start.date())}, {b.start:%H:%M}</b> — {e(b.service)} ({b.price} ₽)"
    if with_client:
        text += f"\n   {e(b.client_name)}, {e(b.phone)}"
    return text


def services_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{s['name']} — {s['price']} ₽, {s['minutes']} мин",
                                  callback_data=f"svc:{i}")]
            for i, s in enumerate(config.SERVICES)]
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Запись: услуга → день → время → имя → телефон
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    name = html.escape(update.effective_user.first_name or "")
    await update.effective_message.reply_text(
        f"Здравствуйте, {name}! Это бот записи в «{html.escape(config.BUSINESS_NAME)}».\n\n"
        "Выберите услугу:",
        reply_markup=services_keyboard(), parse_mode=ParseMode.HTML,
    )
    return CHOOSE_SERVICE


async def choose_service(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    index = int(query.data.split(":")[1])
    service = config.SERVICES[index]
    context.user_data["service"] = index

    days = bookable_days(service["minutes"], storage.busy(), now(), config)
    if not days:
        await query.edit_message_text(
            "К сожалению, свободных окон на ближайшие дни нет. Попробуйте позже: /start")
        return ConversationHandler.END

    buttons = [InlineKeyboardButton(fmt_day(d), callback_data=f"day:{d.isoformat()}") for d in days]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]  # по две кнопки в ряд
    rows.append([InlineKeyboardButton("← Другая услуга", callback_data="back:service")])
    await query.edit_message_text(
        f"<b>{html.escape(service['name'])}</b>\nВыберите день:",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
    return CHOOSE_DAY


async def show_times(query, context: ContextTypes.DEFAULT_TYPE, day: date) -> int:
    service = config.SERVICES[context.user_data["service"]]
    times = free_slots(day, service["minutes"], storage.busy(), now(), config)
    if not times:
        await query.edit_message_text("На этот день окна уже заняли. Выберите другой: /start")
        return ConversationHandler.END
    buttons = [InlineKeyboardButton(f"{t:%H:%M}", callback_data=f"time:{t:%H:%M}") for t in times]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton("← Другой день", callback_data=f"svc:{context.user_data['service']}")])
    await query.edit_message_text(
        f"<b>{html.escape(service['name'])}</b>, {fmt_day(day)}\nСвободное время:",
        reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)
    return CHOOSE_TIME


async def choose_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    day = date.fromisoformat(query.data.split(":")[1])
    context.user_data["day"] = day.isoformat()
    return await show_times(query, context, day)


async def choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    hh, mm = query.data.split(":")[1:]
    day = date.fromisoformat(context.user_data["day"])
    start_at = datetime(day.year, day.month, day.day, int(hh), int(mm), tzinfo=TZ)
    service = config.SERVICES[context.user_data["service"]]
    if start_at not in free_slots(day, service["minutes"], storage.busy(), now(), config):
        await query.answer("Это время уже занято", show_alert=True)
        return await show_times(query, context, day)
    await query.answer()
    context.user_data["start"] = start_at.isoformat()
    await query.edit_message_text(f"Отлично, {fmt_day(day)} в {start_at:%H:%M}.")
    await query.message.reply_text("Как вас зовут?")
    return ASK_NAME


async def back_to_services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Выберите услугу:", reply_markup=services_keyboard())
    return CHOOSE_SERVICE


async def ask_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()[:60]
    if len(name) < 2:
        await update.message.reply_text("Напишите, пожалуйста, имя.")
        return ASK_NAME
    context.user_data["name"] = name
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Ваш телефон? Нажмите кнопку ниже или напишите номер.", reply_markup=keyboard)
    return ASK_PHONE


def clean_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text)
    if not 10 <= len(digits) <= 15:
        return None
    if len(digits) == 11 and digits[0] == "8":  # 8 900 ... → +7 900 ...
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    return "+" + digits


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    raw = msg.contact.phone_number if msg.contact else (msg.text or "")
    phone = clean_phone(raw)
    if not phone:
        await msg.reply_text("Не похоже на номер. Например: +7 900 123-45-67")
        return ASK_PHONE

    data = context.user_data
    service = config.SERVICES[data["service"]]
    start_at = datetime.fromisoformat(data["start"])
    try:
        booking = storage.add(start_at, service, data["name"], phone, update.effective_user.id, now())
    except SlotTaken:
        await msg.reply_text("Ой, это время только что заняли. Выберите другое: /start",
                             reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END

    await msg.reply_text(
        "✅ Вы записаны!\n\n" + fmt_booking(booking) +
        "\n\nЗа день до визита я напомню. Посмотреть или отменить запись: /moi",
        reply_markup=ReplyKeyboardRemove(), parse_mode=ParseMode.HTML)
    await notify_admin(context, "🆕 <b>Новая запись</b>\n" + fmt_booking(booking, with_client=True))
    data.clear()
    return ConversationHandler.END


async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Хорошо, запись не оформлена. Начать заново: /start", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Мои записи и отмена
# ---------------------------------------------------------------------------

async def my_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = storage.upcoming_for_user(update.effective_user.id, now())
    if not items:
        await update.message.reply_text("У вас нет предстоящих записей. Записаться: /start")
        return
    for b in items:
        await update.message.reply_text(
            fmt_booking(b), parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                "Отменить запись", callback_data=f"cancel:{b.id}")]]))


async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    booking_id = query.data.split(":")[1]
    booking = storage.get(booking_id)
    user_id = update.effective_user.id
    if not booking or (booking.user_id != user_id and user_id != ADMIN_ID):
        await query.answer("Запись не найдена", show_alert=True)
        return
    if booking.status != "активна":
        await query.answer("Эта запись уже отменена")
        return
    storage.cancel(booking_id)
    await query.answer("Запись отменена")
    await query.edit_message_text("❌ Отменено: " + fmt_booking(booking) +
                                  "\nЗаписаться снова: /start", parse_mode=ParseMode.HTML)
    who = "клиент" if user_id == booking.user_id else "вы"
    await notify_admin(context, f"❌ <b>Отмена</b> ({who})\n" + fmt_booking(booking, with_client=True))


# ---------------------------------------------------------------------------
# Для владельца
# ---------------------------------------------------------------------------

async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    if not ADMIN_ID:
        log.warning("ADMIN_ID не задан в .env — уведомление никуда не отправлено")
        return
    try:
        await context.bot.send_message(ADMIN_ID, text, parse_mode=ParseMode.HTML)
    except Exception as err:
        log.error("Не удалось написать владельцу: %s. Нажмите «Старт» в своём боте.", err)


async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Эта команда только для владельца.")
        return
    today = now().date()
    parts = []
    for label, day in (("Сегодня", today), ("Завтра", today + timedelta(days=1))):
        start = datetime.combine(day, datetime.min.time(), tzinfo=TZ)
        items = storage.between(start, start + timedelta(days=1))
        parts.append(f"<b>{label}, {fmt_day(day)}</b>")
        parts.extend(f"• {b.start:%H:%M} — {html.escape(b.service)}\n   "
                     f"{html.escape(b.client_name)}, {html.escape(b.phone)}" for b in items)
        if not items:
            parts.append("записей нет")
        parts.append("")
    await update.message.reply_text("\n".join(parts).strip(), parse_mode=ParseMode.HTML)


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Подсказка: так владелец узнаёт свой Telegram ID."""
    await update.message.reply_text(f"Ваш Telegram ID: <code>{update.effective_user.id}</code>",
                                    parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Напоминания (проверка каждые 10 минут)
# ---------------------------------------------------------------------------

async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    for b in storage.need_reminder(now(), config.REMINDER_HOURS):
        try:
            await context.bot.send_message(
                b.user_id,
                "⏰ Напоминаем о записи:\n" + fmt_booking(b) + "\n\nЕсли не получается прийти, отмените:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                    "Отменить запись", callback_data=f"cancel:{b.id}")]]))
        except Exception as err:
            log.warning("Не удалось напомнить %s: %s", b.client_name, err)
        storage.mark_reminded(b.id)  # не повторяем, даже если клиент заблокировал бота


# ---------------------------------------------------------------------------

def main() -> None:
    if not TOKEN:
        raise SystemExit("Нет токена. Скопируйте .env.example в .env и вставьте TELEGRAM_BOT_TOKEN.")
    if not ADMIN_ID:
        log.warning("ADMIN_ID не задан: уведомления о записях приходить не будут. "
                    "Узнайте ID командой /id в боте и впишите в .env")

    app = Application.builder().token(TOKEN).build()

    booking_flow = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            CHOOSE_SERVICE: [CallbackQueryHandler(choose_service, pattern=r"^svc:")],
            CHOOSE_DAY: [CallbackQueryHandler(choose_day, pattern=r"^day:"),
                         CallbackQueryHandler(back_to_services, pattern=r"^back:service$")],
            CHOOSE_TIME: [CallbackQueryHandler(choose_time, pattern=r"^time:"),
                          CallbackQueryHandler(choose_service, pattern=r"^svc:")],
            ASK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_name)],
            ASK_PHONE: [MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.CONTACT, ask_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel_dialog), CommandHandler("start", start)],
        conversation_timeout=timedelta(minutes=30),
    )
    app.add_handler(booking_flow)
    app.add_handler(CommandHandler("moi", my_bookings))
    app.add_handler(CommandHandler("zapisi", admin_list))
    app.add_handler(CommandHandler("id", whoami))
    app.add_handler(CallbackQueryHandler(cancel_booking, pattern=r"^cancel:"))

    app.job_queue.run_repeating(send_reminders, interval=600, first=10)

    log.info("Бот запущен. Остановить: Ctrl+C")
    app.run_polling()


if __name__ == "__main__":
    main()
