"""Fixed, least-privilege Elsie publisher policy; never human approval authority."""
import hashlib
import re

import x4_protocol

AGENT = "elsie"
PREFIX = "elsie."
TOKEN_KEY = "elsie_publisher_token_sha256"
ACTIONS = ("record_put", "record_get", "record_remove", "records")


def validate_config(cfg):
    if TOKEN_KEY not in cfg:
        return  # absent means disabled, not a fallback to the broad credential
    digest = cfg[TOKEN_KEY]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid Elsie publisher credential digest")
    if (digest == hashlib.sha256(b"").hexdigest() or digest == cfg.get("agent_token_sha256")
            or digest in cfg.get("devices", {}).values()):
        raise ValueError("Elsie publisher credential must be independent")


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("invalid publisher request")
    if request.get("action") not in ACTIONS:
        raise PermissionError("publisher scope required")
    try:
        req = x4_protocol.validate(request)
    except (ValueError, TypeError, KeyError, OverflowError):
        # Validators can include unknown field names, which are untrusted/private data.
        raise ValueError("invalid publisher request") from None
    if req["action"] == "records":
        if req["agent"] not in (None, AGENT):
            raise PermissionError("publisher scope required")
        req["agent"] = AGENT
    else:
        body = req["record"] if req["action"] == "record_put" else req
        if not body["id"].startswith(PREFIX) or ("agent" in body and body["agent"] != AGENT):
            raise PermissionError("publisher scope required")
    return req


def owns(entry):
    return (isinstance(entry, dict) and isinstance(entry.get("id"), str) and entry["id"].startswith(PREFIX)
            and entry.get("agent") == AGENT and entry.get("kind") in ("report", "decision"))


def dispatch(app, request, now):
    req = validate_request(request)
    # Check and mutation share the same RLock used by broad-agent record writes.
    # A caller cannot take over another author's pre-existing id inside elsie.*.
    with app.store.lock:
        if req["action"] == "records":
            out = {"result": [r for r in app.records.list(now, agent=AGENT) if owns(r)]}
        else:
            ident = req["record"]["id"] if req["action"] == "record_put" else req["id"]
            existing = app.records.get(ident)
            if existing is not None and not owns(existing):
                if req["action"] == "record_put":
                    raise PermissionError("publisher scope required")
                # Reading/deleting a foreign record is indistinguishable from missing.
                out = {"result": None if req["action"] == "record_get" else {"removed": False}}
            else:
                out = app.agent(req, now)
    return dict(out, principal=AGENT)
