"""
Проверка вариантов объявлений: длина полей и рискованные слова.

Запуск:
  python check_limits.py варианты.md --platform yandex
  python check_limits.py варианты.md --platform vk
  python check_limits.py варианты.md --platform telegram

Формат файла — как в варианты.пример.md:
  ## Вариант 1
  Гипотеза: ...
  Заголовок: ...
  Доп. заголовок: ...
  Текст: ...

ЛИМИТЫ МЕНЯЮТСЯ. Перед запуском сверьтесь со справкой площадки и,
если нужно, поправьте таблицу LIMITS ниже.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

# Поле → (макс. символов, макс. длина одного слова или None)
LIMITS = {
    # Яндекс Директ, текстово-графическое объявление (ЕПК), справка «Технические ограничения».
    # Узкие знаки препинания . , ! : ; " не входят в лимит, но их не больше 15 на поле.
    "yandex": {
        "Заголовок": (56, 22),
        "Доп. заголовок": (30, 22),
        "Текст": (81, 23),
    },
    # VK Реклама: точные лимиты зависят от формата объявления. Сверьте в кабинете.
    "vk": {
        "Заголовок": (40, None),
        "Текст": (90, None),
    },
    # Telegram Ads: текст объявления до 160 символов.
    "telegram": {
        "Текст": (160, None),
        "Кнопка": (30, None),
    },
}

YANDEX_NARROW = set('.,!:;"')
YANDEX_NARROW_MAX = 15

# Слова, за которые чаще всего прилетает от модерации и ФАС без доказательств
RISKY = [
    r"лучш\w*", r"№\s?1", r"номер\s+один", r"сам(ый|ая|ое|ые)\b", r"первы[йе]\s+в",
    r"гарант\w*", r"100\s?%", r"бесплатно\s+навсегда", r"без\s+риск\w*", r"уникальн\w*",
]


@dataclass
class Variant:
    title: str
    fields: dict[str, str] = field(default_factory=dict)


def parse(text: str) -> list[Variant]:
    variants: list[Variant] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("## "):
            variants.append(Variant(line[3:].strip()))
            continue
        if variants and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().strip("*_").strip()
            variants[-1].fields[key] = value.strip().lstrip("*_").strip()
    return variants


def length(value: str, platform: str) -> tuple[int, str]:
    """Длина по правилам площадки и пояснение."""
    if platform == "yandex":
        narrow = sum(ch in YANDEX_NARROW for ch in value)
        counted = len(value) - min(narrow, YANDEX_NARROW_MAX)
        note = f"знаков препинания {narrow}" + (" — больше 15!" if narrow > YANDEX_NARROW_MAX else "")
        return counted, note
    return len(value), ""


def check(variants: list[Variant], platform: str) -> tuple[list[str], int]:
    limits = LIMITS[platform]
    lines, errors = [], 0
    for v in variants:
        if not v.fields:
            continue  # например, раздел «Разбор» без полей
        lines.append(f"\n{v.title}")
        if "Гипотеза" in v.fields:
            lines.append(f"  гипотеза: {v.fields['Гипотеза']}")
        for name, (max_len, max_word) in limits.items():
            value = v.fields.get(name)
            if value is None:
                continue
            n, note = length(value, platform)
            status = "ок" if n <= max_len else f"ДЛИННЕЕ на {n - max_len}"
            problems = []
            if n > max_len:
                errors += 1
            if platform == "yandex" and note.endswith("!"):
                errors += 1
            if max_word:
                long_words = [w for w in re.findall(r"[\w-]+", value) if len(w) > max_word]
                if long_words:
                    errors += 1
                    problems.append(f"слишком длинные слова: {', '.join(long_words)}")
            extra = f" ({note})" if note else ""
            lines.append(f"  {name}: {n}/{max_len} — {status}{extra}")
            lines.extend(f"    ⚠ {p}" for p in problems)
        unknown = [k for k in v.fields if k not in limits and k != "Гипотеза"]
        if unknown:
            lines.append(f"  (не проверялись: {', '.join(unknown)} — у этой площадки таких полей нет в таблице)")
        risky = sorted({m.group(0) for value in v.fields.values() if value
                        for pattern in RISKY for m in re.finditer(pattern, value, re.IGNORECASE)})
        if risky:
            lines.append(f"  ⚠ рискованные слова: {', '.join(risky)} — нужны доказательства или замените")
    return lines, errors


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Проверка длины объявлений")
    parser.add_argument("file", help="файл с вариантами, например варианты.md")
    parser.add_argument("--platform", choices=sorted(LIMITS), required=True)
    args = parser.parse_args()

    with open(args.file, encoding="utf-8-sig") as f:
        variants = [v for v in parse(f.read()) if v.fields]
    if not variants:
        raise SystemExit("Не нашёл вариантов. Каждый вариант начинается со строки «## Вариант N».")

    lines, errors = check(variants, args.platform)
    print(f"Площадка: {args.platform}. Вариантов: {len(variants)}.")
    print("\n".join(lines))
    print("\nВсё влезает." if errors == 0 else f"\nНужно поправить: {errors}.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
