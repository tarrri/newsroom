#!/usr/bin/env python3
"""
Генератор списка НОВЫХ url для докачки телами (под scrape_bodies.py).

Логика: берём из sitemap.xml статьи, чьи article_id попадают в окно
[FLOOR_ID, CEIL_ID), и выкидываем те, что уже скачаны (есть в news_bodies.jsonl).
Результат — news_scrape_list.csv с колонкой url. Скрапер потом докачает только их.

Окно по id = окно по времени, т.к. id растут последовательно (~250–370 / месяц).
Текущий датасет начинается с id 56938 (декабрь 2025). Чтобы добрать НОЯБРЬ —
ставим CEIL_ID=56938, FLOOR_ID≈56570. Для более старых месяцев опускаем FLOOR_ID
НО только если за эти месяцы есть выгрузка Метрики в 4target/ (иначе нечем метить).
"""
import csv
import json
import os
import re

SITEMAP = "sitemap.xml"
BODIES = "news_bodies.jsonl"
OUT = "news_scrape_list.csv"

CEIL_ID = 56938      # не трогаем декабрь и новее (уже собрано)
FLOOR_ID = 56570     # нижняя граница: ноябрь 2025. Опусти для более старых месяцев.

def article_id(url):
    m = re.search(r"-(\d+)/?$", url)
    return int(m.group(1)) if m else None

# 1. sitemap → id -> путь
id2path = {}
txt = open(SITEMAP, encoding="utf-8").read()
for loc in re.findall(r"<loc>(.*?)</loc>", txt):
    if "/news/" not in loc:
        continue
    aid = article_id(loc)
    if aid is None:
        continue
    path = loc.split("nts-tv.com", 1)[-1]      # храним как /news/...-id/
    id2path[aid] = path

# 2. уже скачанные id (из news_bodies.jsonl)
done = set()
if os.path.exists(BODIES):
    for line in open(BODIES, encoding="utf-8"):
        try:
            r = json.loads(line)
            if r.get("ok") and r.get("body"):
                aid = article_id(r["url"])
                if aid:
                    done.add(aid)
        except Exception:
            pass

# 3. кандидаты: в окне и ещё не скачаны
cand = sorted(aid for aid in id2path
              if FLOOR_ID <= aid < CEIL_ID and aid not in done)

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["url"])
    for aid in cand:
        w.writerow([id2path[aid]])

print(f"окно id [{FLOOR_ID}, {CEIL_ID}) | уже скачано в окне: "
      f"{sum(1 for a in done if FLOOR_ID<=a<CEIL_ID)}")
print(f"НОВЫХ к докачке: {len(cand)} → {OUT}")
if cand:
    print(f"id-диапазон новых: {cand[0]}–{cand[-1]}")
