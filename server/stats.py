"""Анонімна статистика відвідувань застосунку: без імен, IP чи телефонів — лише випадковий id пристрою."""
import json
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    KYIV = ZoneInfo("Europe/Kyiv")
except Exception:            # pragma: no cover
    KYIV = None


def _today(dt=None):
    return (dt or datetime.now(KYIV)).strftime("%Y-%m-%d")


class Stats:
    KEEP_DAYS = 400

    def __init__(self, data_dir: Path):
        self.f = Path(data_dir) / "stats.json"
        try:
            self.d = json.loads(self.f.read_text())
        except Exception:
            self.d = {}
        self.d.setdefault("users", {})     # uid -> {"first": день, "last": день, "inst": 0/1, "pl": платформа, "city": місто}
        self.d.setdefault("days", {})      # день -> {"u": [uid...], "opens": n}
        self.dirty = False
        self.saved = 0

    def hit(self, body: bytes):
        try:
            o = json.loads(body or b"{}")
        except Exception:
            return False
        uid = str(o.get("id") or "")[:40]
        if len(uid) < 8 or not uid.replace("-", "").isalnum():
            return False
        day = _today()
        u = self.d["users"].setdefault(uid, {"first": day})
        u["last"] = day
        u["inst"] = 1 if o.get("inst") else u.get("inst", 0)
        u["pl"] = str(o.get("pl") or u.get("pl") or "?")[:12]
        if o.get("city"):
            u["city"] = str(o["city"])[:40]
        u["push"] = 1 if o.get("push") else 0
        dd = self.d["days"].setdefault(day, {"u": [], "opens": 0})
        if uid not in dd["u"]:
            dd["u"].append(uid)
        dd["opens"] += 1
        self.dirty = True
        self.maybe_save()
        return True

    def maybe_save(self, force=False):
        if not self.dirty or (not force and time.time() - self.saved < 30):
            return
        cut = (datetime.now(KYIV) - timedelta(days=self.KEEP_DAYS)).strftime("%Y-%m-%d")
        for k in [k for k in self.d["days"] if k < cut]:
            del self.d["days"][k]
        tmp = self.f.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False))
        tmp.replace(self.f)
        self.dirty, self.saved = False, time.time()

    def active(self, days):
        now = datetime.now(KYIV)
        keys = {(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)}
        s = set()
        for k in keys:
            s.update(self.d["days"].get(k, {}).get("u", []))
        return s

    def page(self, push_subs=None, bot_subs=None):
        now = datetime.now(KYIV)
        U = self.d["users"]
        a1, a7, a30 = self.active(1), self.active(7), self.active(30)
        y = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        ay = set(self.d["days"].get(y, {}).get("u", []))
        inst = sum(1 for u in U.values() if u.get("inst"))
        rows = []
        for i in range(30):
            k = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            dd = self.d["days"].get(k, {"u": [], "opens": 0})
            new = sum(1 for u in U.values() if u.get("first") == k)
            rows.append((k, len(dd["u"]), new, dd["opens"]))
        mx = max([r[1] for r in rows] + [1])
        pl = Counter(U[x].get("pl", "?") for x in a30 if x in U)
        cities = Counter(U[x].get("city") for x in a30 if x in U and U[x].get("city"))
        pcity = Counter()
        if push_subs:
            for v in push_subs.values():
                if v.get("all"):
                    pcity["усі міста"] += 1
                for c in v.get("cities") or []:
                    pcity[c] += 1
        PL = {"android": "Android", "ios": "iPhone / iPad", "win": "Windows", "mac": "Mac", "linux": "Linux", "?": "інше"}

        def tbl(cnt, n=15):
            return "".join(f"<tr><td>{k}</td><td class=n>{v}</td></tr>" for k, v in cnt.most_common(n)) or "<tr><td colspan=2 class=m>поки немає даних</td></tr>"

        days = "".join(
            f"<tr><td>{datetime.strptime(k, '%Y-%m-%d').strftime('%d.%m')}</td><td class=n>{u}</td>"
            f"<td><div class=bar style='width:{max(2, round(u / mx * 100))}%'></div></td><td class=n>{nw}</td><td class=n>{o}</td></tr>"
            for k, u, nw, o in rows)
        card = lambda v, t: f"<div class=c><b>{v}</b><span>{t}</span></div>"
        return f"""<!doctype html><html lang=uk><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Статистика · Небо над містом</title><style>
body{{font:15px/1.45 system-ui,sans-serif;background:#0f141c;color:#e6e9ef;margin:0;padding:16px;max-width:760px;margin:auto}}
h1{{font-size:20px;margin:4px 0 2px}} h2{{font-size:16px;margin:24px 0 8px}} .m{{color:#8a93a3}}
.g{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:12px}}
.c{{background:#18202b;border-radius:10px;padding:12px}} .c b{{display:block;font-size:26px}} .c span{{color:#8a93a3;font-size:13px}}
table{{width:100%;border-collapse:collapse}} td,th{{padding:5px 6px;border-bottom:1px solid #222c39;text-align:left}} th{{color:#8a93a3;font-weight:500;font-size:13px}}
.n{{text-align:right;font-variant-numeric:tabular-nums;width:60px}} .bar{{height:10px;background:#f2a33a;border-radius:3px}}
</style></head><body><h1>Статистика застосунку</h1><div class=m>станом на {now.strftime('%d.%m.%Y %H:%M')} · анонімно, без особистих даних</div>
<div class=g>{card(len(a1), 'користувачів сьогодні')}{card(len(ay), 'учора')}{card(len(a7), 'за 7 днів')}{card(len(a30), 'за 30 днів')}
{card(len(U), 'усього пристроїв')}{card(inst, 'встановили як застосунок')}{card(len(push_subs or {}), 'увімкнули push')}{card(bot_subs if bot_subs is not None else '—', 'у Telegram-боті')}</div>
<h2>По днях</h2><table><tr><th>день</th><th class=n>людей</th><th></th><th class=n>нових</th><th class=n>відкрит.</th></tr>{days}</table>
<h2>Пристрої (за 30 днів)</h2><table>{tbl(Counter({PL.get(k, k): v for k, v in pl.items()}))}</table>
<h2>«Моє місто» (за 30 днів)</h2><table>{tbl(cities)}</table>
<h2>Сповіщення по містах</h2><table>{tbl(pcity, 25)}</table>
<p class=m>Пристрій рахується за випадковим номером, який застосунок зберігає у браузері. Очищення даних браузера чи інший браузер — це «новий» пристрій.</p>
</body></html>"""
