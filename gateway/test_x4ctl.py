"""x4ctl: clockctl-shaped CLI for agents, against a live in-process x4d."""
import contextlib
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest

from PIL import Image

import x4ctl
import x4d

AGENT = "a" * 64


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        cfg = {"listen": "127.0.0.1:0", "db": os.path.join(self.dir.name, "x4.db"), "tz": "UTC",
               "devices": {}, "agent_token_sha256": hashlib.sha256(AGENT.encode()).hexdigest()}
        self.httpd = x4d.make_server(x4d.App(cfg))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.conf = os.path.join(self.dir.name, "x4ctl.json")
        with open(self.conf, "w") as fh:
            json.dump({"gateway": "http://127.0.0.1:%d" % self.httpd.server_address[1], "agent_token": AGENT}, fh)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def run_cli(self, *argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        old = x4ctl.sys.stdin
        x4ctl.sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = x4ctl.main(["--config", self.conf, *argv])
        finally:
            x4ctl.sys.stdin = old
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_slide_with_literal_backslash_n_and_actions(self):
        code, body = self.run_cli("slide", "--id", "alice.q", "--owner", "alice", "--avatar", "alice",
                                  "--action", "confirm=yes", "--action", "down=later", "one\\ntwo")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["delivery"], "next_wake")
        slide = body["result"]["slide"]
        self.assertEqual(slide["frame"]["text"], "one\ntwo")
        self.assertEqual(slide["actions"], {"confirm": "yes", "down": "later"})
        self.assertEqual(slide["frame"]["title"], "NOTE")

    def test_line_flag_keeps_text_literal(self):
        code, body = self.run_cli("slide", "--id", "a.b", "--line", "C:\\new", "--line", "")
        self.assertEqual(code, 0, body)
        self.assertEqual(body["result"]["slide"]["frame"]["lines"], ["C:\\new", ""])

    def test_stdin_body(self):
        code, body = self.run_cli("slide", "--id", "a.b", "--stdin", stdin="from\nstdin\n")
        self.assertEqual((code, body["result"]["slide"]["frame"]["text"]), (0, "from\nstdin\n"))

    def test_png_avatar_becomes_32x32_rows(self):
        path = os.path.join(self.dir.name, "logo.png")
        img = Image.new("L", (64, 64), 255)
        img.paste(0, (0, 0, 32, 64))  # left half black
        img.save(path)
        code, body = self.run_cli("slide", "--id", "a.logo", "--avatar", path, "hi")
        self.assertEqual(code, 0, body)
        rows = body["result"]["slide"]["frame"]["avatar"]["rows"]
        self.assertEqual((len(rows), rows[5][:16], rows[5][16:]), (32, "1" * 16, "0" * 16))

    def test_read_verbs_and_remove(self):
        self.run_cli("slide", "--id", "a.b", "hello")
        self.assertEqual([s["slide"]["id"] for s in self.run_cli("slides")[1]["result"]], ["a.b"])
        self.assertEqual(self.run_cli("slide-get", "a.b")[1]["result"]["slide"]["id"], "a.b")
        self.assertEqual(self.run_cli("slide-remove", "a.b")[1]["result"], {"removed": True})
        self.assertIn("devices", self.run_cli("status")[1]["result"])
        self.assertEqual(self.run_cli("capabilities")[1]["result"]["delivery"]["model"], "pull")
        self.assertEqual(self.run_cli("events", "--limit", "5")[1]["result"], [])

    def test_request_passthrough(self):
        req = {"action": "slide_put", "intent": "hold", "hold_s": 600, "slide": {
            "id": "a.h", "frame": {"action": "card", "title": "HOLD", "lines": ["x"]}}}
        code, body = self.run_cli("request", stdin=json.dumps(req))
        self.assertEqual(code, 0, body)

    def test_errors_are_json_on_stderr_with_exit_1(self):
        code, body = self.run_cli("slide", "--id", "BAD ID", "x")
        self.assertEqual(code, 1)
        self.assertFalse(body["ok"])
        code, body = self.run_cli("slide", "--id", "a.b", "--action", "left=nope", "x")
        self.assertEqual(code, 1)
        code, body = self.run_cli("slide", "--id", "a.b")  # no body source
        self.assertEqual(code, 1)

    def test_preview_offline_writes_png_without_a_gateway(self):
        out = os.path.join(self.dir.name, "p.png")
        req = {"action": "slide_put", "slide": {"id": "a.p", "owner": "alice",
                                                 "frame": {"action": "card", "title": "P", "text": "a\\nb"}}}
        code, body = self.run_cli("preview", "--output", out, stdin=json.dumps(req))
        self.assertEqual(code, 0, body)
        with Image.open(out) as img:
            self.assertEqual(img.size, (800, 480))

    def test_unreachable_gateway_is_a_clean_error(self):
        with open(self.conf, "w") as fh:
            json.dump({"gateway": "http://127.0.0.1:9", "agent_token": AGENT}, fh)
        code, body = self.run_cli("status")
        self.assertEqual(code, 1)
        self.assertIn("unreachable", body["error"])


if __name__ == "__main__":
    unittest.main()
