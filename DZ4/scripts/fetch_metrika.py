#!/usr/bin/env python3
"""
Выгрузка просмотров статей из Яндекс.Метрики через Reporting API —
аналог отчёта «Содержание» (просмотры по URL по дням).

Запуск:

    pip install requests
    export METRIKA_TOKEN='твой_oauth_токен'      # Windows: set METRIKA_TOKEN=...
    export METRIKA_COUNTER='12345678'            # номер счётчика Метрики
    python fetch_metrika.py 2024-06-01 2025-05-31

Результат: файлы metrika_api/views-YYYY-MM.csv (колонки: date,url,pageviews) —
по одному на календарный месяц. 

"""
import csv, os, sys, time
import requests
from datetime import date

API = "https://api-metrika.yandex.net/stat/v1/data"
TOKEN = os.environ.get("METRIKA_TOKEN")
COUNTER = os.environ.get("METRIKA_COUNTER")
LIMIT = 100000                      # максимум строк на запрос

def fail(msg): sys.exit(f"[!] {msg}")

if not TOKEN:   fail("нет METRIKA_TOKEN в окружении")
if not COUNTER: fail("нет METRIKA_COUNTER в окружении")
if len(sys.argv) < 2:
    fail("укажи начальную дату: python fetch_metrika.py 2022-01-01 [2026-06-26]")
DATE1 = sys.argv[1]
DATE2 = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()
# месяцы без данных просто дадут пустой/маленький файл — это нормально

def fetch_page(date1, date2, offset):
    params = {
        "ids": COUNTER,
        "metrics": "ym:pv:pageviews",
        "dimensions": "ym:pv:date,ym:pv:URLPathFull",
        "filters": "ym:pv:URLPathFull=@'/news/'",   # =@ — содержит подстроку
        "date1": date1, "date2": date2,
        "accuracy": "full",                          # без семплирования
        "limit": LIMIT, "offset": offset,
        "sort": "ym:pv:date",
    }
    r = requests.get(API, params=params,
                     headers={"Authorization": f"OAuth {TOKEN}"}, timeout=120)
    if r.status_code != 200:
        fail(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()

def fetch_range(date1, date2):
    rows, offset = [], 1
    while True:
        j = fetch_page(date1, date2, offset)
        data = j.get("data", [])
        for d in data:
            day = d["dimensions"][0]["name"]
            url = d["dimensions"][1]["name"]
            pv = int(d["metrics"][0])
            rows.append((day, url, pv))
        if j.get("sampled"):
            print(f"    [внимание] семплирование: {j.get('sample_share')}")
        if len(data) < LIMIT:
            break
        offset += LIMIT
        time.sleep(0.3)
    return rows

# --- разбить диапазон по календарным месяцам и тянуть помесячно --------------
def month_iter(d1, d2):
    y, m = int(d1[:4]), int(d1[5:7])
    ey, em = int(d2[:4]), int(d2[5:7])
    while (y, m) <= (ey, em):
        first = date(y, m, 1)
        last = date(y + (m == 12), (m % 12) + 1, 1).toordinal() - 1
        yield first.isoformat(), date.fromordinal(last).isoformat(), f"{y:04d}-{m:02d}"
        y, m = (y + (m == 12), (m % 12) + 1)

os.makedirs("metrika_api", exist_ok=True)
print(f"[i] счётчик {COUNTER}, период {DATE1}..{DATE2}")
for d1, d2, tag in month_iter(DATE1, DATE2):
    rows = fetch_range(d1, d2)
    out = f"metrika_api/views-{tag}.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["date", "url", "pageviews"])
        w.writerows(rows)
    print(f"  {tag}: {len(rows):>6} строк → {out}")
print("\nГотово. Пришли мне папку metrika_api/ — смержу в датасет.")
