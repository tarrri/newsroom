#!/usr/bin/env python3
"""
Сбор текстов и метаданных статей НТС по url из news_target_clean.csv.

Запуск (на машине с доступом к nts-tv.com):
    pip install requests cloudscraper beautifulsoup4 trafilatura
    python scrape_bodies.py

Результат: news_bodies.jsonl — по строке на статью:
    url, http_status, ok, article_id, title, date_published, date_modified,
    tags (list), lead, has_video, body, body_len, fetched_at
"""

import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

BASE = "https://nts-tv.com"
# входной список url можно передать аргументом: python scrape_bodies.py news_scrape_list.csv
IN_CSV = sys.argv[1] if len(sys.argv) > 1 else "news_target_clean.csv"
OUT_JSONL = "news_bodies.jsonl"

MIN_DELAY, MAX_DELAY = 1.5, 4.0      # пауза между запросами, сек
MAX_RETRIES = 4                       # ретраи на один url
CONSEC_BLOCK_LIMIT = 8                # стоп после стольких блокировок подряд

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


# --- HTTP session: cloudscraper если есть, иначе requests --------------------
def make_session():
    try:
        import cloudscraper
        s = cloudscraper.create_scraper(
            browser={"browser": "chrome", "platform": "windows", "mobile": False})
        s.headers.update(HEADERS)
        print("[i] cloudscraper — обход ddos-guard включён")
        return s
    except Exception:
        import requests
        s = requests.Session()
        s.headers.update(HEADERS)
        print("[i] cloudscraper не найден — обычный requests. "
              "Если пойдут блокировки: pip install cloudscraper")
        return s


# --- парсинг страницы НТС ----------------------------------------------------
def parse_article(content_bytes, url):
    from bs4 import BeautifulSoup
    head = content_bytes[:3000].decode("latin-1", "ignore").lower()
    m = re.search(r'charset=["\']?([\w-]+)', head)
    enc = m.group(1) if m else "utf-8"
    try:
        soup = BeautifulSoup(content_bytes, "html.parser", from_encoding=enc)
    except Exception:
        soup = BeautifulSoup(content_bytes, "html.parser")

    post = soup.select_one("#post") or soup

    h1 = post.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""

    bd = soup.select_one('div.text[itemprop="articleBody"]')
    if bd:
        body = "\n".join(p.get_text(" ", strip=True)
                         for p in bd.find_all("p") if p.get_text(strip=True))
    else:
        body = ""  # fallback ниже

    # fallback на trafilatura, если точный селектор не сработал
    if not body:
        try:
            import trafilatura
            txt = trafilatura.extract(content_bytes, url=url,
                                      include_comments=False,
                                      favor_precision=True)
            body = (txt or "").strip()
        except Exception:
            pass

    dp = soup.select_one('[itemprop="datePublished"]')
    date_pub = dp.get_text(strip=True) if dp else ""
    dm = soup.select_one('[itemprop="dateModified"]')
    date_mod = dm.get_text(strip=True) if dm else ""

    tags = [a.get_text(strip=True) for a in soup.select("div.hashtag a")]

    desc = soup.find("meta", attrs={"name": "description"})
    lead = desc.get("content", "").strip() if desc else ""

    has_video = bool(soup.select_one("div.youtube iframe, div.text iframe"))
    aid = post.get("data-id", "") if hasattr(post, "get") else ""

    return {
        "article_id": aid,
        "title": title,
        "date_published": date_pub,
        "date_modified": date_mod,
        "tags": tags,
        "lead": lead,
        "has_video": has_video,
        "body": body,
        "body_len": len(body),
    }


def is_blocked(resp):
    # ВНИМАНИЕ: server=ddos-guard стоит на ВСЕХ ответах (это прокси перед сайтом),
    # поэтому по заголовку блок не определяем.
    # Блок = коды 403/429/5xx ИЛИ крошечная челлендж-страница (~585 байт) на 200.
    # 404/410 (статья удалена) — это НЕ блок: помечаем как нет-тела и едем дальше,
    # иначе скрипт падает на серии удалённых статей (частое в старом архиве).
    if resp.status_code in (403, 429, 500, 502, 503, 504):
        return True
    if resp.status_code == 200 and len(resp.content) < 2000:
        return True
    return False


def fetch(session, url):
    delay = 2.0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=25)
            if is_blocked(r):
                if attempt < MAX_RETRIES:
                    time.sleep(delay); delay *= 2; continue
                return r.status_code, None, True            # blocked
            return r.status_code, r.content, False          # bytes!
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(delay); delay *= 2; continue
            print(f"    ! сеть: {e}")
            return 0, None, False
    return 0, None, True


# --- main --------------------------------------------------------------------
def main():
    if not os.path.exists(IN_CSV):
        sys.exit(f"Не найден {IN_CSV} рядом со скриптом.")

    done = set()
    if os.path.exists(OUT_JSONL):
        with open(OUT_JSONL, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    # пропускаем при повторном запуске: уже с текстом ИЛИ удалённые (404/410)
                    if (r.get("ok") and r.get("body")) or r.get("http_status") in (404, 410):
                        done.add(r["url"])
                except Exception:
                    pass
        print(f"[i] уже с текстом: {len(done)} — продолжаю")

    urls = []
    with open(IN_CSV, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            urls.append(row["url"])
    todo = [u for u in urls if u not in done]
    print(f"[i] всего url: {len(urls)} | осталось: {len(todo)}")

    session = make_session()
    consec_block = 0
    ok_cnt = 0
    print("[i] старт сбора (первый запрос может занять до ~30 с — "
          "cloudscraper решает JS-челлендж)...", flush=True)

    with open(OUT_JSONL, "a", encoding="utf-8") as out:
        for i, path in enumerate(todo, 1):
            full = BASE + path if path.startswith("/") else path
            status, content, blocked = fetch(session, full)

            # парсим только успешный ответ; 404/прочее → пустое тело (без мусора)
            parsed = (parse_article(content, full)
                      if (content and status == 200) else {"body": "", "body_len": 0})
            rec = {
                "url": path,
                "http_status": status,
                "ok": bool(parsed.get("body")),
                **parsed,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

            if blocked:
                consec_block += 1
                print(f"  {i}/{len(todo)} | ⛔ блок http={status} "
                      f"(подряд {consec_block})", flush=True)
            else:
                consec_block = 0
                if rec["ok"]:
                    ok_cnt += 1
                # первые 5 — поштучно, дальше каждые 10
                if i <= 5 or i % 10 == 0 or i == len(todo):
                    print(f"  {i}/{len(todo)} | с текстом: {ok_cnt} | "
                          f"http={status} len={rec.get('body_len', 0)}", flush=True)

            if consec_block >= CONSEC_BLOCK_LIMIT:
                print(f"[!] {consec_block} блокировок подряд — стоп. "
                      f"Запусти скрипт снова позже, он продолжит с этого места.")
                break

            time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

    print(f"\nГотово в этом запуске → {OUT_JSONL}")
    print("Осталось недокачанное? Просто запусти скрипт ещё раз.")


if __name__ == "__main__":
    main()
