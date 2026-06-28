#!/usr/bin/env python3
"""
Пересборка датасета залетаемости из:
  - metrika_api/views-*.csv   — просмотры по статьям (метка),
  - news_bodies.jsonl         — тела статей и метаданные (признаки),
  - news_tags.jsonl           — LLM-разметка (опционально, для размеченного подмножества).

Результат: news_dataset_v2.csv с 3-классовым таргетом (низкая/средняя/высокая)
внутри месяца публикации.
"""
import glob, json, re, sys
import numpy as np, pandas as pd

OUTAGE = {"2025-03", "2025-04", "2025-05"}          # отвалился счётчик, нет данных за период
ROLLUP = r"(?:Основные события недели|Итоги (?:года|недели|дня|месяца))"

def aid_of(u):
    m = re.search(r"-(\d+)/?$", str(u))
    return int(m.group(1)) if m else None

# --- 1. Просмотры из Метрики: сумма по статье за всё время --------------------
pv_parts = []
for f in glob.glob("metrika_api/views-*.csv"):
    d = pd.read_csv(f)
    if len(d) == 0:
        continue
    d["aid"] = d["url"].map(aid_of)
    pv_parts.append(d.dropna(subset=["aid"])[["aid", "pageviews"]])
pv = pd.concat(pv_parts).groupby("aid")["pageviews"].sum()
pv.index = pv.index.astype(int)
print(f"[i] статей с просмотрами в Метрике: {len(pv)}")

# --- 2. Тексты статей ----------------------------------------------------------
rows = []
for line in open("news_bodies.jsonl", encoding="utf-8"):
    try:
        r = json.loads(line)
    except Exception:
        continue
    if not (r.get("ok") and r.get("body")):
        continue
    aid = aid_of(r["url"])
    if aid is None:
        continue
    rows.append({
        "article_id": aid, "url": r["url"], "title": r.get("title", ""),
        "date_published": r.get("date_published", ""), "lead": r.get("lead", ""),
        "has_video": int(bool(r.get("has_video"))),
        "tags_str": "|".join(r.get("tags", []) or []),
        "body": r.get("body", ""), "body_len": r.get("body_len", 0),
    })
df = pd.DataFrame(rows).drop_duplicates("article_id", keep="last")
print(f"[i] тел статей с текстом: {len(df)}")

# --- 3. pub_month из спарсенной даты публикации (ground truth) ---------------
dt = pd.to_datetime(df["date_published"], errors="coerce", utc=True)
df["pub_month"] = dt.dt.strftime("%Y-%m")
miss = df["pub_month"].isna().sum()
if miss:
    print(f"[!] без даты публикации: {miss} (исключаем)")
df = df.dropna(subset=["pub_month"])

# --- 4. Просмотры + фильтры --------------------------------------------------
df["views"] = df["article_id"].map(pv).fillna(0).astype(int)
df["is_rollup"] = df["title"].str.contains(ROLLUP, na=False, regex=True)
before = len(df)
df = df[~df["pub_month"].isin(OUTAGE)]                       # отвалился счётчик
df = df[~df["is_rollup"]]                                    # «итоги» — не инфоповоды
df = df[df["views"] > 0]                                     # без просмотров метку не построить
print(f"[i] после фильтров (отвал/итоги/0-просмотров): {len(df)} (убрано {before-len(df)})")

# --- 5. 3-классовый таргет внутри месяца ------------------------------------
# низкая <50% | средняя 50–80% | высокая = топ-20% по просмотрам месяца
df["y3"] = 0
for mo, g in df.groupby("pub_month"):
    q = g["views"].rank(pct=True, method="first")
    df.loc[q[q >= 0.50].index, "y3"] = 1
    df.loc[q[q >= 0.80].index, "y3"] = 2
df["y_high"] = (df["y3"] == 2).astype(int)

# --- 6. Подмешать LLM-разметку, где есть ------------------------------------
FLAGS = ["federal_hook","vip_person","affects_daily_life","money_tariffs",
         "achievement","unusual","negative"]
tags = {}
try:
    for line in open("news_tags.jsonl", encoding="utf-8"):
        t = json.loads(line); tags[t["id"]] = t
except FileNotFoundError:
    pass
df["event_type"] = df["article_id"].map(lambda a: tags.get(a, {}).get("event_type"))
for fl in FLAGS:
    df[fl] = df["article_id"].map(lambda a: 1 if fl in tags.get(a, {}).get("flags", []) else 0)
print(f"[i] статей с LLM-разметкой: {df['event_type'].notna().sum()} / {len(df)}")

# --- 7. Сохранить -----------------------------------------------------------
df = df.sort_values(["pub_month", "views"], ascending=[True, False]).reset_index(drop=True)
df.to_csv("news_dataset_v2.csv", index=False)
df.to_csv("news_dataset_v2_excel.csv", index=False, encoding="utf-8-sig")
print(f"\n[OK] news_dataset_v2.csv — {len(df)} статей, "
      f"{df['pub_month'].nunique()} месяцев ({df['pub_month'].min()}…{df['pub_month'].max()})")
print("классы:", df["y3"].value_counts().sort_index().to_dict(),
      "| высокая доля:", round(df["y_high"].mean(), 3))
print("статей по месяцам (мин/медиана/макс):",
      df.groupby("pub_month").size().agg(["min","median","max"]).to_dict())
