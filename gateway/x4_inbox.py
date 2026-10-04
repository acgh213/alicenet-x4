"""Opt-in agent-published Inbox reports. No Telegram/network/history reader.

Every read and write loads current exact source/chat/thread consent. A grant must
be replaced on re-authorization; revoked rows are purged on the next operation.
Content never enters Records, the ambient badge, or the forwarding queue.
"""
import datetime as dt
import hashlib
import json
import sqlite3
from pathlib import Path

from x4_protocol import _exact, _expiry, _int, _string

MAX_POLICY = 16384
MAX_MESSAGES = 128
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS inbox_messages (key TEXT PRIMARY KEY, scope TEXT NOT NULL, "
    "body TEXT NOT NULL, received_at REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS inbox_sources (scope TEXT PRIMARY KEY, state TEXT NOT NULL, "
    "observed_at REAL NOT NULL)",
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _scope(body):
    return tuple(_string(body[k], k, limit=128) for k in ("source", "chat", "thread"))


def validate_config(body):
    _exact(body, {"scopes", "stale_after_s"}, "inbox policy", required=("scopes",))
    scopes = body["scopes"]
    if type(scopes) is not list or len(scopes) > 32:
        raise ValueError("scopes must be a list of at most 32 exact scopes")
    out, seen = [], set()
    for scope in scopes:
        _exact(scope, {"source", "chat", "thread", "label", "grant"}, "scope",
               required=("source", "chat", "thread", "label", "grant"))
        key = _scope(scope)
        if key in seen:
            raise ValueError("duplicate scope")
        seen.add(key)
        out.append(dict(zip(("source", "chat", "thread"), key),
                        label=_string(scope["label"], "label", limit=40),
                        grant=_string(scope["grant"], "grant", limit=128)))
    return {"scopes": out, "stale_after_s": _int(body.get("stale_after_s", 3600),
                                                "stale_after_s", 60, 604800)}


def validate_message(body, now):
    _exact(body, {"source", "chat", "thread", "id", "time", "title", "text"}, "inbox message",
           required=("source", "chat", "thread", "id", "time", "title", "text"))
    key = _scope(body)
    timestamp = _expiry(body["time"])
    if timestamp is None or dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp() > now + 300:
        raise ValueError("message time must be a non-future timestamp")
    return dict(zip(("source", "chat", "thread"), key),
                id=_string(body["id"], "id", limit=128), time=timestamp,
                title=_string(body["title"], "title", limit=80),
                text=_string(body["text"], "text", single_line=False, limit=12000))


class Inbox:
    def __init__(self, store, config_path=None):
        self.store = store
        self.config_path = Path(config_path) if config_path else None
        with store.lock, store._db() as db:
            for statement in SCHEMA:
                db.execute(statement)

    def _policy(self):
        if self.config_path is None:
            return None, "not_configured"
        try:
            with self.config_path.open("rb") as fh:
                raw = fh.read(MAX_POLICY + 1)
            if len(raw) > MAX_POLICY:
                raise ValueError("policy too large")
            cfg = validate_config(json.loads(raw))
            return cfg, "ok" if cfg["scopes"] else "not_configured"
        except FileNotFoundError:
            return None, "not_configured"
        except (OSError, ValueError, TypeError, RecursionError):
            return None, "error"

    def policy_key(self):
        cfg, state = self._policy()
        return fingerprint([cfg, state])

    def _fence(self, cfg, state):
        if self.policy_key() != fingerprint([cfg, state]):
            raise PermissionError("Inbox consent changed during operation")

    @staticmethod
    def _prune(db, cfg):
        allowed = {fingerprint(s) for s in cfg["scopes"]} if cfg else set()
        for table in ("inbox_messages", "inbox_sources"):
            for row in db.execute("SELECT DISTINCT scope FROM " + table).fetchall():
                if row["scope"] not in allowed:
                    db.execute("DELETE FROM " + table + " WHERE scope=?", (row["scope"],))

    @staticmethod
    def _authorized(cfg, body):
        match = next((s for s in (cfg or {}).get("scopes", []) if _scope(s) == _scope(body)), None)
        if match is None:
            raise PermissionError("Inbox scope not authorized")
        return fingerprint(match)

    def _sync_policy(self):
        # Revocation cleanup must commit even when the subsequent request is
        # denied/malformed; raising inside that transaction would undo the purge.
        with self.store.lock, self.store._db() as db:
            cfg, _ = self._policy()
            self._prune(db, cfg)

    def put(self, body, now):
        self._sync_policy()
        message = validate_message(body, now)
        with self.store.lock, self.store._db() as db:
            cfg, state = self._policy()
            self._prune(db, cfg)
            scope = self._authorized(cfg, message)
            key = fingerprint([scope, message["id"]])
            raw = json.dumps(message, sort_keys=True, ensure_ascii=False)
            row = db.execute("SELECT body,received_at FROM inbox_messages WHERE key=?", (key,)).fetchone()
            if row:
                if row["body"] != raw:
                    raise ValueError("Inbox stable id conflict")
                self._fence(cfg, state)
                return {"key": key, "received_at": row["received_at"]}
            if db.execute("SELECT COUNT(*) FROM inbox_messages").fetchone()[0] >= MAX_MESSAGES:
                raise ValueError("Inbox cache full; remove a report first")
            db.execute("INSERT INTO inbox_messages VALUES (?,?,?,?)", (key, scope, raw, now))
            db.execute("INSERT INTO inbox_sources VALUES (?,'ok',?) ON CONFLICT(scope) "
                       "DO UPDATE SET state='ok',observed_at=excluded.observed_at", (scope, now))
            self._fence(cfg, state)
            return {"key": key, "received_at": now}

    def set_status(self, body, now):
        self._sync_policy()
        _exact(body, {"source", "chat", "thread", "state"}, "inbox status",
               required=("source", "chat", "thread", "state"))
        _scope(body)
        if body["state"] not in ("ok", "error", "unavailable"):
            raise ValueError("invalid Inbox source state")
        with self.store.lock, self.store._db() as db:
            cfg, policy_state = self._policy()
            self._prune(db, cfg)
            scope = self._authorized(cfg, body)
            db.execute("INSERT INTO inbox_sources VALUES (?,?,?) ON CONFLICT(scope) "
                       "DO UPDATE SET state=excluded.state,observed_at=excluded.observed_at",
                       (scope, body["state"], now))
            self._fence(cfg, policy_state)
        return {"state": body["state"]}

    def remove(self, key, now):
        # Re-project consent before even the removal API can reveal existence.
        with self.store.lock, self.store._db() as db:
            cfg, _ = self._policy()
            self._prune(db, cfg)
            return db.execute("DELETE FROM inbox_messages WHERE key=?", (key,)).rowcount > 0

    def snapshot(self, now):
        try:
            with self.store.lock, self.store._db() as db:
                cfg, state = self._policy()
                self._prune(db, cfg)
                if state != "ok" or cfg is None:
                    return {"state": state, "items": [], "sources": [], "policy_key": fingerprint([cfg, state])}
                scopes = {fingerprint(s): s for s in cfg["scopes"]}
                sources = []
                for key, scope in scopes.items():
                    row = db.execute("SELECT * FROM inbox_sources WHERE scope=?", (key,)).fetchone()
                    status = row["state"] if row else "unavailable"
                    if status == "ok" and now - row["observed_at"] > cfg["stale_after_s"]:
                        status = "stale"
                    sources.append({"label": scope["label"], "state": status,
                                    "observed_at": row["observed_at"] if row else None})
                items = []
                for row in db.execute("SELECT * FROM inbox_messages ORDER BY received_at DESC,key").fetchall():
                    msg = validate_message(json.loads(row["body"]), now)
                    # Reuse report pagination shape, but not the decision/ack/forward semantics.
                    text = msg["text"]
                    record = {"title": msg["title"], "summary": text[:600],
                              "sections": [{"heading": "Text (continued)", "body": text[n:n + 1200]}
                                           for n in range(600, len(text), 1200)]}
                    items.append({"key": row["key"], "record": record, "source": scopes[row["scope"]]["label"],
                                  "time": msg["time"], "received_at": row["received_at"],
                                  "stale": now - row["received_at"] > cfg["stale_after_s"],
                                  "revision": fingerprint(msg)})
                states = {s["state"] for s in sources}
                overall = next((s for s in ("error", "unavailable", "stale") if s in states), "ok")
                self._fence(cfg, state)
                return {"state": overall, "items": items, "sources": sources,
                        "policy_key": fingerprint([cfg, state])}
        except (sqlite3.Error, ValueError, TypeError, KeyError, RecursionError, PermissionError):
            return {"state": "error", "items": [], "sources": []}
