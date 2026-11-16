# -*- coding: utf-8 -*-
"""
Хмарний збирач «Рейтинг влучань» (GitHub Actions).

Кожен запуск:
  * дочитує нові повідомлення з каналів (channels.txt) — лише після останнього обробленого id;
  * перечитує 15 останніх повідомлень кожного каналу (ОВА часто редагують цифри постраждалих);
  * зберігає події з точним часом у <data>/events.json, стан — у <data>/state.json.

Змінні середовища (секрети GitHub):
  TG_API_ID, TG_API_HASH  — ключі my.telegram.org
  TG_SESSION              — рядок окремої сесії Telegram для хмари (StringSession)
Необов'язково:
  BOOTSTRAP_DAYS (типово 35) — на скільки днів назад читати канал під час першого запуску
  KEEP_DAYS (типово 100)     — скільки днів зберігати події
"""
import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import rating_core as rc

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:
    KYIV = rc.KYIV
RECHECK = 15


def read_channels(path: Path):
    chans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        ch = re.sub(r"^(https?://)?t\.me/", "", line.split()[0]).lstrip("@").strip("/")
        if ch and ch not in chans:
            chans.append(ch)
    return chans


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def process_message(m, ch, city_res):
    """Повідомлення -> список подій (по одній на місто)."""
    text = m.message or ""
    cities = rc.classify(text, city_res)
    reg = rc.CHANNEL_REGION.get(ch.lower())
    if reg:
        cities = [c for c in cities if rc.CITY_REGION.get(c) == reg]
    t = m.date.astimezone(KYIV).isoformat(timespec="minutes")
    out = []
    for c in cities:
        k, i = rc.casualties_for_city(text, city_res[c], len(cities))
        out.append({"t": t, "c": c, "ch": ch, "id": m.id, "k": k, "i": i, "x": text[:600]})
    return out


async def run(data_dir: Path):
    from telethon import TelegramClient
    from telethon.errors import FloodWaitError
    from telethon.sessions import StringSession

    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    session = os.environ["TG_SESSION"]
    boot_days = int(os.environ.get("BOOTSTRAP_DAYS", "35"))
    keep_days = int(os.environ.get("KEEP_DAYS", "100"))

    data_dir.mkdir(parents=True, exist_ok=True)
    state = load_json(data_dir / "state.json", {"last_id": {}})
    events = load_json(data_dir / "events.json", {}).get("events", [])
    by_msg = {}
    for e in events:
        by_msg.setdefault((e["ch"], e["id"]), []).append(e)

    channels = read_channels(Path(__file__).with_name("channels.txt"))
    city_res = rc.compile_cities(True)
    now = datetime.now(KYIV)
    boot_from = now - timedelta(days=boot_days)
    stats = {"channels_ok": 0, "channels_failed": [], "new_msgs": 0}

    client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        print("::error::Сесія Telegram недійсна — створіть новий TG_SESSION")
        sys.exit(2)
    try:
        for ch in channels:
            last = state["last_id"].get(ch)
            seen, max_id = 0, last or 0
            try:
                async for m in client.iter_messages(ch):
                    if last is None and m.date < boot_from:
                        break
                    if last is not None and m.id <= last and seen >= RECHECK:
                        break
                    seen += 1
                    if last is None or m.id > last:
                        stats["new_msgs"] += 1
                    max_id = max(max_id, m.id)
                    by_msg[(ch, m.id)] = process_message(m, ch, city_res)   # upsert (і редагування)
                state["last_id"][ch] = max_id
                stats["channels_ok"] += 1
            except FloodWaitError as e:
                print(f"! {ch}: FloodWait {e.seconds} с — пропущено цього разу")
                stats["channels_failed"].append(ch)
                if e.seconds <= 30:
                    await asyncio.sleep(e.seconds)
            except Exception as e:
                print(f"! {ch}: {type(e).__name__}: {e}")
                stats["channels_failed"].append(ch)
    finally:
        await client.disconnect()

    cutoff = (now - timedelta(days=keep_days)).isoformat(timespec="minutes")
    all_ev = [e for lst in by_msg.values() for e in lst if e["t"] >= cutoff]
    all_ev.sort(key=lambda e: (e["t"], e["ch"], e["id"]), reverse=True)

    cities = {}
    for name, (lat, lon) in rc.COORDS.items():
        cities[name] = {"lat": lat, "lon": lon, "border": name in rc.BORDER_CITIES}

    out = {
        "updated": now.isoformat(timespec="seconds"),
        "channels": len(channels),
        "channels_failed": stats["channels_failed"],
        "keep_days": keep_days,
        "cities": cities,
        "events": all_ev,
    }
    new_text = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    old = load_json(data_dir / "events.json", {})
    changed = json.dumps(old.get("events"), ensure_ascii=False) != json.dumps(all_ev, ensure_ascii=False)
    # оновлюємо файл, якщо змінились події або минуло 30 хв (щоб у додатку було видно «живий» статус)
    stale = True
    try:
        stale = (now - datetime.fromisoformat(old.get("updated"))) > timedelta(minutes=30)
    except Exception:
        pass
    if changed or stale:
        (data_dir / "events.json").write_text(new_text, encoding="utf-8")
    (data_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Каналів: {stats['channels_ok']}/{len(channels)}, нових повідомлень: {stats['new_msgs']}, "
          f"подій у базі: {len(all_ev)}, змінено: {changed}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data", help="тека з events.json / state.json")
    a = ap.parse_args()
    asyncio.run(run(Path(a.data)))


if __name__ == "__main__":
    main()
