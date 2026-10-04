#!/usr/bin/env python3
"""x4ctl: the agent-facing CLI for X4 endpoints, shaped like clockctl.

Same verbs as clockctl (slide, slides, slide-get, slide-remove, status, capabilities,
request, preview) plus `events` and per-card `--action BUTTON=LABEL`. Output is one JSON
object on stdout (exit 0) or {"ok": false, "error": ...} on stderr (exit 1).

Agent records (Cassie opens them from Agents/Reports; they never take over the panel):
  decide / report   publish from flags or --markdown FILE|- ('# Title', summary, '## Sections')
  records, record-get, record-remove
  wait ID           block until answered/read: exit 0; timeout, expired or revised: exit 2

Body text: pass TEXT (a literal "\\n" becomes a line break), --stdin, or repeated --line
(taken literally, never escape-decoded). Publishing is honest about latency:
"delivery": "next_wake" means stored now, shown when the X4 next wakes.

Config: --config PATH, else $X4CTL_CONFIG, else ~/.config/x4ctl/config.json:
  {"gateway": "http://192.168.18.61:8787", "agent_token": "..."}
For restricted own-record publishing, mode0600 config uses publisher_token instead
of agent_token; the endpoint is /x4/v1/publisher, never a broad-token fallback.
"""
import argparse
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

import x4_protocol
import x4_records
import x4_render

MAX_STDIN = 8192
MAX_MARKDOWN = 32768
MAX_AVATAR_BYTES = 1024 * 1024
MAX_PUBLISHER_RESPONSE = 2 * 1024 * 1024


class CLIError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise CLIError(message)


def avatar_value(raw):
    """none/builtin name, or a local PNG/JPEG converted to 32x32 rows (1 = black)."""
    if raw in x4_protocol.BUILTIN_AVATARS:
        return raw
    if "://" in raw:
        raise CLIError("avatar must be a builtin name or a local image path.")
    path = Path(raw)
    if not path.is_file() or path.stat().st_size > MAX_AVATAR_BYTES:
        raise CLIError(f"avatar {raw!r} is not a readable local image under 1 MB.")
    with Image.open(path) as img:
        img.load()
        if img.width * img.height > 4_000_000:
            raise CLIError("avatar image is too large.")
        gray = img.convert("RGBA")
        flat = Image.new("RGBA", gray.size, (255, 255, 255, 255))
        flat.alpha_composite(gray)  # transparent -> white
        small = flat.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    return {"rows": ["".join("1" if small.getpixel((x, y)) < 128 else "0" for x in range(32))
                     for y in range(32)]}


def _stdin_text():
    data = sys.stdin.read(MAX_STDIN + 1)
    if len(data.encode("utf-8")) > MAX_STDIN:
        raise CLIError(f"stdin exceeds {MAX_STDIN} bytes.")
    return data


def _stdin_json():
    text = _stdin_text()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CLIError(f"invalid JSON on stdin: {exc}") from exc
    if not isinstance(value, dict):
        raise CLIError("JSON request must be an object.")
    return value


def _actions(pairs):
    out = {}
    for pair in pairs or []:
        button, sep, label = pair.partition("=")
        if not sep:
            raise CLIError(f"--action expects BUTTON=LABEL, got {pair!r}.")
        out[button] = label
    return out


def _parser():
    p = _Parser(prog="x4ctl", description="Publish cards to X4 endpoints through x4d.")
    p.add_argument("--config")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("slide", help="Publish or update one card.")
    s.add_argument("--id", required=True)
    s.add_argument("--owner", default="shared")
    s.add_argument("--kind", default="note")
    s.add_argument("--title")
    s.add_argument("--footer", default="")
    s.add_argument("--avatar", default="none", help="none, alice, pyrrha, muse, or a local PNG/JPEG.")
    s.add_argument("--action", action="append", metavar="BUTTON=LABEL",
                   help="Assign confirm, confirm_long, back, up or down. Repeatable.")
    s.add_argument("--intent", choices=("rotate", "next", "hold"), default="rotate")
    s.add_argument("--hold-s", type=int)
    s.add_argument("--expires-at")
    s.add_argument("--stale-after-s", type=int, default=3600)
    s.add_argument("--stdin", action="store_true", help="Read the body from stdin.")
    s.add_argument("--line", action="append", help="One literal body line. Repeatable.")
    s.add_argument("text", nargs="?")
    for name in ('home','weather','agenda','refresh','glance'):
        sub.add_parser(name, help='Control the built-in glance dashboard.').add_argument('--device',default='x4-01')
    for kind in ("decide", "report"):
        r = sub.add_parser(kind, help=f"Publish a {'decision' if kind == 'decide' else 'report'} record.")
        r.add_argument("--id", required=True)
        r.add_argument("--agent", required=True)
        r.add_argument("--title")
        r.add_argument("--summary")
        r.add_argument("--section", action="append", metavar="HEADING=BODY", help="Repeatable; after markdown ones.")
        r.add_argument("--markdown", metavar="FILE", help="'# Title', summary paragraph, '## Heading' sections; - = stdin.")
        r.add_argument("--expires-at")
        if kind == "decide":
            r.add_argument("--option", action="append", help="2-4 lowercase words. Default approve/reject/defer.")
            r.add_argument("--recommend")
    sub.add_parser("records").add_argument("--agent")
    sub.add_parser("record-get").add_argument("id")
    sub.add_parser("record-remove").add_argument("id")
    w = sub.add_parser("wait", help="Block until Cassie answers or reads a record.")
    w.add_argument("id")
    w.add_argument("--revision", type=int, help="Stop with outcome=revised if the record moves past this.")
    w.add_argument("--timeout", type=float, default=600)
    w.add_argument("--interval", type=float, default=15)
    sub.add_parser("slides")
    sub.add_parser("slide-get").add_argument("id")
    sub.add_parser("slide-remove").add_argument("id")
    sub.add_parser("events").add_argument("--limit", type=int, default=20)
    for name in ("status", "capabilities", "request"):
        sub.add_parser(name)
    sub.add_parser("preview", help="Render a JSON slide_put from stdin locally (no gateway).").add_argument(
        "--output", required=True)
    return p


def _slide_request(args):
    sources = [args.text is not None, args.stdin, bool(args.line)]
    if sum(sources) != 1:
        raise CLIError("slide needs exactly one body source: TEXT, --stdin, or --line.")
    frame = {"action": "card", "title": args.title if args.title is not None else args.kind.upper(),
             "footer": args.footer, "avatar": avatar_value(args.avatar)}
    if args.line:
        frame["lines"] = args.line
    else:
        frame["text"] = _stdin_text() if args.stdin else args.text
    slide = {"id": args.id, "owner": args.owner, "kind": args.kind, "frame": frame,
             "expires_at": args.expires_at, "stale_after_s": args.stale_after_s, "actions": _actions(args.action)}
    request = {"action": "slide_put", "slide": slide, "intent": args.intent}
    if args.hold_s is not None:
        request["hold_s"] = args.hold_s
    return request


def _markdown(source):
    if source == "-":
        text = sys.stdin.read(MAX_MARKDOWN + 1)
    else:
        path = Path(source)
        if not path.is_file() or path.stat().st_size > MAX_MARKDOWN:
            raise CLIError(f"markdown {source!r} is not a readable file under {MAX_MARKDOWN} bytes.")
        text = path.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > MAX_MARKDOWN:
        raise CLIError(f"markdown exceeds {MAX_MARKDOWN} bytes.")
    return x4_records.from_markdown(text)


def _record_request(args):
    md = _markdown(args.markdown) if args.markdown else {"title": "", "summary": "", "sections": []}
    title = args.title or md["title"]
    summary = args.summary if args.summary is not None else md["summary"]
    if not title:
        raise CLIError("a record needs a title: --title, or a '# Title' line in --markdown.")
    if not summary:
        raise CLIError("a record needs a summary: --summary, or a paragraph before the first '## ' section.")
    sections = list(md["sections"])
    for pair in args.section or []:
        heading, sep, body = pair.partition("=")
        if not sep:
            raise CLIError(f"--section expects HEADING=BODY, got {pair!r}.")
        sections.append({"heading": heading, "body": body})
    record = {"id": args.id, "kind": "decision" if args.cmd == "decide" else "report", "agent": args.agent,
              "title": title, "summary": summary, "sections": sections, "expires_at": args.expires_at}
    if args.cmd == "decide":
        if args.option:
            record["options"] = args.option
        if args.recommend:
            record["recommendation"] = args.recommend
    return {"action": "record_put", "record": record}


def wait(cfg, args):
    """Poll one record. Returns (exit code, JSON object)."""
    deadline = time.monotonic() + args.timeout
    while True:
        got = send(cfg, {"action": "record_get", "id": args.id})["result"]
        if got is None:
            raise CLIError(f"no record {args.id!r}: removed, or never published.")
        out = {"ok": True, "id": args.id, "revision": got["revision"], "status": got["status"],
               "answer": got["answer"]}
        if args.revision is not None and got["revision"] != args.revision:
            return 2, dict(out, ok=False, outcome="revised")
        if got["status"] in ("answered", "read"):
            return 0, dict(out, outcome=got["status"])
        if x4_records._expired(got["record"], time.time()):
            return 2, dict(out, ok=False, outcome="expired")
        if time.monotonic() >= deadline:
            return 2, dict(out, ok=False, outcome="timeout")
        time.sleep(max(0.0, min(args.interval, deadline - time.monotonic())))


def _request(args):
    if args.cmd in ("decide", "report"):
        return _record_request(args)
    if args.cmd == "records":
        return {"action": "records", "agent": args.agent}
    if args.cmd in ("record-get", "record-remove"):
        return {"action": args.cmd.replace("-", "_"), "id": args.id}
    if args.cmd in ('home','weather','agenda','refresh','glance'):
        return {'action':'glance','op':'status' if args.cmd=='glance' else args.cmd,'device':args.device}
    if args.cmd == "slide":
        return _slide_request(args)
    if args.cmd == "request":
        return _stdin_json()
    if args.cmd in ("slide-get", "slide-remove"):
        return {"action": args.cmd.replace("-", "_"), "id": args.id}
    if args.cmd == "events":
        return {"action": "events", "limit": args.limit}
    return {"action": args.cmd}


def _config(path):
    path = path or os.environ.get("X4CTL_CONFIG") or os.path.expanduser("~/.config/x4ctl/config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
            mode = stat.S_IMODE(os.fstat(fh.fileno()).st_mode)
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError("cannot read x4ctl config") from exc
    if not isinstance(cfg, dict):
        raise CLIError("x4ctl config must be an object")
    if "publisher_token" in cfg:
        if mode != 0o600:
            raise CLIError("publisher config must have mode 0600")
        if "agent_token" in cfg:
            raise CLIError("publisher config needs only publisher_token, not agent_token")
        _publisher_token(cfg["publisher_token"])
        _publisher_gateway(cfg.get("gateway"))
    elif not cfg.get("gateway") or not cfg.get("agent_token"):
        raise CLIError("x4ctl config needs gateway and agent_token.")
    return cfg


def _publisher_token(token):
    if not isinstance(token, str) or not token or any(not 33 <= ord(ch) <= 126 for ch in token):
        raise CLIError("invalid publisher credential")
    return token


def _publisher_gateway(gateway):
    try:
        parsed = urlsplit(gateway) if isinstance(gateway, str) else None
        if (parsed is None or parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ("", "/")
                or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in gateway)):
            raise ValueError
        parsed.port  # validate without echoing potentially secret URL contents
    except ValueError:
        raise CLIError("publisher gateway must be an HTTP(S) origin without credentials, path or query") from None
    return gateway.rstrip("/")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _publisher_result(request, result):
    import x4_publisher
    action = request["action"]
    if action == "record_remove":
        valid = isinstance(result, dict) and type(result.get("removed")) is bool
    elif action == "record_get" and result is None:
        valid = True
    else:
        rows = result if action == "records" else [result]
        valid = isinstance(rows, list)
        try:
            for row in rows:
                body = x4_records.validate_record(row["record"])
                valid = valid and x4_publisher.owns(row) and body["id"] == row["id"] and body["agent"] == row["agent"]
                valid = valid and type(row["revision"]) is int and row["revision"] > 0
                expected_id = request["record"]["id"] if action == "record_put" else request.get("id")
                valid = valid and (expected_id is None or row["id"] == expected_id)
                valid = valid and row["kind"] == body["kind"]
                if row["kind"] == "decision":
                    valid = valid and row["status"] in ("open", "answered")
                    if row["status"] == "answered":
                        valid = (valid and row["answer"] in body["options"]
                                 and type(row["answered_revision"]) is int and row["answered_revision"] == row["revision"])
                    else:
                        valid = valid and row["answer"] is None and row["answered_revision"] is None
                else:
                    valid = valid and row["status"] in ("unread", "read") and row["answer"] is None
        except (ValueError, TypeError, KeyError, OverflowError):
            valid = False
    if not valid:
        raise CLIError("invalid publisher response")


def _send_publisher(cfg, request):
    # Server is the authority; local validation is convenience, never a substitute.
    import x4_publisher
    try:
        canonical = x4_publisher.validate_request(request)
    except PermissionError:
        raise CLIError("publisher scope required") from None
    req = urllib.request.Request(_publisher_gateway(cfg.get("gateway")) + "/x4/v1/publisher", method="POST",
                                 data=json.dumps(canonical).encode(),
                                 headers={"Authorization": "Bearer " + _publisher_token(cfg["publisher_token"]),
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=15) as resp:
            data = resp.read(MAX_PUBLISHER_RESPONSE + 1)
            if len(data) > MAX_PUBLISHER_RESPONSE:
                raise CLIError("publisher response too large; get/remove records individually")
            body = json.loads(data)
        if not isinstance(body, dict) or body.get("ok") is not True or "result" not in body or body.get("principal") != "elsie":
            raise CLIError("invalid publisher response")
        _publisher_result(canonical, body["result"])
        return body
    except urllib.error.HTTPError as err:
        with err:
            code = err.code  # response body and URL are deliberately not relayed
        raise CLIError(f"publisher HTTP {code}") from None
    except (json.JSONDecodeError, UnicodeError):
        raise CLIError("invalid publisher response") from None
    except OSError:
        raise CLIError("publisher gateway unreachable") from None


def send(cfg, request):
    if "publisher_token" in cfg:
        return _send_publisher(cfg, request)
    canonical = x4_protocol.validate(request)  # fail fast locally, same rules as x4d
    req = urllib.request.Request(cfg["gateway"].rstrip("/") + "/x4/v1/agent", method="POST",
                                 data=json.dumps(canonical).encode())
    req.add_header("Authorization", "Bearer " + cfg["agent_token"])
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as err:
        try:
            raise RuntimeError(json.loads(err.read()).get("error", f"HTTP {err.code}")) from None
        except json.JSONDecodeError:
            raise RuntimeError(f"HTTP {err.code}") from None
    except OSError as exc:
        raise RuntimeError(f"x4d unreachable at {cfg['gateway']}: {exc}") from exc


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.cmd == "preview":
            req = x4_protocol.validate(_stdin_json())
            if req["action"] != "slide_put":
                raise CLIError("preview needs a slide_put request.")
            now = time.time()
            x4_render.render_slide(req["slide"], updated_at=now, now=now).save(args.output)
            print(json.dumps({"ok": True, "output": args.output}))
            return 0
        if args.cmd == "wait":
            code, out = wait(_config(args.config), args)
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return code
        print(json.dumps(send(_config(args.config), _request(args)), indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
