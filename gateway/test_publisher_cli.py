"""x4ctl markdown + revision wait through the restricted HTTP bridge."""
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch

import x4ctl
from test_publisher_http import PUBLISHER_TOKEN, record
from test_x4d import Server, sha


class PublisherCLI(Server):
    def setUp(self):
        super().setUp()
        self.app.cfg["elsie_publisher_token_sha256"] = sha(PUBLISHER_TOKEN)
        self.conf = Path(self.dir.name) / "publisher.json"
        self.write_config(gateway=self.base, publisher_token=PUBLISHER_TOKEN)

    def write_config(self, **cfg):
        self.conf.write_text(json.dumps(cfg))
        self.conf.chmod(0o600)

    def cli(self, *args, text=""):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(x4ctl.sys, "stdin", io.StringIO(text)), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = x4ctl.main(["--config", str(self.conf), *args])
        raw = stdout.getvalue() or stderr.getvalue().strip().splitlines()[-1]
        return code, json.loads(raw)

    def publish(self, cmd="report", **kwargs):
        return self.cli(cmd, "--id", "elsie.release", "--agent", "elsie", "--markdown", "-",
                        text="# Release\n\nSynthetic status only.\n\n## Evidence\nTests passed.\n", **kwargs)

    def test_markdown_report_file_readback_list_ack_wait_remove(self):
        md = Path(self.dir.name) / "report.md"
        md.write_text("# Release\n\nSynthetic status only.\n\n## Evidence\nTests passed.\n")
        code, put = self.cli("report", "--id", "elsie.release", "--agent", "elsie", "--markdown", str(md))
        self.assertEqual(code, 0, put)
        self.assertEqual((put["principal"], put["delivery"]), ("elsie", "next_wake"))
        body = put["result"]["record"]
        self.assertEqual((body["title"], body["summary"], body["sections"]),
                         ("Release", "Synthetic status only.", [{"heading": "Evidence", "body": "Tests passed."}]))
        self.assertEqual(self.cli("record-get", "elsie.release")[1]["result"], put["result"])
        self.assertEqual(self.cli("records")[1]["result"], [put["result"]])
        self.app.records.ack("elsie.release", 1, now=1)
        code, waited = self.cli("wait", "elsie.release", "--revision", "1", "--timeout", "0")
        self.assertEqual((code, waited["outcome"]), (0, "read"))
        self.assertTrue(self.cli("record-remove", "elsie.release")[1]["result"]["removed"])

    def test_decision_stdin_answer_wait_and_revision_mismatch(self):
        code, put = self.publish("decide")
        self.assertEqual(code, 0, put)
        self.app.records.answer("elsie.release", 1, "reject", now=1, source="x4-01")
        code, answer = self.cli("wait", "elsie.release", "--revision", "1", "--timeout", "0")
        self.assertEqual((code, answer["revision"], answer["answer"]), (0, 1, "reject"))
        self.cli("decide", "--id", "elsie.release", "--agent", "elsie", "--title", "Revised", "--summary", "Changed")
        code, revised = self.cli("wait", "elsie.release", "--revision", "1", "--timeout", "0")
        self.assertEqual((code, revised["outcome"], revised["revision"], revised["answer"]), (2, "revised", 2, None))
        code, timeout = self.cli("wait", "elsie.release", "--revision", "2", "--timeout", "0")
        self.assertEqual((code, timeout["outcome"]), (2, "timeout"))

    def test_wait_on_recreated_record_reports_revised_and_never_old_answer(self):
        self.publish("decide")
        self.cli("record-remove", "elsie.release")
        self.publish("decide")
        self.app.records.answer("elsie.release", 1, "approve", now=1, source="x4-01")
        code, out = self.cli("wait", "elsie.release", "--revision", "1", "--timeout", "0")
        self.assertEqual((code, out["outcome"], out["revision"], out["answer"]), (2, "revised", 2, None))

    def test_overflow_expiry_is_clean_cli_error(self):
        code, out = self.cli("report", "--id", "elsie.release", "--agent", "elsie", "--title", "Q", "--summary", "s",
                             "--expires-at", "0001-01-01T00:00:00+14:00")
        self.assertEqual(code, 1)
        self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))

    def test_wait_refuses_malformed_success_result_with_clean_error(self):
        def malformed(handler):
            return handler._json(200, {"ok": True, "principal": "elsie", "result": {}})
        with patch.object(self.httpd.RequestHandlerClass, "_publisher", malformed):
            code, out = self.cli("wait", "elsie.release", "--revision", "1", "--timeout", "0")
        self.assertEqual((code, out["error"]), (1, "invalid publisher response"))

    def test_wait_refuses_malformed_outer_types_and_boolean_answer_revision(self):
        self.publish("decide")
        self.app.records.answer("elsie.release", 1, "approve", now=1, source="x4-01")
        original = self.app.records.get("elsie.release")
        cases = [("id", value) for value in (None, 1, [], {})]
        cases += [("answered_revision", value) for value in (True, 1.0, None, "1")]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                def malformed(handler):
                    return handler._json(200, {"ok": True, "principal": "elsie", "result": dict(original, **{key: value})})
                with patch.object(self.httpd.RequestHandlerClass, "_publisher", malformed):
                    code, out = self.cli("wait", "elsie.release", "--revision", "1", "--timeout", "0")
                self.assertEqual((code, out["error"]), (1, "invalid publisher response"))

    def test_control_char_gateway_is_clean_redacted_error(self):
        for control in ("\x00", "\x01", "\x7f", "\n", "\t"):
            self.write_config(gateway="http://127.0.0.1" + control + PUBLISHER_TOKEN, publisher_token=PUBLISHER_TOKEN)
            code, out = self.cli("records")
            self.assertEqual(code, 1)
            self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))

    def test_wrong_auth_wrong_scope_and_status_are_clean_cli_errors(self):
        for args in (("record-get", "pyrrha.secret"), ("records", "--agent", "pyrrha"), ("status",),
                     ("report", "--id", "elsie.release", "--agent", "pyrrha", "--title", "Q", "--summary", "s")):
            code, out = self.cli(*args)
            self.assertEqual(code, 1, out)
            self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))
        self.write_config(gateway=self.base, publisher_token="wrong")
        self.assertEqual(self.cli("records")[0], 1)

    def test_secret_config_requires_0600_and_unambiguous_credential(self):
        self.conf.chmod(0o644)
        code, out = self.cli("records")
        self.assertEqual(code, 1, out)
        self.assertIn("0600", out["error"])
        self.write_config(gateway=self.base, publisher_token=PUBLISHER_TOKEN, agent_token="synthetic-broad")
        self.assertEqual(self.cli("records")[0], 1)
        self.write_config(gateway=self.base, publisher_token="")
        self.assertEqual(self.cli("records")[0], 1)

    def test_invalid_publisher_token_is_redacted_before_http_headers(self):
        for token in (PUBLISHER_TOKEN + "\nSECRET", PUBLISHER_TOKEN + "é", PUBLISHER_TOKEN + " "):
            self.write_config(gateway=self.base, publisher_token=token)
            code, out = self.cli("records")
            self.assertEqual(code, 1)
            self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))

    def test_publisher_errors_never_echo_server_response_or_credential(self):
        for status, body in ((403, {"ok": False, "error": PUBLISHER_TOKEN}),
                             (200, {"ok": False, "error": PUBLISHER_TOKEN}),
                             (200, [PUBLISHER_TOKEN]), (200, {"ok": True})):
            def response(handler):
                return handler._json(status, body)
            with patch.object(self.httpd.RequestHandlerClass, "_publisher", response):
                code, out = self.cli("records")
                self.assertEqual(code, 1, out)
                self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))
        def malformed(handler):
            return handler._send(502, PUBLISHER_TOKEN.encode())
        with patch.object(self.httpd.RequestHandlerClass, "_publisher", malformed):
            code, out = self.cli("records")
            self.assertEqual(code, 1)
            self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))

    def test_publisher_refuses_redirects_instead_of_forwarding_bearer(self):
        calls = []
        def redirect(handler):
            calls.append(handler.path)
            return handler._send(307, headers={"Location": self.base + "/x4/v1/agent"})
        with patch.object(self.httpd.RequestHandlerClass, "_publisher", redirect):
            code, out = self.cli("records")
        self.assertEqual(code, 1)
        self.assertEqual(calls, ["/x4/v1/publisher"])
        self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))

    def test_publisher_config_refuses_unsafe_urls_without_echoing_them(self):
        for url in ("http://user:" + PUBLISHER_TOKEN + "@127.0.0.1", self.base + "?" + PUBLISHER_TOKEN,
                    "file:///" + PUBLISHER_TOKEN, self.base + "/x4/v1/agent"):
            self.write_config(gateway=url, publisher_token=PUBLISHER_TOKEN)
            code, out = self.cli("records")
            self.assertEqual(code, 1, out)
            self.assertNotIn(PUBLISHER_TOKEN, json.dumps(out))

    def test_list_can_read_back_multiple_maximal_records(self):
        sections = [{"heading": "Evidence", "body": "x" * 1000} for _ in range(12)]
        for index in range(2):
            code, out = self.cli("report", "--id", f"elsie.large-{index}", "--agent", "elsie",
                                 "--title", "Large", "--summary", "s",
                                 *[arg for section in sections for arg in ("--section", "Evidence=" + section["body"])])
            self.assertEqual(code, 0, out)
        code, out = self.cli("records")
        self.assertEqual(code, 0, out)
        self.assertEqual([r["id"] for r in out["result"]], ["elsie.large-0", "elsie.large-1"])

    def test_unreachable_publisher_redacts_url_and_transport_details(self):
        self.write_config(gateway="http://127.0.0.1:9", publisher_token=PUBLISHER_TOKEN)
        code, out = self.cli("records")
        self.assertEqual(code, 1)
        self.assertEqual(out["error"], "publisher gateway unreachable")
