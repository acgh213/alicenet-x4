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
        # The in-process gateway logs requests to this same stderr; the error object is the last line.
        return code, json.loads(out.getvalue() or err.getvalue().strip().splitlines()[-1])

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

    # ---- records -----------------------------------------------------------------------
    def test_decide_with_flags_defaults_options_and_reads_back(self):
        code, body = self.run_cli("decide", "--id", "vesper.merge", "--agent", "vesper", "--title", "Merge it?",
                                  "--summary", "CI green.\\nOne reviewer.", "--section", "Risk=Low; touches docs only",
                                  "--section", "Diff=a=b stays in the body", "--recommend", "approve")
        self.assertEqual(code, 0, body)
        record = body["result"]["record"]
        self.assertEqual(record["options"], ["approve", "reject", "defer"])
        self.assertEqual(record["summary"], "CI green.\nOne reviewer.")
        self.assertEqual(record["sections"][1], {"heading": "Diff", "body": "a=b stays in the body"})
        self.assertEqual((body["result"]["revision"], body["delivery"]), (1, "next_wake"))
        got = self.run_cli("record-get", "vesper.merge")[1]["result"]
        self.assertEqual(got["status"], "open")

    def test_custom_options(self):
        code, body = self.run_cli("decide", "--id", "pyrrha.next", "--agent", "pyrrha", "--title", "Next?",
                                  "--summary", "Pick.", "--option", "house", "--option", "work")
        self.assertEqual((code, body["result"]["record"]["options"]), (0, ["house", "work"]))

    def test_report_from_markdown_file(self):
        path = os.path.join(self.dir.name, "r.md")
        with open(path, "w") as fh:
            fh.write("# Weekly lab report\n\nThree runs converged.\nOne regression open.\n\n"
                     "## Findings\n\nSeeds agree.\n\n- item one\n- item two\n\n## Recommendation\nKeep going.\n")
        code, body = self.run_cli("report", "--id", "eido.weekly", "--agent", "eido", "--markdown", path)
        self.assertEqual(code, 0, body)
        record = body["result"]["record"]
        self.assertEqual(record["title"], "Weekly lab report")
        self.assertEqual(record["summary"], "Three runs converged.\nOne regression open.")
        self.assertEqual([s["heading"] for s in record["sections"]], ["Findings", "Recommendation"])
        self.assertEqual(record["sections"][0]["body"], "Seeds agree.\n\n- item one\n- item two")
        self.assertNotIn("options", record)

    def test_markdown_from_stdin_and_title_flag_wins(self):
        code, body = self.run_cli("report", "--id", "eido.r", "--agent", "eido", "--title", "Override",
                                  "--markdown", "-", stdin="# Ignored\n\nSummary here.\n")
        self.assertEqual((code, body["result"]["record"]["title"]), (0, "Override"))

    def test_markdown_without_title_or_summary_is_an_error(self):
        code, body = self.run_cli("report", "--id", "eido.r", "--agent", "eido", "--markdown", "-",
                                  stdin="## Only a section\nbody\n")
        self.assertEqual(code, 1)
        self.assertIn("title", body["error"])

    def test_records_list_and_remove(self):
        self.run_cli("report", "--id", "eido.a", "--agent", "eido", "--title", "A", "--summary", "s")
        self.run_cli("decide", "--id", "vesper.b", "--agent", "vesper", "--title", "B", "--summary", "s")
        self.assertEqual([r["id"] for r in self.run_cli("records")[1]["result"]], ["vesper.b", "eido.a"])
        self.assertEqual([r["id"] for r in self.run_cli("records", "--agent", "eido")[1]["result"]], ["eido.a"])
        self.assertEqual(self.run_cli("record-remove", "eido.a")[1]["result"], {"removed": True})

    def test_wait_returns_when_cassie_answers(self):
        self.run_cli("decide", "--id", "pyrrha.q", "--agent", "pyrrha", "--title", "Q", "--summary", "s")
        app = self.httpd.RequestHandlerClass.app
        threading.Timer(0.3, lambda: app.records.answer("pyrrha.q", 1, "reject", now=1, source="x4-01")).start()
        code, body = self.run_cli("wait", "pyrrha.q", "--timeout", "10", "--interval", "0.1")
        self.assertEqual(code, 0, body)
        self.assertEqual((body["outcome"], body["answer"], body["revision"]), ("answered", "reject", 1))

    def test_wait_times_out_with_exit_2(self):
        self.run_cli("decide", "--id", "pyrrha.q", "--agent", "pyrrha", "--title", "Q", "--summary", "s")
        code, body = self.run_cli("wait", "pyrrha.q", "--timeout", "0.3", "--interval", "0.1")
        self.assertEqual((code, body["outcome"], body["status"]), (2, "timeout", "open"))

    def test_wait_reports_a_revision_it_was_not_waiting_for(self):
        self.run_cli("decide", "--id", "pyrrha.q", "--agent", "pyrrha", "--title", "Q", "--summary", "s")
        self.run_cli("decide", "--id", "pyrrha.q", "--agent", "pyrrha", "--title", "Q", "--summary", "changed")
        code, body = self.run_cli("wait", "pyrrha.q", "--revision", "1", "--timeout", "5", "--interval", "0.1")
        self.assertEqual((code, body["outcome"], body["revision"]), (2, "revised", 2))

    def test_wait_on_report_returns_when_read_and_missing_is_an_error(self):
        self.run_cli("report", "--id", "eido.r", "--agent", "eido", "--title", "R", "--summary", "s")
        app = self.httpd.RequestHandlerClass.app
        app.records.ack("eido.r", 1, now=1)
        code, body = self.run_cli("wait", "eido.r", "--timeout", "5", "--interval", "0.1")
        self.assertEqual((code, body["outcome"]), (0, "read"))
        code, body = self.run_cli("wait", "nobody.home", "--timeout", "1", "--interval", "0.1")
        self.assertEqual(code, 1)
        self.assertIn("no record", body["error"])

    def test_wait_on_expired_record_exits_2(self):
        self.run_cli("decide", "--id", "pyrrha.q", "--agent", "pyrrha", "--title", "Q", "--summary", "s",
                     "--expires-at", "2026-01-01T00:00:00Z")
        code, body = self.run_cli("wait", "pyrrha.q", "--timeout", "5", "--interval", "0.1")
        self.assertEqual((code, body["outcome"]), (2, "expired"))


if __name__ == "__main__":
    unittest.main()
