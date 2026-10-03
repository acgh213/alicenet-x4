#!/usr/bin/env python3
"""x4d: the Alicenet gateway for Xteink X4 endpoints.

Device side (pull only; the X4 initiates everything):
  GET  /x4/v1/frame[?wait=N]   current card as an 800x480 P4 PBM, 304 if unchanged, 204 if empty
  POST /x4/v1/events           button events -> {"acked": maxseq}
Agent side (clockctl and other agents):
  POST /x4/v1/agent            x4_protocol requests (slide_put, slides, status, ...)
  GET  /x4/v1/preview.png?id=  what that slide will look like on the panel
Spec: docs/protocol.md. Config: JSON file, path in argv[1] (see x4d.example.json).
"""
import hashlib
import hmac
import io
import ipaddress
import json
import shlex
import subprocess
import sys
import threading
import time
import zoneinfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import x4_protocol
import x4_render
from x4_store import Store

FW_HEADERS = {"X-Wake": "wake", "X-Battery": "battery", "X-Rssi": "rssi", "X-Fw": "fw"}
NAVIGATION = {"right": +1, "left": -1}


def allowed(ip, cidrs):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(c) for c in cidrs)


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg["db"], dwell_s=cfg.get("dwell_s", 3600))
        self.tz = zoneinfo.ZoneInfo(cfg.get("tz", "America/New_York"))
        self.changed = threading.Condition()
        self.generation = 0

    def bump(self):
        with self.changed:
            self.generation += 1
            self.changed.notify_all()

    def device_for(self, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        for device, want in self.cfg.get("devices", {}).items():
            if hmac.compare_digest(digest, want):
                return device
        return None

    def is_agent(self, token):
        want = self.cfg.get("agent_token_sha256", "")
        return bool(want) and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), want)

    def render(self, entry, now):
        img = x4_render.render_slide(entry["slide"], updated_at=entry["updated_at"], now=now, tz=self.tz)
        return x4_render.to_pbm(img)

    def frame(self, wake, now):
        entry = self.store.current(now=now, wake=wake)
        if entry is None:
            return None
        pbm = self.render(entry, now)
        actions = sorted(entry["slide"]["actions"])
        return {"pbm": pbm, "etag": '"%s"' % x4_render.etag(pbm), "card": entry["id"], "actions": actions,
                "session": self.cfg.get("session_s", 30) if actions else 0}

    def agent(self, request, now):
        req = x4_protocol.validate(request)
        action = req["action"]
        if action == "slide_put":
            self.render({"slide": req["slide"], "updated_at": now}, now)  # unrenderable -> ValueError, not stored
            entry = self.store.put(req, now=now)
            self.bump()
            return {"result": entry, "delivery": "next_wake"}
        if action == "slide_get":
            return {"result": self.store.get(req["id"])}
        if action == "slide_remove":
            gone = self.store.remove(req["id"])
            self.bump()
            return {"result": {"removed": gone}}
        if action == "slides":
            return {"result": self.store.slides(now=now)}
        if action == "events":
            return {"result": self.store.events(limit=req["limit"])}
        if action == "status":
            return {"result": self.store.status(now=now)}
        return {"result": x4_protocol.capabilities()}

    def forward_once(self):
        """Send pending assigned-button events to Muse via the configured command."""
        cmd = self.cfg.get("forward_cmd")
        if not cmd:
            return 0
        sent = 0
        for ev in self.store.pending_forwards():
            text = f"[x4] Cassie pressed {ev['button']} ({ev['press']}) = '{ev['label']}' on '{ev['card']}'"
            try:
                ok = subprocess.run(list(cmd) + [text], timeout=30, capture_output=True).returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                ok = False
            self.store.mark_forwarded(ev["rowid"], ok)
            if not ok:
                break  # keep order; retry next tick
            sent += 1
        return sent


class Handler(BaseHTTPRequestHandler):
    server_version = "x4d/0.1"
    app: App

    def log_message(self, fmt, *args):
        sys.stderr.write("x4d %s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body=b"", ctype="application/json", headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, str(v))
        if body:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, status, obj, headers=None):
        self._send(status, json.dumps(obj, ensure_ascii=False).encode(), headers=headers)

    def _token(self):
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > x4_protocol.MAX_REQUEST:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(length) or b"null")

    def _guard(self):
        if not allowed(self.client_address[0], self.app.cfg.get("allow", ["127.0.0.0/8"])):
            self._json(403, {"ok": False, "error": "not on the allowed network"})
            return False
        return True

    def do_GET(self):
        if not self._guard():
            return
        url = urlsplit(self.path)
        if url.path == "/x4/v1/frame":
            return self._frame(parse_qs(url.query))
        if url.path == "/x4/v1/preview.png":
            return self._preview(parse_qs(url.query))
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self._guard():
            return
        path = urlsplit(self.path).path
        try:
            if path == "/x4/v1/events":
                return self._events()
            if path == "/x4/v1/agent":
                if not self.app.is_agent(self._token()):
                    return self._json(401, {"ok": False, "error": "agent token required"})
                return self._json(200, {"ok": True, **self.app.agent(self._body(), time.time())})
            self._json(404, {"ok": False, "error": "not found"})
        except ValueError as exc:  # includes json.JSONDecodeError
            self._json(400, {"ok": False, "error": str(exc)})

    def _frame(self, query):
        device = self.app.device_for(self._token())
        if not device:
            return self._send(401)
        tele = {key: self.headers.get(h) for h, key in FW_HEADERS.items()}
        wake = tele["wake"] if tele["wake"] in x4_protocol.WAKES else "timer"
        rssi = tele["rssi"]
        self.app.store.seen(device, now=time.time(), wake=wake, battery=tele["battery"], fw=tele["fw"],
                            rssi=int(rssi) if rssi and rssi.lstrip("-").isdigit() else None)
        wait = min(max(int((query.get("wait") or ["0"])[0] or 0), 0), 25)
        deadline = time.monotonic() + wait
        known = self.headers.get("If-None-Match")
        while True:
            with self.app.changed:
                generation = self.app.generation
            frame = self.app.frame(wake if not wait else "session", time.time())
            if frame is None and not wait:
                return self._send(204)
            if frame is not None and frame["etag"] != known:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._send(304) if frame else self._send(204)
            with self.app.changed:
                self.app.changed.wait_for(lambda: self.app.generation != generation, timeout=remaining)
        headers = {"ETag": frame["etag"], "X-Card": frame["card"], "X-Card-Actions": ",".join(frame["actions"]),
                   "X-Refresh": "half", "X-Next-Poll": self.app.cfg.get("poll_s", 900),
                   "X-Session": frame["session"], "Cache-Control": "no-store"}
        self._send(200, frame["pbm"], "image/x-portable-bitmap", headers)

    def _events(self):
        device = self.app.device_for(self._token())
        if not device:
            return self._send(401)
        batch = x4_protocol.validate_events(self._body())
        if batch["device"] != device:
            return self._json(403, {"ok": False, "error": "device does not match token"})
        now = time.time()
        new = self.app.store.record_events(batch, now=now)
        moved = False
        for ev in new:
            if ev["button"] in NAVIGATION and ev["press"] == "short":
                self.app.store.navigate(NAVIGATION[ev["button"]], now=now)
                moved = True
        if moved:
            self.app.bump()
        if any(ev["button"] not in NAVIGATION for ev in new):
            threading.Thread(target=self.app.forward_once, daemon=True).start()
        self._json(200, {"acked": self.app.store.acked(device, batch["boot"])},
                   headers={"X-Frame-Changed": 1} if moved else None)

    def _preview(self, query):
        if not self.app.is_agent(self._token()):
            return self._send(401)
        entry = self.app.store.get((query.get("id") or [""])[0])
        if entry is None:
            return self._json(404, {"ok": False, "error": "no such slide"})
        img = x4_render.render_slide(entry["slide"], updated_at=entry["updated_at"], now=time.time(), tz=self.app.tz)
        out = io.BytesIO()
        img.save(out, "PNG")
        self._send(200, out.getvalue(), "image/png")


def make_server(app):
    host, port = app.cfg.get("listen", "127.0.0.1:8787").rsplit(":", 1)
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, int(port)), handler)
    httpd.daemon_threads = True
    return httpd


def main(argv):
    if len(argv) != 2:
        sys.exit("usage: x4d.py CONFIG.json")
    with open(argv[1], encoding="utf-8") as fh:
        app = App(json.load(fh))
    if isinstance(app.cfg.get("forward_cmd"), str):
        app.cfg["forward_cmd"] = shlex.split(app.cfg["forward_cmd"])

    def retry_forwards():
        while True:
            time.sleep(60)
            app.forward_once()

    threading.Thread(target=retry_forwards, daemon=True).start()
    httpd = make_server(app)
    print("x4d listening on %s:%d" % httpd.server_address[:2], flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main(sys.argv)
