# -*- coding: utf-8 -*-
"""
Web Push-сповіщення в додаток «Рейтинг влучань» (PWA): приходять, навіть коли додаток закритий.

* Ключі VAPID створюються один раз: /var/lib/rating/vapid_private.pem; публічний ключ іде в events.json.
* Підписки: /var/lib/rating/push_subs.json  {endpoint: {"sub": {...}, "cities": [...], "all": bool}}
* HTTP (Caddy проксіює /login* на 127.0.0.1:8099):
    POST /login/push/subscribe    {"sub": PushSubscription.toJSON(), "cities": ["Полтава"], "all": false}
    POST /login/push/unsubscribe  {"endpoint": "..."}
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import rating_core as rc

EXT = None


def ensure_deps(data_dir: Path, log):
    """pywebpush ставиться в /var/lib/rating/pyext (сервіс працює без прав root)."""
    global EXT
    EXT = data_dir / "pyext"
    if str(EXT) not in sys.path:
        sys.path.insert(0, str(EXT))
    try:
        import pywebpush  # noqa: F401
        return True
    except ImportError:
        pass
    log("Встановлюю pywebpush…")
    env = dict(os.environ, HOME=str(data_dir), PIP_DISABLE_PIP_VERSION_CHECK="1")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--target", str(EXT), "pywebpush"],
                       env=env, capture_output=True, text=True, timeout=600)
    if r.returncode:
        log("! pywebpush не встановився: " + (r.stderr or r.stdout)[-300:])
        return False
    import importlib
    importlib.invalidate_caches()
    return True


class Push:
    def __init__(self, data_dir: Path, log):
        self.data, self.log = data_dir, log
        self.ok = ensure_deps(data_dir, log)
        self.key_f = data_dir / "vapid_private.pem"
        self.subs_f = data_dir / "push_subs.json"
        self.public = None
        self.last_thr = {}
        self.pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="push")
        try:
            self.subs = json.loads(self.subs_f.read_text())
        except Exception:
            self.subs = {}
        if self.ok:
            self._keys()

    def _keys(self):
        from py_vapid import Vapid01
        from cryptography.hazmat.primitives import serialization
        if not self.key_f.exists():
            v = Vapid01()
            v.generate_keys()
            v.save_key(str(self.key_f))
            os.chmod(self.key_f, 0o600)
        v = Vapid01.from_file(str(self.key_f))
        raw = v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.log(f"Push готовий, підписок: {len(self.subs)}")

    def save(self):
        tmp = self.subs_f.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.subs, ensure_ascii=False))
        os.replace(tmp, self.subs_f)

    # ---------- HTTP ----------
    def api(self, path, body: bytes):
        """Повертає (статус, dict)."""
        try:
            d = json.loads(body or b"{}")
        except Exception:
            return "400 Bad Request", {"ok": False}
        if path == "/login/push/subscribe":
            sub = d.get("sub") or {}
            ep = sub.get("endpoint", "")
            if not ep.startswith("https://") or not (sub.get("keys") or {}).get("p256dh"):
                return "400 Bad Request", {"ok": False}
            cities = [c for c in (d.get("cities") or []) if isinstance(c, str)][:60]
            self.subs[ep] = {"sub": sub, "cities": cities, "all": bool(d.get("all"))}
            self.save()
            self.log(f"Push-підписка: {', '.join(cities) or 'усі міста'} (усього {len(self.subs)})")
            return "200 OK", {"ok": True}
        if path == "/login/push/unsubscribe":
            if self.subs.pop(d.get("endpoint", ""), None):
                self.save()
            return "200 OK", {"ok": True}
        return "404 Not Found", {"ok": False}

    async def confirm(self, ep):
        s = self.subs.get(ep)
        if not s:
            return
        what = "усі міста" if s.get("all") else ", ".join(s.get("cities", []))
        payload = json.dumps({"title": "🔔 Сповіщення увімкнено", "body": f"Стежу за: {what}. Щойно з'явиться допис про влучання — прийде сповіщення.",
                              "tag": "rv-confirm", "url": "https://ugi-analytics.github.io/rating/"}, ensure_ascii=False)
        r = await asyncio.to_thread(self._send, s["sub"], payload)
        self.log(f"Тестове push-сповіщення: {r}")

    async def _fanout(self, targets, payload):
        """Паралельна розсилка (до 32 одночасно); прибирає підписки, яких уже не існує."""
        targets = [ep for ep in targets if ep in self.subs]
        if not targets:
            return
        loop = asyncio.get_running_loop()
        res = await asyncio.gather(*[loop.run_in_executor(self.pool, self._send, self.subs[ep]["sub"], payload)
                                     for ep in targets])
        gone = [ep for ep, r in zip(targets, res) if r == "gone"]
        for ep in gone:
            self.subs.pop(ep, None)
        if gone:
            self.save()
        self.log(f"Push: {len(targets)} адресатів, доставлено {sum(r is True for r in res)}, прибрано {len(gone)}")

    # ---------- розсилка ----------
    def _send(self, sub, payload):
        from pywebpush import webpush, WebPushException
        try:
            webpush(subscription_info=sub, data=payload, vapid_private_key=str(self.key_f),
                    vapid_claims={"sub": "mailto:rating@ugi-analytics.github.io"}, ttl=3600,
                    headers={"Urgency": "high"})
            return True
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            return "gone" if code in (404, 410) else False
        except Exception:
            return False

    def _threat_payload(self, d):
        where = d["c"] or d["r"]
        return json.dumps({"title": f"{d['ic']} {where}: {d['kn']}", "body": f"{d['t'][11:16]} · {d['x']}",
                           "tag": f"rv-thr-{where}", "url": "https://ugi-analytics.github.io/rating/#threats"}, ensure_ascii=False)

    async def notify_threat(self, d):
        now = time.time()
        payload = self._threat_payload(d)
        target = d["c"] or d["r"]
        targets = []
        for ep, s in list(self.subs.items()):
            cities = s.get("cities", [])
            regs = {rc.CITY_REGION.get(c) for c in cities}
            hit = s.get("all") or (d["c"] and d["c"] in cities) or (not d["c"] and d["r"] in regs)
            if not hit:
                continue
            key = (ep, target)
            last_t, last_k = self.last_thr.get(key, (0, ""))
            urgent = d["k"] in ("bal", "rkt", "fast", "jet") and last_k not in ("bal", "rkt", "fast", "jet")
            if now - last_t < (240 if d["c"] else 600) and not urgent:
                continue
            self.last_thr[key] = (now, d["k"])
            targets.append(ep)
        await self._fanout(targets, payload)

    async def notify(self, e):
        if not self.ok or not self.subs:
            return
        if e.get("type") == "threat":
            return await self.notify_threat(e)
        t = e["t"]
        cas = []
        if e.get("k"):
            cas.append(f"загиблі: {e['k']}")
        if e.get("i"):
            cas.append(f"поранені: {e['i']}")
        body = (f"{t[11:16]} · " + ("; ".join(cas) + " · " if cas else "") + e["x"][:180]).replace("\n", " ")
        payload = json.dumps({"title": f"💥 Влучання: {e['c']}", "body": body, "city": e["c"],
                              "tag": f"rv-{e['c']}-{e['ch']}-{e['id']}",
                              "url": "https://ugi-analytics.github.io/rating/?city=" + e["c"]}, ensure_ascii=False)
        targets = [ep for ep, s in list(self.subs.items()) if s.get("all") or e["c"] in s.get("cities", [])]
        await self._fanout(targets, payload)
