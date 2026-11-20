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
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(hits)")]
        if "n" not in cols:                       # скільки влучань описує допис (1 ракета/дрон = 1 влучання)
            self.db.execute("ALTER TABLE hits ADD COLUMN n INTEGER DEFAULT 1")
        self.db.execute("CREATE INDEX IF NOT EXISTS hits_day ON hits(day)")
        self.db.execute("CREATE INDEX IF NOT EXISTS hits_city_day ON hits(city, day)")
        self.db.execute("CREATE TABLE IF NOT EXISTS backfill(ch TEXT PRIMARY KEY, min_id INTEGER, done INTEGER DEFAULT 0, oldest TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS hits_removed AS SELECT * FROM hits WHERE 0")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        if "n" not in [r[1] for r in self.db.execute("PRAGMA table_info(hits_removed)")]:
            self.db.execute("ALTER TABLE hits_removed ADD COLUMN n INTEGER DEFAULT 1")
        self.db.commit()
        self.pending = 0

    def put(self, ch, mid, events, region_of):
        """Замінює записи одного допису (після редагування допису влучання можуть змінитись)."""
        with self.lock:
            self.db.execute("DELETE FROM hits WHERE ch=? AND id=?", (ch, mid))
            for e in events:
                self.db.execute("INSERT OR REPLACE INTO hits(ch,id,city,region,t,day,k,i,x,n) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (ch, mid, e["c"], region_of(e["c"]), e["t"], e["t"][:10],
                                 e.get("k") or 0, e.get("i") or 0, (e.get("x") or "")[:1500], e.get("n") or 1))
            self.pending += 1
            if self.pending >= 50:
                self.db.commit()
                self.pending = 0

    def commit(self):
        with self.lock:
            self.db.commit()
            self.pending = 0

    def meta_get(self, k):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else None

    def meta_set(self, k, v):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))
            self.db.commit()

    def restore_removed(self):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO hits(ch,id,city,region,t,day,k,i,x,n) SELECT ch,id,city,region,t,day,k,i,x,n FROM hits_removed")
            self.db.execute("DELETE FROM hits_removed")
            self.db.commit()

    def recheck(self, keep):
        """Перевіряє весь архів новою методикою: keep(text, city) -> False означає хибне «влучання».
        Відкинуті записи не видаляються назавжди, а переносяться в hits_removed (можна повернути)."""
        with self.lock:
            rows = self.db.execute("SELECT rowid, ch, id, city, x FROM hits").fetchall()
        bad = [r for r in rows if not keep(r[4] or "", r[3])]
        with self.lock:
            for rowid, *_ in bad:
                self.db.execute("INSERT INTO hits_removed(ch,id,city,region,t,day,k,i,x,n) SELECT ch,id,city,region,t,day,k,i,x,n FROM hits WHERE rowid=?", (rowid,))
                self.db.execute("DELETE FROM hits WHERE rowid=?", (rowid,))
            self.db.commit()
        return len(rows), bad

    def recount(self, fn):
        """Перераховує кількість влучань (n) для всіх записів: fn(text, city, cities_in_post) -> int."""
        with self.lock:
            rows = self.db.execute("SELECT rowid, ch, id, city, x FROM hits").fetchall()
            per = {}
            for _, ch, mid, _, _ in rows:
                per[(ch, mid)] = per.get((ch, mid), 0) + 1
            for rowid, ch, mid, c, x in rows:
                self.db.execute("UPDATE hits SET n=? WHERE rowid=?", (fn(x or "", c, per[(ch, mid)]), rowid))
            self.db.commit()
        return len(rows)

    def export_compact(self):
        """Усі влучання без текстів — для підрахунків у застосунку за весь період (з 24.02.2022)."""
        with self.lock:
            rows = self.db.execute("SELECT city, t, ch, id, k, i, n FROM hits ORDER BY t").fetchall()
        cities, chans, ci, chi, out = [], [], {}, {}, []
        for c, t, ch, mid, k, i, n in rows:
            if c not in ci:
                ci[c] = len(cities); cities.append(c)
            if ch not in chi:
                chi[ch] = len(chans); chans.append(ch)
            out.append([ci[c], t[:16], chi[ch], mid, k or 0, i or 0, n or 1])
        return {"cities": cities, "channels": chans, "rows": out}

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
