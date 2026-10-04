"""SQLite state for x4d: the deck, the deck cursor/hold, devices, and button events.

The device is asleep or off most of the time, so the deck lives here rather than on
the device. Selection happens at fetch time: timer wakes advance the deck only after
`dwell_s`; button/session wakes never auto-advance (Left/Right navigate explicitly).
"""
import datetime as dt
import json
import os
import sqlite3
import threading
from contextlib import closing

MAX_SLIDES = 64
ASSIGNABLE = ("confirm", "confirm_long", "back", "up", "down")

SCHEMA = """
CREATE TABLE IF NOT EXISTS slides (id TEXT PRIMARY KEY, body TEXT NOT NULL, created_at REAL NOT NULL,
  updated_at REAL NOT NULL, revision INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS deck (k INTEGER PRIMARY KEY CHECK (k = 1), cursor TEXT, shown_at REAL,
  hold_id TEXT, hold_until REAL, revision INTEGER NOT NULL DEFAULT 0);
INSERT OR IGNORE INTO deck (k) VALUES (1);
CREATE TABLE IF NOT EXISTS devices (device TEXT PRIMARY KEY, last_seen REAL, last_wake TEXT,
  battery TEXT, rssi INTEGER, fw TEXT);
CREATE TABLE IF NOT EXISTS events (rowid INTEGER PRIMARY KEY, device TEXT NOT NULL, boot INTEGER NOT NULL,
  seq INTEGER NOT NULL, card TEXT, etag TEXT, button TEXT NOT NULL, press TEXT NOT NULL, wake TEXT,
  received_at REAL NOT NULL, label TEXT, forward TEXT NOT NULL, UNIQUE (device, boot, seq));
CREATE TABLE IF NOT EXISTS event_context (device TEXT, boot INTEGER, seq INTEGER, body TEXT NOT NULL,
  PRIMARY KEY (device,boot,seq));
"""


def _expired(slide, now):
    exp = slide.get("expires_at")
    return exp is not None and now >= dt.datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()


class Store:
    def __init__(self, path, dwell_s=3600):
        self.path, self.dwell_s = path, dwell_s
        self.lock = threading.RLock()
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as db:
            if self.path != ":memory:": os.chmod(self.path,0o600)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        return _Tx(db)

    # ---- deck -------------------------------------------------------------
    def put(self, request, now):
        slide = request["slide"]
        body = json.dumps(slide, sort_keys=True, ensure_ascii=False)
        with self.lock, self._db() as db:
            row = db.execute("SELECT body, revision FROM slides WHERE id=?", (slide["id"],)).fetchone()
            if row is None:
                if db.execute("SELECT COUNT(*) FROM slides").fetchone()[0] >= MAX_SLIDES:
                    raise ValueError(f"The deck is full ({MAX_SLIDES} slides); remove one first.")
                db.execute("INSERT INTO slides VALUES (?,?,?,?,1)", (slide["id"], body, now, now))
            elif row["body"] != body:
                db.execute("UPDATE slides SET body=?, updated_at=?, revision=revision+1 WHERE id=?",
                           (body, now, slide["id"]))
            if request["intent"] == "next":
                db.execute("UPDATE deck SET cursor=?, shown_at=NULL", (slide["id"],))
            elif request["intent"] == "hold":
                db.execute("UPDATE deck SET hold_id=?, hold_until=?", (slide["id"], now + request["hold_s"]))
            db.execute("UPDATE deck SET revision=revision+1")
            return self._row(db, slide["id"])

    @staticmethod
    def _row(db, ident):
        row = db.execute("SELECT * FROM slides WHERE id=?", (ident,)).fetchone()
        if row is None:
            return None
        return {"slide": json.loads(row["body"]), "created_at": row["created_at"],
                "updated_at": row["updated_at"], "revision": row["revision"]}

    def get(self, ident):
        with self._db() as db:
            return self._row(db, ident)

    def remove(self, ident):
        with self.lock, self._db() as db:
            gone = db.execute("DELETE FROM slides WHERE id=?", (ident,)).rowcount > 0
            db.execute("UPDATE deck SET revision=revision+1")
        return gone

    def slides(self, now):
        with self._db() as db:
            rows = db.execute("SELECT * FROM slides ORDER BY created_at, id").fetchall()
        out = []
        for r in rows:
            slide = json.loads(r["body"])
            out.append({"slide": slide, "created_at": r["created_at"], "updated_at": r["updated_at"],
                        "revision": r["revision"], "expired": _expired(slide, now)})
        return out

    def _live(self, db, now):
        rows = db.execute("SELECT id, body FROM slides ORDER BY created_at, id").fetchall()
        return [r["id"] for r in rows if not _expired(json.loads(r["body"]), now)]

    def current(self, now, wake):
        """The card the device should show for this fetch (advancing the deck if due)."""
        with self.lock, self._db() as db:
            live = self._live(db, now)
            if not live:
                return None
            deck = db.execute("SELECT * FROM deck").fetchone()
            if deck["hold_id"] in live and deck["hold_until"] and now < deck["hold_until"]:
                return {**self._row(db, deck["hold_id"]), "id": deck["hold_id"], "held": True}
            cursor = deck["cursor"] if deck["cursor"] in live else live[0]
            due = deck["shown_at"] is not None and now - deck["shown_at"] >= self.dwell_s
            if wake == "timer" and due and deck["cursor"] in live:
                cursor = live[(live.index(cursor) + 1) % len(live)]
                db.execute("UPDATE deck SET shown_at=NULL")
            if cursor != deck["cursor"] or deck["shown_at"] is None:
                db.execute("UPDATE deck SET cursor=?, shown_at=?", (cursor, now))
            return {**self._row(db, cursor), "id": cursor, "held": False}

    def navigate(self, step, now):
        with self.lock, self._db() as db:
            live = self._live(db, now)
            deck = db.execute("SELECT * FROM deck").fetchone()
            db.execute("UPDATE deck SET hold_id=NULL, hold_until=NULL")
            if not live:
                return None
            here = deck["hold_id"] if deck["hold_id"] in live and (deck["hold_until"] or 0) > now else deck["cursor"]
            index = live.index(here) if here in live else 0
            target = live[(index + step) % len(live)]
            db.execute("UPDATE deck SET cursor=?, shown_at=?, revision=revision+1", (target, now))
        return target

    def deck_revision(self):
        with self._db() as db:
            return db.execute("SELECT revision FROM deck").fetchone()[0]

    # ---- devices ----------------------------------------------------------
    def seen(self, device, now, wake, battery=None, rssi=None, fw=None):
        with self.lock, self._db() as db:
            db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?) ON CONFLICT(device) DO UPDATE SET "
                       "last_seen=excluded.last_seen, last_wake=excluded.last_wake, battery=excluded.battery, "
                       "rssi=excluded.rssi, fw=excluded.fw", (device, now, wake, battery, rssi, fw))

    def status(self, now):
        with self._db() as db:
            deck = dict(db.execute("SELECT * FROM deck").fetchone())
            devices = {r["device"]: dict(r) | {"age_s": round(now - r["last_seen"])}
                       for r in db.execute("SELECT * FROM devices")}
            counts = db.execute("SELECT COUNT(*), SUM(forward='pending') FROM events").fetchone()
        hold = deck["hold_id"] if deck["hold_id"] and (deck["hold_until"] or 0) > now else None
        return {"cursor": deck["cursor"], "hold": hold, "devices": devices,
                "events": counts[0], "pending_forwards": counts[1] or 0, "dwell_s": self.dwell_s}

    # ---- events -----------------------------------------------------------
    def record_events(self, batch, now, action_labels=None, contexts=None, local_only=False):
        """Insert events idempotently on (device, boot, seq); returns the ones that were new."""
        new = []
        with self.lock, self._db() as db:
            for ev in batch["events"]:
                label = None
                if not local_only and ev["card"] and ev["button"] in ASSIGNABLE:
                    key = ev["button"] + ("_long" if ev["press"] == "long" and ev["button"] == "confirm" else "")
                    if ev['card'] in (action_labels or {}):
                        label = action_labels[ev['card']].get(key)
                    else:
                        row = db.execute("SELECT body FROM slides WHERE id=?", (ev["card"],)).fetchone()
                        label = json.loads(row["body"])["actions"].get(key) if row else None
                cur = db.execute("INSERT OR IGNORE INTO events (device, boot, seq, card, etag, button, press, wake, "
                                 "received_at, label, forward) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (batch["device"], batch["boot"], ev["seq"], ev["card"], ev["etag"], ev["button"],
                                  ev["press"], ev["wake"], now, label, "pending" if label else "local"))
                if cur.rowcount:
                    if label and ev['seq'] in (contexts or {}):
                        db.execute('INSERT INTO event_context VALUES (?,?,?,?)',
                            (batch['device'],batch['boot'],ev['seq'],contexts[ev['seq']]))
                    new.append(ev)
        return new

    def assign_forward(self, device, boot, seq, label, context):
        """Mark an already-recorded local event for Muse once its meaning is known."""
        with self.lock, self._db() as db:
            changed = db.execute("UPDATE events SET label=?, forward='pending' WHERE device=? AND boot=? AND seq=? "
                                 "AND forward='local'", (label, device, boot, seq)).rowcount
            if changed:
                db.execute("INSERT OR REPLACE INTO event_context VALUES (?,?,?,?)", (device, boot, seq, context))
            return bool(changed)

    def acked(self, device, boot):
        with self._db() as db:
            row = db.execute("SELECT MAX(seq) FROM events WHERE device=? AND boot=?", (device, boot)).fetchone()
        return row[0] or 0

    def events(self, limit):
        with self._db() as db:
            rows = db.execute("SELECT * FROM events ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def pending_forwards(self):
        with self._db() as db:
            rows = db.execute("SELECT e.*, c.body AS context FROM events e LEFT JOIN event_context c "
                "ON e.device=c.device AND e.boot=c.boot AND e.seq=c.seq "
                "WHERE e.forward='pending' ORDER BY e.rowid").fetchall()
        return [dict(r) for r in rows]

    def mark_forwarded(self, rowid, ok):
        with self.lock, self._db() as db:
            db.execute("UPDATE events SET forward=? WHERE rowid=?", ("sent" if ok else "pending", rowid))


class _Tx:
    """Context manager: one connection, one IMMEDIATE transaction, always closed."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, kind, value, tb):
        with closing(self.db):
            self.db.execute("COMMIT" if kind is None else "ROLLBACK")
