#!/usr/bin/env python3
"""x4ctl: the agent-facing CLI for X4 endpoints, shaped like clockctl.

Same verbs as clockctl (slide, slides, slide-get, slide-remove, status, capabilities,
request, preview) plus `events` and per-card `--action BUTTON=LABEL`. Output is one JSON
object on stdout (exit 0) or {"ok": false, "error": ...} on stderr (exit 1).

Body text: pass TEXT (a literal "\\n" becomes a line break), --stdin, or repeated --line
(taken literally, never escape-decoded). Publishing is honest about latency:
"delivery": "next_wake" means stored now, shown when the X4 next wakes.

Config: --config PATH, else $X4CTL_CONFIG, else ~/.config/x4ctl/config.json:
  {"gateway": "http://192.168.18.61:8787", "agent_token": "..."}
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

import x4_protocol
import x4_render

MAX_STDIN = 8192
MAX_AVATAR_BYTES = 1024 * 1024


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


def _request(args):
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
    except (OSError, json.JSONDecodeError) as exc:
        raise CLIError(f"cannot read x4ctl config {path}: {exc}") from exc
    if not cfg.get("gateway") or not cfg.get("agent_token"):
        raise CLIError("x4ctl config needs gateway and agent_token.")
    return cfg


def send(cfg, request):
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
        print(json.dumps(send(_config(args.config), _request(args)), indent=2, ensure_ascii=False))
        return 0
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
