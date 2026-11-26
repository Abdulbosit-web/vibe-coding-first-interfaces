"""
Собирает одностраничный сайт index.html из файла данные.txt и фото из папки фото.

Запуск:  python build.py
Потом откройте index.html двойным щелчком.

Сайт содержит только то, что написано в данные.txt: ничего не выдумывает.
Если какого-то блока в данных нет, его не будет и на сайте.
"""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "данные.txt"
PHOTOS = HERE / "фото"
OUT = HERE / "index.html"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".avif"}


# ---------------------------------------------------------------------------
# Чтение данные.txt
# ---------------------------------------------------------------------------

def parse(text: str) -> dict:
    """
    Формат:
        [основное]           ← раздел
        название: Кофейня    ← «ключ: значение»
        [меню]
        Капучино | 250 ₽ | описание   ← строки списка через «|»
    Строки, начинающиеся с #, — комментарии.
    """
    data: dict = {}
    section = None
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[(.+)\]", line)
        if m:
            section = m.group(1).strip().lower()
            data.setdefault(section, [])
            continue
        if section is None:
            raise SystemExit(f"Строка {line_no}: сначала укажите раздел, например [основное]")
        data[section].append(line)
    return data


def key_values(lines: list[str]) -> dict[str, str]:
    result = {}
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            result[key.strip().lower()] = value.strip()
    return result


def pairs(lines: list[str]) -> list[tuple[str, str]]:
    """Для часов работы: «Пн–Пт: 8:00–21:00» → ('Пн–Пт', '8:00–21:00')."""
    out = []
    for line in lines:
        if ":" in line:
            k, v = line.split(":", 1)
            out.append((k.strip(), v.strip()))
    return out


def rows(lines: list[str]) -> list[list[str]]:
    return [[cell.strip() for cell in line.split("|")] for line in lines]


# ---------------------------------------------------------------------------
# Сборка страницы
# ---------------------------------------------------------------------------

def tel_link(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("8") and len(digits) == 11:
        digits = "+7" + digits[1:]
    return "tel:" + digits


def telegram_link(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        return value
    return "https://t.me/" + value.lstrip("@")


def find_photos() -> list[str]:
    if not PHOTOS.is_dir():
        return []
    files = sorted(p for p in PHOTOS.iterdir() if p.suffix.lower() in IMAGE_EXT)
    return [f"{PHOTOS.name}/{p.name}" for p in files]


def build(data: dict, photos: list[str]) -> str:
    e = html.escape
    main = key_values(data.get("основное", []))
    name = main.get("название")
    if not name:
        raise SystemExit("В разделе [основное] нет строки «название: …»")

    kind = main.get("тип", "")
    city = main.get("город", "")
    tagline = main.get("фраза", "")
    phone = main.get("телефон", "")
    telegram = main.get("telegram", "")
    address = main.get("адрес", "")
    map_url = main.get("ссылка на карты", "")
    accent = main.get("цвет", "#7a5236")
    review_label = main.get("подпись отзыва", "отзыв с Яндекс Карт")
    menu_title = main.get("заголовок меню", "Меню и цены")
    demo = main.get("демо", "").lower() in {"да", "yes", "true", "1"}

    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", accent):
        accent = "#7a5236"

    hours = pairs(data.get("часы", []))
    menu = rows(data.get("меню", []) or data.get("услуги", []))
    reviews = rows(data.get("отзывы", []))
    hero_photo, gallery = (photos[0], photos[1:7]) if photos else (None, [])

    # --- кнопки ---
    buttons = []
    if phone:
        buttons.append(f'<a class="btn btn-main" href="{e(tel_link(phone))}">Позвонить</a>')
    if telegram:
        buttons.append(f'<a class="btn btn-ghost" href="{e(telegram_link(telegram))}" '
                       f'target="_blank" rel="noopener">Написать в Telegram</a>')

    sections = []

    # --- меню / услуги ---
    if menu:
        items = []
        for row in menu:
            title = e(row[0])
            price = e(row[1]) if len(row) > 1 else ""
            note = f'<p class="item-note">{e(row[2])}</p>' if len(row) > 2 and row[2] else ""
            items.append(f'<li><div class="item-line"><span class="item-name">{title}</span>'
                         f'<span class="dots" aria-hidden="true"></span>'
                         f'<span class="item-price">{price}</span></div>{note}</li>')
        sections.append(f'<section id="menu" class="block"><h2>{e(menu_title)}</h2>'
                        f'<ul class="menu">{"".join(items)}</ul></section>')

    # --- фото ---
    if gallery:
        imgs = "".join(f'<img src="{e(p)}" alt="{e(name)}" loading="lazy">' for p in gallery)
        sections.append(f'<section class="block"><h2>Как у нас</h2><div class="gallery">{imgs}</div></section>')

    # --- отзывы ---
    if reviews:
        cards = []
        for row in reviews:
            author = e(row[0])
            text = e(row[1]) if len(row) > 1 else ""
            cards.append(f'<figure class="review"><blockquote>«{text}»</blockquote>'
                         f'<figcaption>{author} · <span>{e(review_label)}</span></figcaption></figure>')
        sections.append(f'<section id="reviews" class="block"><h2>Отзывы</h2>'
                        f'<div class="reviews">{"".join(cards)}</div></section>')

    # --- часы и адрес ---
    if hours or address:
        hours_html = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in hours)
        route = (f'<a class="btn btn-main" href="{e(map_url)}" target="_blank" rel="noopener">'
                 f'Как добраться</a>') if map_url else ""
        addr = f'<p class="address">{e(address)}</p>' if address else ""
        tbl = f'<table class="hours">{hours_html}</table>' if hours else ""
        sections.append(f'<section id="contacts" class="block contacts"><h2>Часы работы и адрес</h2>'
                        f'{tbl}{addr}<div class="actions">{route}{"".join(buttons)}</div></section>')

    # --- нижняя панель на телефоне ---
    bar = []
    if phone:
        bar.append(f'<a href="{e(tel_link(phone))}">📞 Позвонить</a>')
    if map_url:
        bar.append(f'<a href="{e(map_url)}" target="_blank" rel="noopener">📍 Маршрут</a>')
    if telegram:
        bar.append(f'<a href="{e(telegram_link(telegram))}" target="_blank" rel="noopener">✈️ Telegram</a>')
    mobile_bar = f'<nav class="mobile-bar">{"".join(bar)}</nav>' if bar else ""

    # --- разметка для поисковиков (только из ваших данных) ---
    schema = {"@context": "https://schema.org", "@type": "LocalBusiness", "name": name}
    if address:
        schema["address"] = address
    if phone:
        schema["telephone"] = phone
    if hours:
        schema["openingHours"] = [f"{k} {v}" for k, v in hours]
    if map_url:
        schema["hasMap"] = map_url
    schema_json = json.dumps(schema, ensure_ascii=False).replace("<", "\\u003c")

    description = tagline or f"{kind} {name}".strip()
    hero_style = f' style="--hero: url(\'{e(hero_photo)}\')"' if hero_photo else ""
    hero_class = "hero has-photo" if hero_photo else "hero"
    eyebrow = " · ".join(x for x in (kind, city) if x)
    demo_banner = ('<div class="demo">Демо-шаблон: все данные на этой странице вымышлены. '
                   'Замените их в данные.txt и запустите build.py.</div>') if demo else ""
    title = f"{name} — {kind}, {city}" if kind and city else name

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(description)}">
<meta property="og:title" content="{e(name)}">
<meta property="og:description" content="{e(description)}">
{f'<meta property="og:image" content="{e(hero_photo)}">' if hero_photo else ''}
<meta name="theme-color" content="{accent}">
<script type="application/ld+json">{schema_json}</script>
<style>
  :root {{
    --accent: {accent};
    --ink: #2b2622;
    --muted: #6f665e;
    --paper: #faf7f2;
    --card: #ffffff;
    --line: #e6dfd5;
    --radius: 14px;
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0; background: var(--paper); color: var(--ink);
    font: 17px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    padding-bottom: 72px;
  }}
  h1, h2 {{ font-family: Georgia, "Times New Roman", serif; font-weight: 600; line-height: 1.2; }}
  a {{ color: var(--accent); }}
  .demo {{ background: #fff3c4; color: #5c4a00; font-size: 14px; text-align: center; padding: 8px 16px; }}

  .hero {{
    min-height: 56vh; display: flex; align-items: flex-end;
    padding: 48px 20px; background: var(--accent); color: #fff;
  }}
  .hero.has-photo {{
    min-height: 78vh;
    background: linear-gradient(to top, rgba(20,16,12,.78), rgba(20,16,12,.15) 65%), var(--hero) center/cover;
  }}
  .hero-inner {{ max-width: 760px; margin: 0 auto; width: 100%; }}
  .eyebrow {{ text-transform: uppercase; letter-spacing: .12em; font-size: 13px; opacity: .85; margin: 0 0 8px; }}
  .hero h1 {{ font-size: clamp(36px, 9vw, 64px); margin: 0 0 12px; }}
  .tagline {{ font-size: clamp(18px, 4.5vw, 22px); margin: 0 0 28px; max-width: 34ch; opacity: .95; }}

  .actions {{ display: flex; flex-wrap: wrap; gap: 12px; }}
  .btn {{
    display: inline-flex; align-items: center; justify-content: center;
    min-height: 50px; padding: 0 24px; border-radius: 999px;
    font-weight: 600; text-decoration: none; transition: transform .15s ease, opacity .15s ease;
  }}
  .btn:active {{ transform: scale(.97); }}
  .btn-main {{ background: #fff; color: var(--accent); }}
  .btn-ghost {{ border: 1.5px solid currentColor; color: inherit; }}
  .contacts .btn-main {{ background: var(--accent); color: #fff; }}
  .contacts .btn-ghost {{ color: var(--accent); }}

  main {{ max-width: 760px; margin: 0 auto; padding: 0 20px; }}
  .block {{ padding: 56px 0 8px; }}
  .block h2 {{ font-size: clamp(26px, 6vw, 34px); margin: 0 0 24px; }}

  .menu {{ list-style: none; margin: 0; padding: 0; }}
  .menu li {{ padding: 12px 0; border-bottom: 1px solid var(--line); }}
  .item-line {{ display: flex; align-items: baseline; gap: 8px; }}
  .item-name {{ font-weight: 600; }}
  .dots {{ flex: 1; border-bottom: 1.5px dotted #c9bfb2; transform: translateY(-4px); min-width: 16px; }}
  .item-price {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
  .item-note {{ margin: 2px 0 0; color: var(--muted); font-size: 15px; }}

  .gallery {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; }}
  .gallery img {{ width: 100%; aspect-ratio: 1; object-fit: cover; border-radius: var(--radius); }}

  .reviews {{ display: grid; gap: 14px; }}
  .review {{ margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); padding: 20px; }}
  .review blockquote {{ margin: 0 0 10px; }}
  .review figcaption {{ font-weight: 600; font-size: 15px; }}
  .review figcaption span {{ font-weight: 400; color: var(--muted); }}

  .hours {{ border-collapse: collapse; margin-bottom: 16px; }}
  .hours th {{ text-align: left; font-weight: 600; padding: 4px 24px 4px 0; }}
  .hours td {{ font-variant-numeric: tabular-nums; }}
  .address {{ font-size: 18px; margin: 0 0 24px; }}

  footer {{ max-width: 760px; margin: 56px auto 0; padding: 24px 20px; color: var(--muted); font-size: 14px; border-top: 1px solid var(--line); }}

  .mobile-bar {{
    position: fixed; left: 0; right: 0; bottom: 0; display: flex;
    background: rgba(255,255,255,.96); border-top: 1px solid var(--line);
    backdrop-filter: blur(8px); padding-bottom: env(safe-area-inset-bottom);
  }}
  .mobile-bar a {{ flex: 1; text-align: center; padding: 14px 4px; font-weight: 600; font-size: 15px; text-decoration: none; color: var(--ink); }}
  .mobile-bar a + a {{ border-left: 1px solid var(--line); }}

  @media (min-width: 720px) {{
    body {{ padding-bottom: 0; }}
    .mobile-bar {{ display: none; }}
    .gallery {{ grid-template-columns: repeat(3, 1fr); }}
    .reviews {{ grid-template-columns: repeat(2, 1fr); }}
    .hero {{ padding: 72px 20px; }}
  }}
  @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} .btn {{ transition: none; }} }}
</style>
</head>
<body>
{demo_banner}
<header class="{hero_class}"{hero_style}>
  <div class="hero-inner">
    {f'<p class="eyebrow">{e(eyebrow)}</p>' if eyebrow else ''}
    <h1>{e(name)}</h1>
    {f'<p class="tagline">{e(tagline)}</p>' if tagline else ''}
    <div class="actions">{"".join(buttons)}</div>
  </div>
</header>
<main>
{"".join(sections)}
</main>
<footer>{e(name)}{f" · {e(address)}" if address else ""}</footer>
{mobile_bar}
</body>
</html>
"""


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not DATA.exists():
        raise SystemExit("Нет файла данные.txt рядом с build.py")
    page = build(parse(DATA.read_text(encoding="utf-8-sig")), find_photos())
    OUT.write_text(page, encoding="utf-8")
    print(f"Готово: {OUT.name}. Откройте его в браузере.")


if __name__ == "__main__":
    main()
