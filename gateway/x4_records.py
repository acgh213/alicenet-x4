"""Typed agent records for the X4: decisions Cassie answers and reports she reads.

Records are not slides. They never displace the ambient dashboard; Cassie reaches them
through the Agents/Reports destinations. Every answer names the revision that was on
screen, so an agent revising a decision can never have an old choice applied to it.
"""
import datetime as dt
import json
import re

from x4_protocol import _exact, _ident, _string, _OWNER, _expiry

KINDS = ("decision", "report")
DEFAULT_OPTIONS = ("approve", "reject", "defer")
NOTIFY = ("none", "badge")
LIMITS = {"title": 80, "summary": 600, "sections": 12, "heading": 40, "body": 1200,
          "options": 4, "option": 12}
_OPTION = re.compile(r"^[a-z][a-z0-9-]{0,11}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, kind TEXT NOT NULL, agent TEXT NOT NULL,
  body TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL, answer TEXT,
  answered_revision INTEGER, answered_at REAL, answer_source TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL);
"""


def _text(value, label, limit):
    # Agents often send a literal two-character "\\n"; treat it as the line break they meant.
    text = _string(value, label, single_line=False, limit=limit).replace("\r\n", "\n").replace("\\n", "\n")
    return text.strip()


def validate_record(body):
    _exact(body, {"id", "kind", "agent", "title", "summary", "sections", "options",
                  "recommendation", "expires_at", "notify"}, "record",
           required=("id", "kind", "agent", "title", "summary"))
    kind = body["kind"]
    if kind not in KINDS:
        raise ValueError("record kind must be decision or report.")
    out = {"id": _ident(body["id"]), "kind": kind, "agent": _ident(body["agent"], "agent", _OWNER),
           "title": _string(body["title"], "title", limit=LIMITS["title"]),
           "summary": _text(body["summary"], "summary", LIMITS["summary"]),
           "expires_at": _expiry(body.get("expires_at")), "notify": body.get("notify", "none")}
    if out["notify"] not in NOTIFY:
        raise ValueError("notify must be none or badge.")
    sections = body.get("sections", [])
    if type(sections) is not list or len(sections) > LIMITS["sections"]:
        raise ValueError(f"sections must be a list of at most {LIMITS['sections']}.")
    out["sections"] = []
    for section in sections:
        _exact(section, {"heading", "body"}, "section", required=("heading", "body"))
        out["sections"].append({"heading": _string(section["heading"], "heading", limit=LIMITS["heading"]),
                                "body": _text(section["body"], "section body", LIMITS["body"])})
    if kind == "report":
        if "options" in body or "recommendation" in body:
            raise ValueError("reports do not take options or a recommendation.")
        return out
    options = body.get("options", list(DEFAULT_OPTIONS))
    if (type(options) is not list or not 2 <= len(options) <= LIMITS["options"]
            or len(set(options)) != len(options)
            or any(type(o) is not str or not _OPTION.fullmatch(o) for o in options)):
        raise ValueError("options must be 2-4 distinct lowercase words of up to 12 characters.")
    out["options"] = list(options)
    recommendation = body.get("recommendation")
    if recommendation is not None and recommendation not in options:
        raise ValueError("recommendation must be one of the options.")
    out["recommendation"] = recommendation
    return out


def from_markdown(text):
    """'# Title', a summary paragraph, then '## Heading' sections -> partial record fields.

    Missing pieces come back empty; the caller decides whether flags fill them. Deeper
    headings and lists stay as text: the panel shows them literally, which reads fine.
    """
    title, summary, sections, current = "", [], [], None
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            current = {"heading": line[3:].strip(), "body": []}
            sections.append(current)
        elif line.startswith("# ") and not title and current is None and not "".join(summary).strip():
            title = line[2:].strip()
        else:
            (current["body"] if current is not None else summary).append(line.rstrip())
    tidy = lambda lines: re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return {"title": title, "summary": tidy(summary),
            "sections": [{"heading": s["heading"], "body": tidy(s["body"])} for s in sections]}


def _expired(record, now):
    exp = record.get("expires_at")
    return exp is not None and now >= dt.datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp()


class Records:
    def __init__(self, store):
        self.store = store
        with store.lock, store._db() as db:
            db.execute(SCHEMA)

    @staticmethod
    def _row(row):
        if row is None:
            return None
        return {"id": row["id"], "kind": row["kind"], "agent": row["agent"], "record": json.loads(row["body"]),
                "revision": row["revision"], "status": row["status"], "answer": row["answer"],
                "answered_revision": row["answered_revision"], "answered_at": row["answered_at"],
                "updated_at": row["updated_at"]}

    def put(self, record, now):
        body = json.dumps(record, sort_keys=True, ensure_ascii=False)
        fresh = "open" if record["kind"] == "decision" else "unread"
        with self.store.lock, self.store._db() as db:
            row = db.execute("SELECT body FROM records WHERE id=?", (record["id"],)).fetchone()
            if row is None:
                db.execute("INSERT INTO records (id,kind,agent,body,revision,status,created_at,updated_at) "
                           "VALUES (?,?,?,?,1,?,?,?)",
                           (record["id"], record["kind"], record["agent"], body, fresh, now, now))
            elif row["body"] != body:
                # A revised record is a new question: earlier answers do not carry over.
                db.execute("UPDATE records SET kind=?, agent=?, body=?, revision=revision+1, status=?, answer=NULL, "
                           "answered_revision=NULL, answered_at=NULL, answer_source=NULL, updated_at=? WHERE id=?",
                           (record["kind"], record["agent"], body, fresh, now, record["id"]))
            return self._row(db.execute("SELECT * FROM records WHERE id=?", (record["id"],)).fetchone())

    def get(self, ident):
        with self.store._db() as db:
            return self._row(db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone())

    def remove(self, ident):
        with self.store.lock, self.store._db() as db:
            return db.execute("DELETE FROM records WHERE id=?", (ident,)).rowcount > 0

    def list(self, now, agent=None, kind=None):
        with self.store._db() as db:
            rows = db.execute("SELECT * FROM records ORDER BY created_at, id").fetchall()
        out = [self._row(r) for r in rows]
        out = [r for r in out if not _expired(r["record"], now)
               and (agent is None or r["agent"] == agent) and (kind is None or r["kind"] == kind)]
        # What needs Cassie comes first: open decisions, unread reports, then the rest.
        rank = {"open": 0, "unread": 1}
        return sorted(out, key=lambda r: (rank.get(r["status"], 2), 0 if r["kind"] == "decision" else 1))

    def agents(self, now):
        summary = {}
        for r in self.list(now):
            row = summary.setdefault(r["agent"], {"agent": r["agent"], "decisions": 0, "reports": 0, "total": 0})
            row["total"] += 1
            if r["status"] == "open":
                row["decisions"] += 1
            elif r["status"] == "unread":
                row["reports"] += 1
        return sorted(summary.values(), key=lambda row: (-(row["decisions"] + row["reports"]), row["agent"]))

    def answer(self, ident, revision, choice, now, source):
        with self.store.lock, self.store._db() as db:
            row = db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
            if row is None:
                return {"outcome": "missing"}
            record = json.loads(row["body"])
            if row["kind"] != "decision" or choice not in record["options"]:
                return {"outcome": "invalid"}
            if row["revision"] != revision:
                return {"outcome": "stale", "current_revision": row["revision"]}
            if row["status"] == "answered":
                return {"outcome": "already_answered", "answer": row["answer"]}
            db.execute("UPDATE records SET status='answered', answer=?, answered_revision=?, answered_at=?, "
                       "answer_source=? WHERE id=?", (choice, revision, now, source, ident))
            return {"outcome": "answered", "answer": choice, "revision": revision}

    def ack(self, ident, revision, now):
        with self.store.lock, self.store._db() as db:
            row = db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
            if row is None:
                return {"outcome": "missing"}
            if row["kind"] != "report":
                return {"outcome": "invalid"}
            if row["revision"] != revision:
                return {"outcome": "stale", "current_revision": row["revision"]}
            db.execute("UPDATE records SET status='read', answered_at=? WHERE id=?", (now, ident))
            return {"outcome": "read", "revision": revision}
