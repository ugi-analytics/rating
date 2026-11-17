# -*- coding: utf-8 -*-
"""
Вхід службового акаунта Telegram через веб-сторінку (для серверів без доступу до консолі).
Сторінка: https://<сервер>/login?k=<ключ>  (Caddy проксіює /login* сюди, на 127.0.0.1:8099).
Після успішного входу зберігає /etc/rating/env і /etc/rating/session, запускає збирач і завершується.
"""
import asyncio
import html
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import qrcode
import qrcode.image.svg
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

KEY = sys.argv[1]
ENVF, SESF = Path("/etc/rating/env"), Path("/etc/rating/session")
S = {"state": "creds", "msg": "", "url": None}
client = None
qr = None
done = asyncio.Event()

PAGE = """<!doctype html><html lang="uk"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Вхід Telegram — Рейтинг влучань</title><style>
body{font-family:system-ui,sans-serif;background:#0e1217;color:#e8edf2;display:flex;justify-content:center;padding:32px 16px}
.c{max-width:440px;width:100%;background:#161b22;border:1px solid #2a323c;border-radius:14px;padding:24px}
h1{font-size:20px;margin:0 0 6px}p{color:#9aa7b4;line-height:1.5}label{display:block;margin:14px 0 4px;font-size:14px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #39424e;background:#0e1217;color:#fff;font-size:16px}
button{margin-top:18px;width:100%;padding:12px;border:0;border-radius:8px;background:#ff7a2f;color:#111;font-weight:700;font-size:16px;cursor:pointer}
#qr{background:#fff;border-radius:10px;padding:12px;margin:14px 0}#qr img{width:100%;display:block}
.ok{color:#5fd38d;font-weight:700;font-size:18px}.err{color:#ff5d5d}</style><div class="c">%BODY%</div></html>"""

CREDS = """<h1>Підключення службового акаунта</h1>
<p>Ключі з my.telegram.org — їх видно у файлі <b>налаштування.txt</b> на комп'ютері.</p>
<form method="post" action="/login/start?k=%K%"><label>api_id</label><input name="api_id" inputmode="numeric" required>
<label>api_hash</label><input name="api_hash" required autocomplete="off"><button>Далі — показати QR-код</button></form>"""

LIVE = """<h1>Відскануйте QR-код</h1>
<p>На <b>службовому телефоні</b>: Telegram → Налаштування → Пристрої → «Підключити пристрій».</p>
<div id="qr"><img id="img" alt="QR"></div><div id="pw" style="display:none">
<form method="post" action="/login/password?k=%K%"><label>Хмарний пароль Telegram</label>
<input type="password" name="password" required><button>Увійти</button></form></div><p id="st"></p>
<script>
let last='';async function tick(){try{const r=await fetch('/login/state?k=%K%',{cache:'no-store'});const s=await r.json();
if(s.url&&s.url!==last){last=s.url;document.getElementById('img').src='/login/qr.svg?k=%K%&t='+Date.now();}
document.getElementById('pw').style.display=s.state==='password'?'block':'none';
document.getElementById('qr').style.display=s.state==='qr'?'block':'none';
const st=document.getElementById('st');
if(s.state==='done'){st.className='ok';st.textContent='✓ Готово! '+s.msg+' Сторінку можна закрити.';return;}
st.className=s.state==='error'?'err':'';st.textContent=s.msg||'Очікую сканування… (код оновлюється автоматично)';}catch(e){}
setTimeout(tick,2000);}tick();</script>"""


def page(body):
    return PAGE.replace("%BODY%", body.replace("%K%", KEY))


async def qr_flow(api_id, api_hash):
    global client, qr
    try:
        client = TelegramClient(StringSession(), api_id, api_hash, device_model="Rating live server",
                                system_version="Linux", app_version="1.0")
        await client.connect()
        qr = await client.qr_login()
        S.update(state="qr", url=qr.url, msg="")
        while True:
            try:
                await qr.wait(timeout=55)
                break
            except asyncio.TimeoutError:
                await qr.recreate()
                S["url"] = qr.url
            except SessionPasswordNeededError:
                S.update(state="password", msg="На акаунті ввімкнено хмарний пароль — введіть його.")
                return
        await finish()
    except Exception as e:
        S.update(state="error", msg=f"Помилка: {type(e).__name__}: {e}. Оновіть сторінку.")


async def finish():
    me = await client.get_me()
    SESF.write_text(client.session.save())
    for f in (ENVF, SESF):
        os.chmod(f, 0o640)
        subprocess.run(["chgrp", "rating", str(f)])
    S.update(state="done", msg=f"Увійшли як {me.first_name or ''} (@{me.username or '—'}). Збирач запущено.")
    subprocess.run(["systemctl", "restart", "rating-live"])
    await client.disconnect()
    asyncio.get_running_loop().call_later(15, done.set)


async def handle(reader, writer):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        method, target, _ = lines[0].split(" ", 2)
        hdr = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        body = b""
        if int(hdr.get("content-length", 0) or 0):
            body = await reader.readexactly(int(hdr["content-length"]))
        u = urlparse(target)
        q = parse_qs(u.query)
        form = {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
        ctype, status, out = "text/html; charset=utf-8", "200 OK", b""
        if q.get("k", [""])[0] != KEY:
            status, out = "403 Forbidden", "Доступ заборонено".encode()
        elif u.path == "/login":
            out = page(LIVE if S["state"] != "creds" else CREDS).encode()
        elif u.path == "/login/start" and method == "POST" and S["state"] in ("creds", "error"):
            api_id, api_hash = form.get("api_id", "").strip(), form.get("api_hash", "").strip()
            ENVF.write_text(f"TG_API_ID={api_id}\nTG_API_HASH={api_hash}\n")
            S.update(state="wait", msg="З'єднуюсь із Telegram…")
            asyncio.create_task(qr_flow(int(api_id), api_hash))
            status, out = "303 See Other", b""
        elif u.path == "/login/password" and method == "POST" and S["state"] == "password":
            try:
                await client.sign_in(password=form.get("password", ""))
                await finish()
            except Exception as e:
                S["msg"] = f"Пароль не підійшов ({type(e).__name__}). Спробуйте ще раз."
            status, out = "303 See Other", b""
        elif u.path == "/login/state":
            ctype, out = "application/json", json.dumps(S, ensure_ascii=False).encode()
        elif u.path == "/login/qr.svg" and S.get("url"):
            buf = io.BytesIO()
            qrcode.make(S["url"], image_factory=qrcode.image.svg.SvgPathImage, border=2).save(buf)
            ctype, out = "image/svg+xml", buf.getvalue()
        else:
            status, out = "404 Not Found", b"-"
        extra = f"Location: /login?k={KEY}\r\n" if status.startswith("303") else ""
        writer.write((f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(out)}\r\n"
                      f"Cache-Control: no-store\r\n{extra}Connection: close\r\n\r\n").encode() + out)
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()


async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", 8099)
    print("Сторінка входу працює на 127.0.0.1:8099", flush=True)
    async with srv:
        await done.wait()


if __name__ == "__main__":
    asyncio.run(main())
