# -*- coding: utf-8 -*-
"""Вхід службового акаунта Telegram на сервері (QR-код у терміналі)."""
import asyncio
import getpass
import os
from pathlib import Path

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession

ENV = Path("/etc/rating/env")


async def main():
    env = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    if not env.get("TG_API_ID") or not env.get("TG_API_HASH"):
        print("Ключі з my.telegram.org (їх видно у файлі налаштування.txt на комп'ютері).")
        env["TG_API_ID"] = input("api_id: ").strip()
        env["TG_API_HASH"] = input("api_hash: ").strip()
        ENV.parent.mkdir(parents=True, exist_ok=True)
        ENV.write_text(f"TG_API_ID={env['TG_API_ID']}\nTG_API_HASH={env['TG_API_HASH']}\n")
        os.chmod(ENV, 0o640)

    client = TelegramClient(StringSession(), int(env["TG_API_ID"]), env["TG_API_HASH"],
                            device_model="Rating live server", system_version="Linux", app_version="1.0")
    await client.connect()
    qr = await client.qr_login()
    while True:
        q = qrcode.QRCode(border=2)
        q.add_data(qr.url)
        q.make()
        print("\n" * 2)
        q.print_ascii(invert=True)
        print("\nВідскануйте службовим телефоном: Telegram → Налаштування → Пристрої → «Підключити пристрій».")
        print("(Якщо QR не вміщається — зменште шрифт: Ctrl + «-»)")
        try:
            await qr.wait(timeout=60)
            break
        except asyncio.TimeoutError:
            await qr.recreate()
        except SessionPasswordNeededError:
            pw = getpass.getpass("Хмарний пароль Telegram (символи не відображаються): ")
            await client.sign_in(password=pw)
            break
    me = await client.get_me()
    Path("/etc/rating/session").write_text(client.session.save())
    os.chmod("/etc/rating/session", 0o640)
    print(f"\nГотово: увійшли як {me.first_name or ''} (@{me.username or '—'}).")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
