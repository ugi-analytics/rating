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
    from telethon.tl.types import InputPeerChannel
    sf = data / "discover_state.json"
    st = {}
    try:
        st = json.loads(sf.read_text())
    except Exception:
        pass
    if st.get("ver") != ver:
        st = {"ver": ver, "qdone": 0, "cands": {}}
    cands = st["cands"]
    for c in cands.values():                # відновлення після перезапуску сервера
        c["ent"] = InputPeerChannel(c["id"], c["hash"])

    def save():
        tmp = sf.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ver": ver, "qdone": st["qdone"],
                                   "cands": {k: {a: b for a, b in v.items() if a != "ent"} for k, v in cands.items()}}, ensure_ascii=False))
        os.replace(tmp, sf)
    log(f"Пошук каналів: {len(queries)} запитів, продовжую з {st['qdone']}")
    city_res = rc.compile_cities(True)
    for i, q in enumerate(queries):
        if i < st["qdone"]:
            continue
        try:
            res = await client(SearchRequest(q=q, limit=20))
            for ch in res.chats:
                if getattr(ch, "broadcast", False) and getattr(ch, "username", None):
                    c = cands.setdefault(ch.username.lower(), {"u": ch.username, "title": ch.title,
                                                                 "subs": getattr(ch, "participants_count", None),
                                                                 "q": [], "id": ch.id, "hash": ch.access_hash,
                                                                 "ent": InputPeerChannel(ch.id, ch.access_hash)})
                    c["q"].append(q)
        except FloodWaitError as e:
            log(f"! пошук: FloodWait {e.seconds} с")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log(f"! пошук «{q}»: {type(e).__name__}: {e}")
        await asyncio.sleep(12)
        st["qdone"] = i + 1
        save()
        if i % 10 == 9:
            _write(www, cands, queries, i + 1, partial=True)
    log(f"Знайдено кандидатів: {len(cands)}, перевіряю дописи…")
    now = datetime.now(timezone.utc)
    for n, (k, c) in enumerate(list(cands.items())):
        if "n" in c:                         # уже перевірений до перезапуску
            continue
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
        if n % 10 == 9:
            save()
        if n % 20 == 19:
            _write(www, cands, queries, len(queries), partial=True)
    save()
    _write(www, cands, queries, len(queries), partial=False)
    done.write_text(ver)
    log("Пошук каналів завершено")


def _write(www, cands, queries, qdone, partial):
    rows = [{k: v for k, v in c.items() if k not in ("ent", "hash")} for c in cands.values()]
    rows.sort(key=lambda r: (-(r.get("thr") or 0), -(r.get("subs") or 0)))
    out = {"updated": datetime.now().isoformat(timespec="seconds"), "partial": partial,
           "queries_done": qdone, "queries": len(queries), "channels": rows}
    tmp = www / "discover.json.tmp"
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, www / "discover.json")
