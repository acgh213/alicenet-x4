#!/usr/bin/env python3
"""fake_x4: a host-side stand-in for the alicenet-x4 firmware's wake sequence.

It does exactly what the firmware will do per docs/protocol.md, minus the radio and panel:
state that survives "sleep" lives in a JSON file (the firmware's NVS), frames it would draw
are written as PNGs, and it never draws a partial frame.

  fake_x4.py --state DIR boot                  power-on: new boot number, wake=boot
  fake_x4.py --state DIR wake [--timer]        one wake: flush events, fetch frame, "sleep"
  fake_x4.py --state DIR press right [--long]  queue a press and wake on it (wake=button)
  fake_x4.py --state DIR show                  print the stored state
DIR/device.json must hold {"gateway", "device", "token"} (the SD provisioning file).
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from PIL import Image

WIDTH, HEIGHT = 800, 480
HEADER = b"P4\n%d %d\n" % (WIDTH, HEIGHT)
RASTER = WIDTH * HEIGHT // 8
MAX_QUEUE = 16
BUTTONS = ("back", "confirm", "left", "right", "up", "down", "power")
FW = "fake-x4/0.1.0"


class Device:
    def __init__(self, state_dir):
        self.dir = state_dir
        with open(os.path.join(state_dir, "device.json"), encoding="utf-8") as fh:
            self.prov = json.load(fh)
        self.nvs_path = os.path.join(state_dir, "nvs.json")
        try:
            with open(self.nvs_path, encoding="utf-8") as fh:
                self.nvs = json.load(fh)
        except FileNotFoundError:
            self.nvs = {"boot": 0, "seq": 0, "queue": [], "etag": None, "card": None, "frames": 0,
                        "fail_count": 0, "next_sleep_s": None, "screen": None}

    def save(self):
        tmp = self.nvs_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.nvs, fh, indent=1)
        os.replace(tmp, self.nvs_path)

    def _request(self, method, path, body=None, headers=None):
        req = urllib.request.Request(self.prov["gateway"].rstrip("/") + path, method=method,
                                     data=None if body is None else json.dumps(body).encode())
        req.add_header("Authorization", "Bearer " + self.prov["token"])
        req.add_header("X-Device", self.prov["device"])
        if body is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, str(v))
        try:
            with urllib.request.urlopen(req, timeout=self.prov.get("timeout_s", 10) + 30) as resp:
                return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.headers, err.read()

    def queue_press(self, button, press, wake="button"):
        self.nvs["seq"] += 1
        self.nvs["queue"].append({"seq": self.nvs["seq"], "card": self.nvs["card"], "etag": self.nvs["etag"],
                                  "button": button, "press": press, "wake": wake})
        del self.nvs["queue"][:-MAX_QUEUE]  # overflow drops the oldest

    def flush_events(self):
        if not self.nvs["queue"]:
            return False
        batch = {"device": self.prov["device"], "boot": self.nvs["boot"], "events": self.nvs["queue"]}
        status, headers, body = self._request("POST", "/x4/v1/events", batch)
        if status != 200:
            raise ConnectionError(f"events: HTTP {status}")
        acked = json.loads(body)["acked"]
        self.nvs["queue"] = [e for e in self.nvs["queue"] if e["seq"] > acked]
        return headers.get("X-Frame-Changed") == "1"

    def fetch(self, wake, wait=0):
        headers = {"X-Wake": wake, "X-Battery": self.prov.get("battery", "4.01V,88"), "X-Rssi": -58, "X-Fw": FW}
        if self.nvs["etag"]:
            headers["If-None-Match"] = self.nvs["etag"]
        status, resp, body = self._request("GET", "/x4/v1/frame" + (f"?wait={wait}" if wait else ""), headers=headers)
        if status == 304:
            return "kept"
        if status == 204:
            return "empty"
        if status == 401:
            self.nvs["screen"] = "not provisioned"
            return "unauthorised"
        if status != 200:
            raise ConnectionError(f"frame: HTTP {status}")
        if not body.startswith(HEADER) or len(body) != len(HEADER) + RASTER:
            raise ConnectionError("frame: bad PBM, keeping the old screen")
        self.draw(body[len(HEADER):])
        self.nvs.update(etag=resp["ETag"], card=resp["X-Card"], fail_count=0)
        self.nvs["next_sleep_s"] = min(max(int(resp.get("X-Next-Poll", 900)), 300), 21600)
        self.nvs["session_s"] = int(resp.get("X-Session", 0))
        return "drawn"

    def draw(self, raster):
        img = Image.frombytes("1", (WIDTH, HEIGHT), bytes(b ^ 0xFF for b in raster))
        self.nvs["frames"] += 1
        path = os.path.join(self.dir, "screen.png")
        img.save(path)
        img.save(os.path.join(self.dir, "frame-%04d.png" % self.nvs["frames"]))
        self.nvs["screen"] = path

    def wake(self, wake):
        """One full wake: events first, then the frame; on any failure keep the screen and back off."""
        log = {"wake": wake, "boot": self.nvs["boot"]}
        try:
            changed = self.flush_events()
            log["frame"] = self.fetch(wake)
            if changed and log["frame"] == "kept":
                log["frame"] = self.fetch("session")
            log["sleep_s"] = self.nvs["next_sleep_s"] or 900
        except (OSError, ConnectionError, ValueError) as exc:
            self.nvs["fail_count"] += 1
            log.update(frame="kept", error=str(exc), sync="failed",
                       sleep_s=min(300 * 2 ** (self.nvs["fail_count"] - 1), 21600))
        log.update(queued=len(self.nvs["queue"]), card=self.nvs["card"], session_s=self.nvs.get("session_s", 0))
        self.save()
        return log


def main(argv=None):
    ap = argparse.ArgumentParser(description="host-side stand-in for the alicenet-x4 firmware")
    ap.add_argument("--state", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("boot")
    sub.add_parser("show")
    wake = sub.add_parser("wake")
    wake.add_argument("--timer", action="store_true")
    press = sub.add_parser("press")
    press.add_argument("button", choices=BUTTONS)
    press.add_argument("--long", action="store_true")
    args = ap.parse_args(argv)
    dev = Device(args.state)
    if args.cmd == "show":
        print(json.dumps(dev.nvs, indent=1))
        return 0
    if args.cmd == "boot":
        dev.nvs["boot"] += 1  # seq and the unsent queue survive power-off, like NVS
        result = dev.wake("boot")
    elif args.cmd == "press":
        dev.queue_press(args.button, "long" if args.long else "short")
        result = dev.wake("button")
    else:
        result = dev.wake("timer" if args.timer else "button")
    print(json.dumps(result))
    return 0 if "error" not in result else 2


if __name__ == "__main__":
    sys.exit(main())
