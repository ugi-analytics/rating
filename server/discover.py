# -*- coding: utf-8 -*-
"""
Пошук моніторингових каналів у самому Telegram (від службового акаунта).
Запити — у server/discover_queries.txt. Результат — /var/www/rating/discover.json:
для кожного знайденого каналу: назва, підписники, дата останнього допису і скільки з 40
останніх дописів наш сервер розпізнав як попередження про загрози / як влучання.
Запускається один раз на кожну нову версію файлу запитів.
"""
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import rating_core as rc
import threats as TH

from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import SearchRequest


async def run(client, root: Path, data: Path, www: Path, known: set, log):
    qf = root / "server" / "discover_queries.txt"
    if not qf.exists():
        return
    body = qf.read_text(encoding="utf-8")
    ver = hashlib.md5(body.encode()).hexdigest()[:10]
    done = data / "discover_done"
    if done.exists() and done.read_text().strip() == ver:
        return
    queries = [l.strip() for l in body.splitlines() if l.strip() and not l.startswith("#")]
    log(f"Пошук каналів: {len(queries)} запитів")
    city_res = rc.compile_cities(True)
    cands = {}
    for i, q in enumerate(queries):
        try:
            res = await client(SearchRequest(q=q, limit=20))
            for ch in res.chats:
                if getattr(ch, "broadcast", False) and getattr(ch, "username", None):
                    c = cands.setdefault(ch.username.lower(), {"u": ch.username, "title": ch.title,
                                                                 "subs": getattr(ch, "participants_count", None),
                                                                 "q": [], "ent": ch})
                    c["q"].append(q)
        except FloodWaitError as e:
            log(f"! пошук: FloodWait {e.seconds} с")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log(f"! пошук «{q}»: {type(e).__name__}: {e}")
        await asyncio.sleep(12)
        if i % 10 == 9:
            _write(www, cands, queries, i + 1, partial=True)
    log(f"Знайдено кандидатів: {len(cands)}, перевіряю дописи…")
    now = datetime.now(timezone.utc)
    for n, (k, c) in enumerate(list(cands.items())):
        thr = hit = cnt = 0
        last = None
        sample = []
        try:
            async for m in client.iter_messages(c["ent"], limit=40):
                cnt += 1
                last = last or m.date
                t = m.message or ""
                if rc.classify(t, city_res):
                    hit += 1
                elif TH.detect(t, c["u"], city_res, m.date):
                    thr += 1
                    if len(sample) < 3:
                        sample.append(t[:140])
        except FloodWaitError as e:
            await asyncio.sleep(min(e.seconds + 5, 600))
        except Exception as e:
            c["err"] = f"{type(e).__name__}"
        c.update(n=cnt, thr=thr, hit=hit, known=k in known, sample=sample,
                 last=last.isoformat(timespec="minutes") if last else None,
                 age_h=round((now - last).total_seconds() / 3600, 1) if last else None)
        await asyncio.sleep(1.5)
        if n % 20 == 19:
            _write(www, cands, queries, len(queries), partial=True)
    _write(www, cands, queries, len(queries), partial=False)
    done.write_text(ver)
    log("Пошук каналів завершено")


def _write(www, cands, queries, qdone, partial):
    rows = [{k: v for k, v in c.items() if k != "ent"} for c in cands.values()]
    rows.sort(key=lambda r: (-(r.get("thr") or 0), -(r.get("subs") or 0)))
    out = {"updated": datetime.now().isoformat(timespec="seconds"), "partial": partial,
           "queries_done": qdone, "queries": len(queries), "channels": rows}
    tmp = www / "discover.json.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, www / "discover.json")
