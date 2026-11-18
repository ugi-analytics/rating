# -*- coding: utf-8 -*-
"""
Цілодобовий збирач «Рейтинг влучань» для сервера (VPS).

* Тримає постійне з'єднання з Telegram: нові та відредаговані дописи з каналів
  приходять одразу (події NewMessage / MessageEdited) — затримка секунди.
* Службовий акаунт поступово підписується на канали з channels.txt
  (пауза між підписками, щоб не впертися в обмеження Telegram).
* Раз на 20 хв «дочитує» канали — підстраховка, якщо щось пропущено.
* Пише /var/www/rating/events.json (віддає Caddy по HTTPS) — формат як у хмарного збирача.

Налаштування: /etc/rating/env (TG_API_ID, TG_API_HASH), сесія — /etc/rating/session.
"""
import asyncio
import hashlib
import html
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import rating_core as rc                      # noqa: E402
import threats as TH                          # noqa: E402
from collector import KYIV, process_message, read_channels, load_json   # noqa: E402

from telethon import TelegramClient, events, utils                     # noqa: E402
from telethon.errors import FloodWaitError, ChannelsTooMuchError       # noqa: E402
from telethon.sessions import StringSession                            # noqa: E402
from telethon.tl.functions.channels import JoinChannelRequest          # noqa: E402

DATA = Path(os.environ.get("RATING_DATA", "/var/lib/rating"))
WWW = Path(os.environ.get("RATING_WWW", "/var/www/rating"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "100"))
BOOT_DAYS = int(os.environ.get("BOOTSTRAP_DAYS", "35"))
RECHECK = 15
CATCHUP_EVERY = 20 * 60
JOIN_PAUSE = (40, 90)          # секунд між підписками на канали

city_res = rc.compile_cities(True)
by_msg = {}                    # (канал, id) -> [події]
state = {"last_id": {}}
dirty = True
failed = set()
peer2ch = {}                   # peer_id -> ім'я каналу
channels = []
THREAT_ONLY = set()          # канали лише для попереджень (без рейтингу), у нижньому регістрі
ENV = {}
bot = None                     # клієнт Telegram-бота сповіщень
notify_q = None              # черга нових подій для бота
BOT = {"name": None, "err": None, "vapid": None}
THREATS = []                 # попередження «що куди летить» за останні 24 год
THREAT_SEEN = set()          # (канал, id) уже розібраних дописів
subs = {}                      # chat_id -> {"cities": [...], "all": bool}
NOTIFY_MAX_AGE = 90 * 60       # не сповіщати про дописи, старші за 90 хв (наприклад, під час дочитування)


def log(*a):
    print(datetime.now(KYIV).strftime("%d.%m %H:%M:%S"), *a, flush=True)


def load():
    global state
    state = load_json(DATA / "state.json", {"last_id": {}})
    seed = load_json(DATA / "events.json", None) or load_json(WWW / "events.json", {})
    for e in seed.get("events", []):
        by_msg.setdefault((e["ch"], e["id"]), []).append(e)
    THREATS.extend(seed.get("threats", []))
    for d in THREATS:
        THREAT_SEEN.add((d["ch"], d["id"]))
    log(f"Завантажено подій: {sum(len(v) for v in by_msg.values())}, загроз: {len(THREATS)}")


def upsert(m, ch):
    global dirty
    new = [] if ch.lower() in THREAT_ONLY else process_message(m, ch, city_res)
    old = by_msg.get((ch, m.id))
    if new or old:
        if new != old:
            dirty = True
            if new:
                log(f"+ {ch}/{m.id}: {', '.join(e['c'] for e in new)}")
        by_msg[(ch, m.id)] = new
        added = {e["c"] for e in new} - {e["c"] for e in (old or [])}
        if added and notify_q is not None and notify_q.qsize() < 500 and (time.time() - m.date.timestamp()) < NOTIFY_MAX_AGE:
            for e in new:
                if e["c"] in added:
                    notify_q.put_nowait(e)
    # попередження про загрози — лише для свіжих дописів (до 30 хв), кожен допис один раз
    age = time.time() - m.date.timestamp()
    if age < 30 * 60 and (ch, m.id) not in THREAT_SEEN:
        THREAT_SEEN.add((ch, m.id))
        found = TH.detect(m.message or "", ch, city_res, m.date)
        if found:
            t = m.date.astimezone(KYIV).isoformat(timespec="seconds")
            for d in found:
                d.update(t=t, ch=ch, id=m.id, type="threat")
                THREATS.append(d)
                if notify_q is not None and notify_q.qsize() < 500:
                    notify_q.put_nowait(d)
            dirty = True
            log(f"⚠ {ch}/{m.id}: " + ", ".join(f"{d['c'] or d['r']} ({d['kn']})" for d in found))
    if m.id > state["last_id"].get(ch, 0):
        state["last_id"][ch] = m.id


def write():
    global dirty
    now = datetime.now(KYIV)
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat(timespec="minutes")
    for k in [k for k, v in by_msg.items() if not v or v[0]["t"] < cutoff]:
        del by_msg[k]
    ev = [e for lst in by_msg.values() for e in lst]
    ev.sort(key=lambda e: (e["t"], e["ch"], e["id"]), reverse=True)
    tcut = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    THREATS[:] = [d for d in THREATS if d["t"] >= tcut]
    thr = sorted(({k: v for k, v in d.items() if k != "type"} for d in THREATS), key=lambda d: d["t"], reverse=True)
    body = json.dumps([ev, thr], ensure_ascii=False, separators=(",", ":"))
    out = {
        "updated": now.isoformat(timespec="seconds"),
        "rev": hashlib.md5(body.encode()).hexdigest()[:12],
        "live": True,
        "bot": BOT["name"],
        "bot_error": BOT["err"],
        "vapid": BOT["vapid"],
        "push_api": "/login/push/",
        "channels": len(channels) - len(THREAT_ONLY),
        "threat_channels": len(THREAT_ONLY),
        "channels_failed": sorted(failed),
        "channels_errors": {c: ERRS.get(c, "") for c in sorted(failed)},
        "keep_days": KEEP_DAYS,
        "cities": {n: {"lat": la, "lon": lo, "border": n in rc.BORDER_CITIES, "r": rc.CITY_REGION.get(n, "")} for n, (la, lo) in rc.COORDS.items()},
        "events": ev,
        "threats": thr,
    }
    txt = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    for d in (DATA, WWW):
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "events.json.tmp"
        tmp.write_text(txt, encoding="utf-8")
        os.replace(tmp, d / "events.json")
    # крихітний файл-«маячок»: додаток спершу перевіряє його і качає events.json лише після змін
    tmp = WWW / "rev.json.tmp"
    tmp.write_text(json.dumps({"rev": out["rev"], "updated": out["updated"]}), encoding="utf-8")
    os.replace(tmp, WWW / "rev.json")
    (DATA / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    dirty = False


async def writer():
    last = 0
    while True:
        await asyncio.sleep(5)
        if dirty or time.time() - last > 60:      # раз на хвилину — «пульс», щоб додаток бачив, що сервер живий
            write()
            last = time.time()


ERRS = {}                      # канал -> причина, чому не знайдено
ENT = {}                       # канал -> InputPeerChannel (кеш, щоб не шукати канали за назвою щоразу)
RESOLVED = asyncio.Event()


def _peers_load():
    try:
        return json.loads((DATA / "peers.json").read_text())
    except Exception:
        return {}


def _peers_save(p):
    tmp = DATA / "peers.json.tmp"
    tmp.write_text(json.dumps(p))
    os.replace(tmp, DATA / "peers.json")


async def resolve_and_join(client):
    """1) знаходить канали (з кешу або за назвою); 2) поступово підписується на нові."""
    from telethon.tl.types import InputPeerChannel
    peers = _peers_load()
    to_join = []
    for ch in channels:
        rec = peers.get(ch.lower())
        try:
            if rec and rec.get("id"):
                ip = InputPeerChannel(rec["id"], rec["hash"])
            elif rec and time.time() - rec.get("miss", 0) < (86400 if ch.lower() in THREAT_ONLY else 3600):
                failed.add(ch)                 # нещодавно не знайдено — не шукаємо повторно
                ERRS[ch] = rec.get("err", "")
                continue
            else:
                ip = await client.get_input_entity(ch)
                if not hasattr(ip, "channel_id"):
                    raise ValueError("це не канал, а особистий акаунт чи бот")
                peers[ch.lower()] = {"id": ip.channel_id, "hash": ip.access_hash}
                _peers_save(peers)
                await asyncio.sleep(2)
            ENT[ch] = ip
            peer2ch[utils.get_peer_id(ip)] = ch
            failed.discard(ch)
            if not (rec and rec.get("joined")):
                to_join.append(ch)
        except FloodWaitError as e:
            log(f"! FloodWait {e.seconds} с під час пошуку каналів")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            failed.add(ch)
            peers[ch.lower()] = {"miss": time.time(), "err": f"{type(e).__name__}: {e}"[:160]}
            ERRS[ch] = peers[ch.lower()]["err"]
            _peers_save(peers)
            log(f"! {ch}: {type(e).__name__}: {e}")
    RESOLVED.set()
    log(f"Каналів знайдено: {len(ENT)}/{len(channels)}, до перевірки підписки: {len(to_join)}")
    for ch in to_join:
        try:
            ent = await client.get_entity(ENT[ch])
            if getattr(ent, "left", False):
                try:
                    await client(JoinChannelRequest(ENT[ch]))
                    log(f"Підписано: {ch}")
                except ChannelsTooMuchError:
                    log("! Досягнуто ліміту підписок акаунта (≈500)")
                    return
                await asyncio.sleep(random.uniform(*JOIN_PAUSE))
            else:
                await asyncio.sleep(1)
            peers[ch.lower()]["joined"] = True
            _peers_save(peers)
        except FloodWaitError as e:
            log(f"! FloodWait {e.seconds} с під час підписки")
            await asyncio.sleep(e.seconds + 5)
        except Exception as e:
            log(f"! підписка {ch}: {type(e).__name__}: {e}")


async def catch_up(client):
    boot_from = datetime.now(KYIV) - timedelta(days=BOOT_DAYS)
    for ch in channels:
        if ch not in ENT:
            continue
        last = state["last_id"].get(ch)
        seen = 0
        try:
            async for m in client.iter_messages(ENT[ch]):
                if last is None and (m.date < boot_from or (ch.lower() in THREAT_ONLY and seen >= 20)):
                    break
                if last is not None and m.id <= last and seen >= RECHECK:
                    break
                seen += 1
                upsert(m, ch)
            state["last_id"].setdefault(ch, 0)
        except FloodWaitError as e:
            log(f"! {ch}: FloodWait {e.seconds} с")
            await asyncio.sleep(min(e.seconds, 300))
        except Exception as e:
            failed.add(ch)
            log(f"! {ch}: {type(e).__name__}: {e}")
        await asyncio.sleep(0.7)


async def catch_up_loop(client):
    await RESOLVED.wait()
    while True:
        t = time.time()
        await catch_up(client)
        log(f"Дочитування завершено за {time.time() - t:.0f} с")
        await asyncio.sleep(CATCHUP_EVERY)


async def main():
    global channels
    env = {}
    for line in Path("/etc/rating/env").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    session = Path("/etc/rating/session").read_text().strip()
    channels = read_channels(ROOT / "channels.txt")
    for ch in read_channels(ROOT / "channels_threat.txt") if (ROOT / "channels_threat.txt").exists() else []:
        if ch.lower() not in {c.lower() for c in channels}:
            channels.append(ch)
            THREAT_ONLY.add(ch.lower())
    load()

    client = TelegramClient(StringSession(session), int(env["TG_API_ID"]), env["TG_API_HASH"],
                            device_model="Rating live server", system_version="Linux",
                            app_version="1.0", flood_sleep_threshold=120)
    await client.connect()
    if not await client.is_user_authorized():
        log("!!! Сесія Telegram недійсна. Запустіть: sudo rating-login")
        sys.exit(3)

    @client.on(events.NewMessage())
    @client.on(events.MessageEdited())
    async def on_msg(ev):
        ch = peer2ch.get(ev.chat_id)
        if ch:
            upsert(ev.message, ch)

    global notify_q
    from bot import Notifier
    notifier = Notifier(DATA, int(env["TG_API_ID"]), env["TG_API_HASH"], log)
    notify_q = notifier.q
    try:
        from push import Push
        notifier.push = await asyncio.to_thread(Push, DATA, log)
        BOT["vapid"] = notifier.push.public
    except Exception as e:
        log(f"! Push не запустився: {type(e).__name__}: {e}")
    notifier.run_sender()
    try:
        if not notifier.token_f.exists() and not (DATA / "botfather_tried").exists():
            (DATA / "botfather_tried").write_text(datetime.now(KYIV).isoformat())
            await notifier.create_via_botfather(client)
        if await notifier.start():
            BOT["name"] = notifier.me.username
            asyncio.create_task(notifier.rebrand(client))
    except Exception as e:
        BOT["err"] = f"{type(e).__name__}: {e}"[:300]
        log(f"! Бот не запустився: {BOT['err']}")
    try:
        await asyncio.start_server(notifier.handle_http, "127.0.0.1", 8099)
    except OSError as e:
        log(f"! Сторінка налаштування бота недоступна: {e}")

    log(f"Старт. Каналів у списку: {len(channels)}")
    await client.catch_up()
    tasks = [asyncio.create_task(writer()), asyncio.create_task(resolve_and_join(client)),
             asyncio.create_task(catch_up_loop(client))]

    async def _discover():
        await RESOLVED.wait()
        await asyncio.sleep(60)
        try:
            import discover
            await discover.run(client, ROOT, DATA, WWW, {c.lower() for c in channels}, log)
        except Exception as e:
            log(f"! пошук каналів: {type(e).__name__}: {e}")
    tasks.append(asyncio.create_task(_discover()))
    try:
        await client.run_until_disconnected()
    finally:
        for t in tasks:
            t.cancel()
        write()


if __name__ == "__main__":
    asyncio.run(main())
