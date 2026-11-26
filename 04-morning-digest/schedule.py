"""
Включить / выключить ежедневный запуск сводки.

  python schedule.py on            — запускать каждый день в 8:00
  python schedule.py on --time 7:30 — в другое время
  python schedule.py off           — выключить
  python schedule.py status        — проверить, включено ли

Как это устроено:
  • Windows — «Планировщик заданий» (задача MorningDigest);
  • macOS   — launchd (файл ~/Library/LaunchAgents/ru.morning-digest.plist);
              если Mac спал в 8:00, сводка придёт сразу после пробуждения;
  • Linux   — cron (строка с пометкой # morning-digest).
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "digest.py"
LOG = HERE / "digest.log"
PYTHON = sys.executable  # тот же Python, которым вы запускаете этот файл

TASK_NAME = "MorningDigest"
MAC_LABEL = "ru.morning-digest"
MAC_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"
CRON_MARK = "# morning-digest"


def parse_time(value: str) -> tuple[int, int]:
    hour, minute = value.replace(".", ":").split(":")
    hour, minute = int(hour), int(minute)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise SystemExit("Время в формате ЧЧ:ММ, например 8:00")
    return hour, minute


# --- Windows -----------------------------------------------------------------

def win_on(hour: int, minute: int) -> None:
    command = f'"{PYTHON}" "{SCRIPT}"'
    subprocess.run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", TASK_NAME,
                    "/ST", f"{hour:02d}:{minute:02d}", "/TR", command], check=True)


def win_off() -> None:
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME], check=False)


def win_status() -> bool:
    return subprocess.run(["schtasks", "/Query", "/TN", TASK_NAME],
                          capture_output=True).returncode == 0


# --- macOS -------------------------------------------------------------------

def mac_on(hour: int, minute: int) -> None:
    MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": MAC_LABEL,
        "ProgramArguments": [PYTHON, str(SCRIPT)],
        "WorkingDirectory": str(HERE),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
    }
    mac_off(quiet=True)
    with open(MAC_PLIST, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "load", str(MAC_PLIST)], check=True)


def mac_off(quiet: bool = False) -> None:
    if MAC_PLIST.exists():
        subprocess.run(["launchctl", "unload", str(MAC_PLIST)], capture_output=quiet)
        MAC_PLIST.unlink()


def mac_status() -> bool:
    return MAC_PLIST.exists()


# --- Linux (cron) ------------------------------------------------------------

def _cron_lines() -> list[str]:
    import shutil
    if not shutil.which("crontab"):
        raise SystemExit("Не найдена программа cron. Установите её: sudo apt install cron")
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return result.stdout.splitlines() if result.returncode == 0 else []


def _cron_write(lines: list[str]) -> None:
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def linux_on(hour: int, minute: int) -> None:
    lines = [l for l in _cron_lines() if CRON_MARK not in l]
    lines.append(f'{minute} {hour} * * * cd "{HERE}" && "{PYTHON}" "{SCRIPT}" >> "{LOG}" 2>&1 {CRON_MARK}')
    _cron_write(lines)


def linux_off() -> None:
    _cron_write([l for l in _cron_lines() if CRON_MARK not in l])


def linux_status() -> bool:
    return any(CRON_MARK in l for l in _cron_lines())


# -----------------------------------------------------------------------------

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Расписание утренней сводки")
    parser.add_argument("action", choices=["on", "off", "status"])
    parser.add_argument("--time", default="8:00", help="время запуска, по умолчанию 8:00")
    args = parser.parse_args()

    system = platform.system()
    handlers = {
        "Windows": (win_on, win_off, win_status),
        "Darwin": (mac_on, mac_off, mac_status),
        "Linux": (linux_on, linux_off, linux_status),
    }
    if system not in handlers:
        raise SystemExit(f"Не знаю, как настроить расписание в {system}")
    on, off, status = handlers[system]

    if args.action == "on":
        hour, minute = parse_time(args.time)
        on(hour, minute)
        print(f"Готово: сводка будет приходить каждый день в {hour:02d}:{minute:02d}.")
        print("Компьютер в это время должен быть включён. Выключить: python schedule.py off")
    elif args.action == "off":
        off()
        print("Ежедневный запуск выключен.")
    else:
        print("Включено." if status() else "Выключено.")


if __name__ == "__main__":
    main()
