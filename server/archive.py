"""Архів влучань: усі зафіксовані прильоти з дати першого допису каналу, з пошуком за днем і містом."""
import json
import sqlite3
import threading
from pathlib import Path


class Archive:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "archive.db"
        self.lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.create_function("ulower", 1, lambda v: (v or "").lower(), deterministic=True)
        self.db.execute("""CREATE TABLE IF NOT EXISTS hits(
            ch TEXT, id INTEGER, city TEXT, region TEXT, t TEXT, day TEXT,
            k INTEGER, i INTEGER, x TEXT, PRIMARY KEY(ch, id, city))""")
        self.db.execute("CREATE INDEX IF NOT EXISTS hits_day ON hits(day)")
        self.db.execute("CREATE INDEX IF NOT EXISTS hits_city_day ON hits(city, day)")
        self.db.execute("CREATE TABLE IF NOT EXISTS backfill(ch TEXT PRIMARY KEY, min_id INTEGER, done INTEGER DEFAULT 0, oldest TEXT)")
        self.db.commit()
        self.pending = 0

    def put(self, ch, mid, events, region_of):
        """Замінює записи одного допису (після редагування допису влучання можуть змінитись)."""
        with self.lock:
            self.db.execute("DELETE FROM hits WHERE ch=? AND id=?", (ch, mid))
            for e in events:
                self.db.execute("INSERT OR REPLACE INTO hits VALUES (?,?,?,?,?,?,?,?,?)",
                                (ch, mid, e["c"], region_of(e["c"]), e["t"], e["t"][:10],
                                 e.get("k") or 0, e.get("i") or 0, (e.get("x") or "")[:1500]))
            self.pending += 1
            if self.pending >= 50:
                self.db.commit()
                self.pending = 0

    def commit(self):
        with self.lock:
            self.db.commit()
            self.pending = 0

    # --- дочитування історії каналу ---
    def bf_get(self, ch):
        r = self.db.execute("SELECT min_id, done, oldest FROM backfill WHERE ch=?", (ch,)).fetchone()
        return {"min_id": r[0], "done": r[1], "oldest": r[2]} if r else None

    def bf_set(self, ch, min_id, done=0, oldest=None):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO backfill VALUES (?,?,?,?)", (ch, min_id, done, oldest))
            self.db.commit()

    # --- запити для застосунку ---
    def query(self, qs):
        day = (qs.get("day") or [""])[0][:10]
        city = (qs.get("city") or [""])[0][:60]
        month = (qs.get("month") or [""])[0][:7]
        with self.lock:
            if month:            # календар: скільки влучань у кожен день місяця
                sql, a = "SELECT day, COUNT(DISTINCT ch||id||city) FROM hits WHERE day LIKE ?", [month + "%"]
                if city:
                    sql += " AND city=?"
                    a.append(city)
                rows = self.db.execute(sql + " GROUP BY day", a).fetchall()
                return {"month": month, "city": city, "days": {d: n for d, n in rows}}
            frm = (qs.get("from") or [""])[0][:10]
            to = (qs.get("to") or [""])[0][:10]
            q = (qs.get("q") or [""])[0][:80]
            region = (qs.get("region") or [""])[0][:60]
            if frm and to:        # діапазон дат (для розділу «Архів» нової версії)
                sql, a = "SELECT t, city, region, ch, id, k, i, x FROM hits WHERE day BETWEEN ? AND ?", [frm, to]
                if city:
                    sql += " AND city=?"; a.append(city)
                if region:
                    sql += " AND region=?"; a.append(region)
                if q:
                    sql += " AND ulower(x) LIKE ?"; a.append("%" + q.lower() + "%")
                rows = self.db.execute(sql + " ORDER BY t DESC LIMIT 3001", a).fetchall()
                more = len(rows) > 3000
                rows = rows[:3000]
                day = None
            if not day and not (frm and to):
                r = self.db.execute("SELECT MIN(day), MAX(day), COUNT(*) FROM hits").fetchone()
                bf = self.db.execute("SELECT COUNT(*), SUM(done) FROM backfill").fetchone()
                return {"from": r[0], "to": r[1], "total": r[2], "backfill": {"channels": bf[0], "done": bf[1] or 0}}
            if day:
                more = False
                sql, a = "SELECT t, city, region, ch, id, k, i, x FROM hits WHERE day=?", [day]
                if city:
                    sql += " AND city=?"
                    a.append(city)
                rows = self.db.execute(sql + " ORDER BY t", a).fetchall()
        by = {}
        for t, c, r, ch, mid, k, i, x in rows:      # один допис про кілька міст — один запис
            key = (ch, mid)
            d = by.setdefault(key, {"t": t, "ch": ch, "id": mid, "cities": [], "regions": [], "k": 0, "i": 0, "x": x})
            if r and r not in d["regions"]:
                d["regions"].append(r)
            d["cities"].append(c)
            d["k"] += k
            d["i"] += i
        items = list(by.values())
        cities = {}
        for d in items:
            for c in d["cities"]:
                cities[c] = cities.get(c, 0) + 1
        return {"day": day, "from": frm, "to": to, "city": city, "items": items, "cities": cities, "truncated": more}
