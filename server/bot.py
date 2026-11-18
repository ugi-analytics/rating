# -*- coding: utf-8 -*-
"""
Telegram-бот сповіщень «Рейтинг влучань».

* Кожен користувач бота обирає свої міста (або всі) — щойно в офіційному каналі з'являється
  допис про влучання в місті, бот надсилає повідомлення.
* Токен бота вводиться на сторінці сервера https://<сервер>/login/bot
  (підтвердження — api_hash службового акаунта), зберігається у /var/lib/rating/bot_token.
"""
import asyncio
import html
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from telethon import TelegramClient, events, Button
from telethon.sessions import StringSession

import rating_core as rc

APP_URL = "https://ugi-analytics.github.io/rating/"


class Notifier:
    def __init__(self, data_dir: Path, api_id: int, api_hash: str, log):
        self.data = data_dir
        self.api_id, self.api_hash, self.log = api_id, api_hash, log
        self.token_f = data_dir / "bot_token"
        self.sess_f = data_dir / "bot.session"
        self.subs_f = data_dir / "subs.json"
        self.q = asyncio.Queue()
        self.client = None
        self.me = None
        try:
            self.subs = {int(k): v for k, v in json.loads(self.subs_f.read_text()).items()}
        except Exception:
            self.subs = {}
        self.cities = sorted(rc.COORDS)
        self.push = None
        self._sender_task = None

    # ---------- збереження ----------
    def save_subs(self):
        tmp = self.subs_f.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.subs, ensure_ascii=False))
        os.replace(tmp, self.subs_f)

    def sub(self, chat):
        return self.subs.setdefault(chat, {"cities": [], "all": False})

    # ---------- автоматичне створення бота через @BotFather (від службового акаунта) ----------
    async def create_via_botfather(self, user_client):
        import random
        import re
        tok_re = re.compile(r"(\d{6,}:[A-Za-z0-9_-]{30,})")
        names = ["vluchannia_rating_bot", "rating_vluchan_bot", "tylovi_mista_bot"]
        names += [f"vluchannia_rating_{random.randint(100, 9999)}_bot" for _ in range(4)]
        async with user_client.conversation("BotFather", timeout=40) as conv:
            await conv.send_message("/cancel")
            await conv.get_response()
            await conv.send_message("/newbot")
            r = await conv.get_response()
            if "name" not in r.raw_text.lower():
                raise RuntimeError("BotFather: " + r.raw_text[:200])
            await conv.send_message("Рейтинг влучань")
            await conv.get_response()
            for uname in names:
                await asyncio.sleep(2)
                await conv.send_message(uname)
                r = await conv.get_response()
                m = tok_re.search(r.raw_text)
                if m:
                    self.token_f.write_text(m.group(1))
                    os.chmod(self.token_f, 0o600)
                    self.log(f"Бота створено через BotFather: @{uname}")
                    try:   # опис і команди бота
                        for cmd in (f"/setdescription", f"@{uname}",
                                    "Сповіщення про влучання й вибухи в тилових містах України з офіційних каналів ОВА та міських голів. Напишіть назву свого міста."):
                            await asyncio.sleep(1.5)
                            await conv.send_message(cmd)
                            await conv.get_response()
                        for cmd in ("/setcommands", f"@{uname}",
                                    "all - усі міста\nmy - мої міста\ncities - список міст\nstop - вимкнути сповіщення"):
                            await asyncio.sleep(1.5)
                            await conv.send_message(cmd)
                            await conv.get_response()
                    except Exception:
                        pass
                    return True
            raise RuntimeError("BotFather не прийняв жодне ім'я: " + r.raw_text[:200])

    # ---------- запуск бота ----------
    async def start(self):
        if not self.token_f.exists():
            self.log("Бот сповіщень ще не налаштований (сторінка /login/bot)")
            return False
        token = self.token_f.read_text().strip()
        sess = self.sess_f.read_text().strip() if self.sess_f.exists() else ""
        self.client = TelegramClient(StringSession(sess), self.api_id, self.api_hash)
        await self.client.start(bot_token=token)
        self.sess_f.write_text(self.client.session.save())
        os.chmod(self.sess_f, 0o600)
        self.me = await self.client.get_me()
        self.log(f"Бот сповіщень запущено: @{self.me.username}")
        self._handlers()
        self.run_sender()
        return True

    def run_sender(self):
        if not self._sender_task:
            self._sender_task = asyncio.create_task(self._sender())

    def _match(self, text):
        t = text.strip().lower().replace("’", "'").replace("ʼ", "'")
        exact = [c for c in self.cities if c.lower() == t]
        if exact:
            return exact
        return [c for c in self.cities if c.lower().startswith(t)] if len(t) >= 3 else []

    def _status(self, chat):
        s = self.sub(chat)
        if s["all"]:
            return "Зараз: <b>усі міста</b>."
        if s["cities"]:
            return "Зараз стежу за: <b>" + ", ".join(sorted(s["cities"])) + "</b>."
        return "Зараз жодного міста не вибрано."

    def _handlers(self):
        c = self.client

        @c.on(events.NewMessage(pattern=r"^/(start|help)"))
        async def start(ev):
            await ev.respond(
                "Я надсилаю сповіщення, щойно в офіційних каналах ОВА та міських голів "
                "з'являється повідомлення про влучання чи вибух у вашому місті.\n\n"
                "• Напишіть назву міста — додати або прибрати його (наприклад: <code>Полтава</code>)\n"
                "• /all — усі міста\n• /my — мої міста\n• /cities — список міст\n• /stop — вимкнути все\n\n"
                + self._status(ev.chat_id), parse_mode="html",
                buttons=[Button.url("Відкрити рейтинг", APP_URL)])

        @c.on(events.NewMessage(pattern=r"^/my"))
        async def my(ev):
            await ev.respond(self._status(ev.chat_id), parse_mode="html")

        @c.on(events.NewMessage(pattern=r"^/cities"))
        async def cities(ev):
            await ev.respond("Міста, за якими можна стежити:\n" + ", ".join(self.cities) +
                             "\n\n* — міста ближче до фронту: " + ", ".join(sorted(rc.BORDER_CITIES)))

        @c.on(events.NewMessage(pattern=r"^/all"))
        async def all_(ev):
            s = self.sub(ev.chat_id)
            s["all"] = not s["all"]
            self.save_subs()
            await ev.respond(("Увімкнено: усі міста. " if s["all"] else "Режим «усі міста» вимкнено. ")
                             + self._status(ev.chat_id), parse_mode="html")

        @c.on(events.NewMessage(pattern=r"^/stop"))
        async def stop(ev):
            self.subs[ev.chat_id] = {"cities": [], "all": False}
            self.save_subs()
            await ev.respond("Сповіщення вимкнено. Щоб знову ввімкнути — напишіть назву міста або /all.")

        @c.on(events.NewMessage(func=lambda e: e.is_private and e.raw_text and not e.raw_text.startswith("/")))
        async def city(ev):
            found = self._match(ev.raw_text)
            if not found:
                await ev.respond("Не знайшов такого міста. Список — /cities")
                return
            if len(found) > 1:
                await ev.respond("Уточніть: " + ", ".join(found))
                return
            name, s = found[0], self.sub(ev.chat_id)
            if name in s["cities"]:
                s["cities"].remove(name)
                msg = f"Прибрано: {name}. "
            else:
                s["cities"].append(name)
                msg = f"Додано: {name}. "
            self.save_subs()
            await ev.respond(msg + self._status(ev.chat_id), parse_mode="html")

    # ---------- розсилка ----------
    async def _sender(self):
        while True:
            e = await self.q.get()
            if self.push:
                try:
                    await self.push.notify(e)
                except Exception as ex:
                    self.log(f"! push: {type(ex).__name__}: {ex}")
            if not self.client:
                continue
            t = e["t"]
            border = " (ближче до фронту)" if e["c"] in rc.BORDER_CITIES else ""
            cas = []
            if e.get("k"):
                cas.append(f"загиблі: {e['k']}")
            if e.get("i"):
                cas.append(f"поранені: {e['i']}")
            text = (f"💥 <b>{html.escape(e['c'])}</b>{border} · {t[8:10]}.{t[5:7]} {t[11:16]}\n"
                    + (("⚠️ " + ", ".join(cas) + "\n") if cas else "")
                    + "\n" + html.escape(e["x"][:500]) + ("…" if len(e["x"]) > 500 else "")
                    + f"\n\n<a href=\"https://t.me/{e['ch']}/{e['id']}\">Джерело: @{e['ch']}</a>")
            for chat, s in list(self.subs.items()):
                if s.get("all") or e["c"] in s.get("cities", []):
                    try:
                        await self.client.send_message(chat, text, parse_mode="html", link_preview=False)
                    except Exception as ex:
                        self.log(f"! бот → {chat}: {type(ex).__name__}")
                    await asyncio.sleep(0.05)

    # ---------- сторінка налаштування токена ----------
    PAGE = """<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Бот сповіщень — Рейтинг влучань</title><style>
body{font-family:system-ui,sans-serif;background:#0e1217;color:#e8edf2;display:flex;justify-content:center;padding:32px 16px}
.c{max-width:440px;width:100%;background:#161b22;border:1px solid #2a323c;border-radius:14px;padding:24px}
h1{font-size:20px;margin:0 0 6px}p{color:#9aa7b4;line-height:1.5}label{display:block;margin:14px 0 4px;font-size:14px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #39424e;background:#0e1217;color:#fff;font-size:16px}
button{margin-top:18px;width:100%;padding:12px;border:0;border-radius:8px;background:#ff7a2f;color:#111;font-weight:700;font-size:16px;cursor:pointer}
.ok{color:#5fd38d;font-weight:700}.err{color:#ff5d5d}a{color:#ff9a5c}</style><div class="c">%BODY%</div></html>"""

    def form(self, msg=""):
        cur = f'<p class="ok">Зараз працює бот @{self.me.username}.</p>' if self.me else ""
        return self.PAGE.replace("%BODY%", f"""<h1>Бот сповіщень</h1>{cur}{msg}
<p>Токен від @BotFather і, для підтвердження, <b>api_hash</b> службового акаунта (з файлу налаштування.txt).</p>
<form method="post"><label>Токен бота</label><input name="token" required autocomplete="off">
<label>api_hash</label><input name="api_hash" required autocomplete="off"><button>Зберегти й запустити</button></form>""")

    async def handle_http(self, reader, writer):
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            hdr = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
            body = await reader.readexactly(int(hdr.get("content-length") or 0)) if hdr.get("content-length") else b""
            path = urlparse(target).path
            status, out = "200 OK", ""
            if path.startswith("/login/push/"):
                if method == "OPTIONS":
                    st, data, ct = "204 No Content", b"", "text/plain"
                elif self.push and self.push.ok and method == "POST":
                    st, obj = self.push.api(path, body)
                    data, ct = json.dumps(obj).encode(), "application/json"
                else:
                    st, data, ct = "503 Service Unavailable", b"{}", "application/json"
                writer.write((f"HTTP/1.1 {st}\r\nContent-Type: {ct}\r\nContent-Length: {len(data)}\r\n"
                              "Access-Control-Allow-Methods: POST, OPTIONS\r\nAccess-Control-Allow-Headers: Content-Type\r\n"
                              "Access-Control-Max-Age: 86400\r\nConnection: close\r\n\r\n").encode() + data)
                await writer.drain()
                return
            if path != "/login/bot":
                status, out = "404 Not Found", "-"
            elif method == "POST":
                f = {k: v[0].strip() for k, v in parse_qs(body.decode("utf-8")).items()}
                if f.get("api_hash") != self.api_hash:
                    out = self.form('<p class="err">api_hash не збігається.</p>')
                elif ":" not in f.get("token", ""):
                    out = self.form('<p class="err">Схоже, це не токен бота.</p>')
                else:
                    self.token_f.write_text(f["token"])
                    os.chmod(self.token_f, 0o600)
                    self.sess_f.unlink(missing_ok=True)
                    try:
                        if self.client:
                            await self.client.disconnect()
                        await self.start()
                        out = self.PAGE.replace("%BODY%", f"""<h1>Готово ✓</h1>
<p class="ok">Бот @{self.me.username} запущено.</p><p>Відкрийте <a href="https://t.me/{self.me.username}">t.me/{self.me.username}</a>,
натисніть «Start» і напишіть назву свого міста.</p>""")
                    except Exception as ex:
                        self.token_f.unlink(missing_ok=True)
                        out = self.form(f'<p class="err">Telegram не прийняв токен ({type(ex).__name__}).</p>')
            else:
                out = self.form()
            data = out.encode()
            writer.write((f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(data)}\r\n"
                          "Cache-Control: no-store\r\nConnection: close\r\n\r\n").encode() + data)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
