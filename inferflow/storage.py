import json
import math
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path=":memory:", ttl=3600):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, ts REAL, data TEXT);
        CREATE TABLE IF NOT EXISTS attempts (ts REAL, kind TEXT);
        """)
        self.ttl = ttl

    def get(self, key):
        if not key:
            return None
        row = self.db.execute("SELECT value FROM cache WHERE key=? AND expires>?", (key, time.time())).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        if key:
            self.db.execute("DELETE FROM cache WHERE expires<=?", (time.time(),))
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value), time.time() + self.ttl))
            # Bounded local cache, evict earliest expiry first.
            self.db.execute("DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY expires DESC LIMIT -1 OFFSET 2000)")
            self.db.commit()

    def attempt(self, kind):
        self.db.execute("INSERT INTO attempts VALUES (?,?)", (time.time(), kind))
        self.db.execute("DELETE FROM attempts WHERE ts<?", (time.time() - 86400,))
        self.db.commit()

    def recent_attempt_times(self, seconds):
        return [r[0] for r in self.db.execute("SELECT ts FROM attempts WHERE ts>? ORDER BY ts", (time.time()-seconds,))]

    def budget_used(self):
        return self.db.execute("SELECT COUNT(*) FROM attempts WHERE ts>?", (time.time() - 86400,)).fetchone()[0]

    def record(self, event):
        self.db.execute("INSERT INTO events(ts,data) VALUES (?,?)", (time.time(), json.dumps(event)))
        self.db.execute("DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 10000)")
        self.db.commit()

    def events(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM events ORDER BY id")]

    def close(self):
        self.db.close()


def percentile(values, q):
    return round(sorted(values)[max(0, math.ceil(len(values)*q)-1)], 3) if values else None


def summarize(events, elapsed):
    def lat(rows, field):
        values = [x[field] for x in rows]
        return {"p50_ms": percentile(values, .5), "p95_ms": percentile(values, .95)}
    successes = [e for e in events if e["status"] == "success"]
    attempts = sum(e["attempts"] for e in events)
    hits = sum(e["cache_hit"] for e in events)
    return {
        "requests": len(events), "successes": len(successes), "errors": len(events)-len(successes),
        "elapsed_seconds": round(elapsed, 3), "successful_rps": round(len(successes)/max(elapsed, .001), 3),
        "attempts": attempts, "rate_limited_attempts": sum(e["rate_limited"] for e in events),
        "rate_429": sum(e["rate_limited"] for e in events)/max(attempts, 1),
        "cache_hits": hits, "cache_hit_rate": hits/max(len(events), 1),
        "upstream_tokens": sum(e["upstream_tokens"] for e in events),
        "served_tokens": sum(e["served_tokens"] for e in successes),
        "queue": lat(events, "queue_ms"), "end_to_end": lat(events, "e2e_ms"),
        "successful_end_to_end": lat(successes, "e2e_ms"),
        "by_priority": {p: {"requests": len(rows), "errors": sum(e["status"] != "success" for e in rows),
                              "queue": lat(rows, "queue_ms"), "end_to_end": lat(rows, "e2e_ms")}
                        for p in ("interactive", "background")
                        for rows in [[e for e in events if e["priority"] == p]]},
        "outcomes": {s: sum(e["status"] == s for e in events) for s in sorted({e["status"] for e in events})},
    }
