"""Canonical, bounded contract for X4 slides and device events.

Same shape and rules as clock-control's slide protocol (stable ids, owner/kind,
expiry, stale_after_s, rotate/next/hold, literal `lines`, 32x32 avatars), with
800x480 limits and one addition: per-slide button `actions`.
"""
import datetime as _dt
import re
import unicodedata

MAX_REQUEST = 16384
KINDS = frozenset(("weather", "upcoming", "household", "project", "presence", "discovery",
                   "result", "attention", "note", "art"))
INTENTS = frozenset(("rotate", "next", "hold"))
# Left/Right page the deck inside x4d; Power is device-local. Everything else a card may claim.
ASSIGNABLE = ("confirm", "confirm_long", "back", "up", "down")
BUTTONS = frozenset(("back", "confirm", "left", "right", "up", "down", "power"))
PRESSES = frozenset(("short", "long"))
WAKES = frozenset(("timer", "button", "boot", "session"))
LIMITS = {"title_chars": 32, "footer_chars": 64, "body_lines": 12, "body_chars": 900, "action_label_chars": 12}
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,47}$")
_OWNER = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,23}$")
_DEVICE = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
BUILTIN_AVATARS = ("none", "alice", "pyrrha", "muse")


def _string(value, label, *, empty=False, single_line=True, limit=None):
    if type(value) is not str:
        raise ValueError(f"{label} must be a string.")
    for ch in value:
        if (ch != "\n" and unicodedata.category(ch) == "Cc") or (single_line and ch == "\n"):
            raise ValueError(f"{label} contains a control character or line break.")
    if not empty and not value.strip():
        raise ValueError(f"{label} must not be empty.")
    if limit is not None and len(value) > limit:
        raise ValueError(f"{label} is limited to {limit} characters.")
    return value


def _int(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}.")
    return value


def _exact(obj, allowed, label, required=()):
    if type(obj) is not dict:
        raise ValueError(f"{label} must be an object.")
    unknown = set(obj) - set(allowed)
    if unknown:
        raise ValueError(f"{label} has unknown field(s): {', '.join(sorted(unknown))}.")
    missing = [k for k in required if k not in obj]
    if missing:
        raise ValueError(f"{label} requires: {', '.join(missing)}.")


def _ident(value, label="id", pattern=_ID):
    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f"{label} must match {pattern.pattern}.")
    return value


def _avatar(value):
    if type(value) is str:
        if value not in BUILTIN_AVATARS:
            raise ValueError("avatar must be none, alice, pyrrha, muse or {\"rows\": [...]}.")
        return value
    _exact(value, {"rows"}, "avatar", required=("rows",))
    rows = value["rows"]
    if type(rows) is not list or len(rows) != 32 or any(
            type(r) is not str or len(r) != 32 or set(r) - {"0", "1"} for r in rows):
        raise ValueError("avatar rows must be 32 strings of 32 binary digits (1 = black).")
    return {"rows": list(rows)}


def _frame(frame):
    _exact(frame, {"action", "title", "text", "lines", "footer", "avatar"}, "frame")
    if frame.get("action") != "card":
        raise ValueError("frame action must be card.")
    if ("text" in frame) == ("lines" in frame):
        raise ValueError("frame needs exactly one of text or lines.")
    out = {"action": "card",
           "title": _string(frame.get("title", "NOTE"), "title", empty=True, limit=LIMITS["title_chars"]),
           "footer": _string(frame.get("footer", ""), "footer", empty=True, limit=LIMITS["footer_chars"]),
           "avatar": _avatar(frame.get("avatar", "none"))}
    if "text" in frame:
        # Repair legacy CRLF and one literal two-character "\n" separator, idempotently.
        text = _string(frame["text"], "text", single_line=False).replace("\r\n", "\n").replace("\\n", "\n")
        lines = text.split("\n")
        out["text"] = text
    else:
        lines = frame["lines"]
        if type(lines) is not list:
            raise ValueError("lines must be a list of strings.")
        lines = [_string(line, "line", empty=True) for line in lines]
        if not "".join(lines).strip():
            raise ValueError("lines must contain some text.")
        out["lines"] = lines
    if not 1 <= len(lines) <= LIMITS["body_lines"] or len("\n".join(lines)) > LIMITS["body_chars"]:
        raise ValueError(f"body is limited to {LIMITS['body_lines']} lines and {LIMITS['body_chars']} characters.")
    return out


def _expiry(value):
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("expires_at must be null or an ISO8601 timestamp with a timezone.")
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at is not a valid ISO8601 timestamp.") from exc
    if parsed.utcoffset() is None:
        raise ValueError("expires_at must include a timezone.")
    return parsed.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _actions(value):
    if type(value) is not dict:
        raise ValueError("actions must be an object of button -> label.")
    out = {}
    for button, label in value.items():
        if button not in ASSIGNABLE:
            raise ValueError(f"actions may only assign {', '.join(ASSIGNABLE)} (left/right page the deck).")
        out[button] = _string(label, f"action label for {button}", limit=LIMITS["action_label_chars"])
    return out


def _slide_put(request):
    _exact(request, {"action", "slide", "intent", "hold_s"}, "slide_put", required=("slide",))
    src = request["slide"]
    _exact(src, {"id", "owner", "kind", "frame", "expires_at", "stale_after_s", "actions"}, "slide",
           required=("id", "frame"))
    kind = src.get("kind", "note")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(sorted(KINDS))}.")
    slide = {"id": _ident(src["id"]), "owner": _ident(src.get("owner", "shared"), "owner", _OWNER),
             "kind": kind, "frame": _frame(src["frame"]), "expires_at": _expiry(src.get("expires_at")),
             "stale_after_s": _int(src.get("stale_after_s", 3600), "stale_after_s", 180, 604800),
             "actions": _actions(src.get("actions", {}))}
    intent = request.get("intent", "rotate")
    if intent not in INTENTS:
        raise ValueError("intent must be rotate, next or hold.")
    out = {"action": "slide_put", "slide": slide, "intent": intent}
    if intent == "hold":
        out["hold_s"] = _int(request.get("hold_s", 900), "hold_s", 180, 3600)
    elif "hold_s" in request:
        raise ValueError("hold_s is only valid with intent hold.")
    return out


def validate(request):
    """Return a fresh canonical request (validation is idempotent) or raise ValueError."""
    if type(request) is not dict:
        raise ValueError("request must be an object.")
    action = request.get("action")
    if action == 'glance':
        _exact(request, {'action','op','device'}, action, required=('op',))
        if request['op'] not in ('status','refresh','home','weather','agenda'):
            raise ValueError('unknown glance operation')
        return {'action':'glance','op':request['op'],
                'device':_ident(request.get('device','x4-01'),'device',_DEVICE)}
    if action == "record_put":
        from x4_records import validate_record
        _exact(request, {"action", "record"}, action, required=("record",))
        return {"action": action, "record": validate_record(request["record"])}
    if action in ("record_get", "record_remove"):
        _exact(request, {"action", "id"}, action, required=("id",))
        return {"action": action, "id": _ident(request["id"])}
    if action == "records":
        _exact(request, {"action", "agent"}, action)
        agent = request.get("agent")
        return {"action": action, "agent": None if agent is None else _ident(agent, "agent", _OWNER)}
    if action == "slide_put":
        return _slide_put(request)
    if action in ("slide_get", "slide_remove"):
        _exact(request, {"action", "id"}, action, required=("id",))
        return {"action": action, "id": _ident(request["id"])}
    if action == "events":
        _exact(request, {"action", "limit"}, action)
        return {"action": action, "limit": _int(request.get("limit", 20), "limit", 1, 100)}
    if action in ("slides", "status", "capabilities"):
        _exact(request, {"action"}, action)
        return {"action": action}
    raise ValueError("action must be glance, record_put, record_get, record_remove, records, slide_put, "
                     "slide_get, slide_remove, slides, events, status or capabilities.")


def validate_events(body):
    """Validate one device event batch (POST /x4/v1/events)."""
    _exact(body, {"device", "boot", "events"}, "event batch", required=("device", "boot", "events"))
    events = body["events"]
    if type(events) is not list or len(events) > 16:
        raise ValueError("events must be a list of at most 16 items.")
    out = []
    for ev in events:
        _exact(ev, {"seq", "card", "etag", "button", "press", "wake"}, "event",
               required=("seq", "button", "press"))
        card, etag = ev.get("card"), ev.get("etag")
        if ev["button"] not in BUTTONS or ev["press"] not in PRESSES or ev.get("wake", "button") not in WAKES:
            raise ValueError("event button/press/wake is not recognised.")
        out.append({"seq": _int(ev["seq"], "seq", 1, 2**31 - 1),
                    "card": None if card is None else _ident(card, "card"),
                    "etag": None if etag is None else _string(etag, "etag", limit=64),
                    "button": ev["button"], "press": ev["press"], "wake": ev.get("wake", "button")})
    return {"device": _ident(body["device"], "device", _DEVICE),
            "boot": _int(body["boot"], "boot", 0, 2**31 - 1), "events": out}


def capabilities():
    return {
        "protocol": "x4/1",
        "geometry": {"width": 800, "height": 480, "mode": "1"},
        "limits": dict(LIMITS, max_request_bytes=MAX_REQUEST, avatar_px=32, slides=64),
        "kinds": sorted(KINDS),
        "avatars": {"builtins": list(BUILTIN_AVATARS), "custom": "rows: 32 x 32 of 0/1, 1 = black"},
        "buttons": {"assignable": list(ASSIGNABLE), "navigation": ["left", "right"], "device_local": ["power"]},
        "intents": sorted(INTENTS),
        "delivery": {"model": "pull", "shown": "next_wake", "min_poll_s": 300, "max_poll_s": 21600,
                     "note": "The X4 is asleep or powered off most of the time. Accepted means stored; "
                             "it appears when the device next wakes and fetches."},
        "records": {"kinds": ["decision", "report"], "verbs": ["record_put", "record_get", "record_remove", "records"],
                    "note": "Records never displace the dashboard; Cassie opens them from Agents/Reports. "
                            "Answers apply only to the revision she saw; poll record_get for the result."},
        "events": {"forwarded": "presses of a button the card assigned in actions",
                   "local": "left/right page the deck and are not forwarded"},
    }
