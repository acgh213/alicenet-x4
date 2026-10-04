"""Elsie's restricted publisher against a real local x4d HTTP server.

All credentials and records here are synthetic; no live endpoint is contacted.
"""
import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import x4d
from test_x4_dashboard import fixture
from test_x4d import Server, AGENT_TOKEN, DEVICE_TOKEN, sha

PUBLISHER_TOKEN = "p" * 64


def record(ident="elsie.release", agent="elsie", kind="report", **extra):
    return {"id": ident, "agent": agent, "kind": kind, "title": "Release report",
            "summary": "Synthetic publication, not a deployment receipt.", **extra}


class PublisherHTTP(Server):
    def setUp(self):
        super().setUp()
        snapshot = Path(self.dir.name) / "glance.json"
        snapshot.write_text(json.dumps(fixture()))
        self.app = x4d.App(dict(self.app.cfg, glance_snapshot=str(snapshot),
                               elsie_publisher_token_sha256=sha(PUBLISHER_TOKEN)))
        self.httpd.RequestHandlerClass.app = self.app

    def publisher(self, request, token=PUBLISHER_TOKEN):
        status, _, body = self.call("POST", "/x4/v1/publisher", request, token=token)
        return status, json.loads(body)

    def put(self, body=None):
        status, out = self.publisher({"action": "record_put", "record": body or record()})
        self.assertEqual(status, 200, out)
        self.assertEqual(out["principal"], "elsie")
        return out

    def test_publish_read_list_remove_and_idempotent_revision(self):
        first = self.put()
        self.assertEqual((first["delivery"], first["result"]["revision"]), ("next_wake", 1))
        self.assertEqual(self.put()["result"], first["result"])
        got = self.publisher({"action": "record_get", "id": "elsie.release"})[1]
        self.assertEqual((got["principal"], got["result"]["agent"]), ("elsie", "elsie"))
        self.assertEqual(got["result"], first["result"])
        self.assertEqual(self.publisher({"action": "records"})[1]["result"], [first["result"]])
        self.assertEqual(self.publisher({"action": "records", "agent": "elsie"})[1]["result"], [first["result"]])
        self.assertTrue(self.publisher({"action": "record_remove", "id": "elsie.release"})[1]["result"]["removed"])
        self.assertIsNone(self.publisher({"action": "record_get", "id": "elsie.release"})[1]["result"])

    def test_disabled_by_default_and_wrong_credentials(self):
        req = {"action": "records"}
        for token in (None, "wrong", AGENT_TOKEN, DEVICE_TOKEN):
            with self.subTest(token_kind=str(token)[:1]):
                self.assertEqual(self.publisher(req, token=token)[0], 401)
        del self.app.cfg["elsie_publisher_token_sha256"]
        self.assertEqual(self.publisher(req)[0], 401)

    def test_wrong_agent_or_prefix_is_forbidden_without_mutation(self):
        for body in (record(agent="pyrrha"), record(ident="pyrrha.release"),
                     record(ident="elsie-other"), record(ident="elsie")):
            with self.subTest(id=body["id"], agent=body["agent"]):
                self.assertEqual(self.publisher({"action": "record_put", "record": body})[0], 403)
                self.assertIsNone(self.app.records.get(body["id"]))
        for action in ("record_get", "record_remove"):
            self.assertEqual(self.publisher({"action": action, "id": "pyrrha.release"})[0], 403)
        self.assertEqual(self.publisher({"action": "records", "agent": "pyrrha"})[0], 403)

    def test_foreign_records_inside_prefix_and_own_author_outside_prefix_never_leak(self):
        seeds = [record("elsie.foreign", agent="pyrrha", summary="PRIVATE other author"),
                 record("pyrrha.foreign", summary="PRIVATE other namespace")]
        for seed in seeds:
            self.assertEqual(self.agent({"action": "record_put", "record": seed})[0], 200)
        own = self.put()["result"]
        self.assertEqual(self.publisher({"action": "records"})[1]["result"], [own])
        for action in ("record_get", "record_remove"):
            status, out = self.publisher({"action": action, "id": "elsie.foreign"})
            missing = self.publisher({"action": action, "id": "elsie.absent"})
            self.assertEqual((status, out), missing)  # no foreign-existence oracle
            self.assertNotIn("PRIVATE", json.dumps(out))
        status, out = self.publisher({"action": "record_put", "record": record("elsie.foreign")})
        self.assertEqual(status, 403)
        self.assertNotIn("PRIVATE", json.dumps(out))
        self.assertEqual(self.app.records.get("elsie.foreign")["record"]["summary"], seeds[0]["summary"])

    def test_only_typed_records_not_approval_or_other_verbs(self):
        self.put(record(kind="decision"))
        generation = self.app.generation
        for action in ("slide_put", "slide_get", "slide_remove", "slides", "events", "status", "capabilities",
                       "glance", "menu", "house", "record_answer", "record_ack", "config", "admin"):
            with self.subTest(action=action):
                self.assertEqual(self.publisher({"action": action, "id": "elsie.release"})[0], 403)
        self.assertEqual(self.app.generation, generation)
        self.assertEqual(self.app.records.get("elsie.release")["status"], "open")
        self.assertEqual(self.publisher({"action": "record_put", "record": record(kind="slide")})[0], 400)

    def test_publisher_cannot_use_agent_device_or_preview_routes(self):
        for method, path, body in (("POST", "/x4/v1/agent", {"action": "records"}),
                                   ("POST", "/x4/v1/events", {"device": "x4-01", "boot": 1, "events": []}),
                                   ("GET", "/x4/v1/frame", None),
                                   ("GET", "/x4/v1/preview.png?id=private", None),
                                   ("GET", "/x4/v1/glance.png?page=agenda", None)):
            with self.subTest(path=path):
                self.assertEqual(self.call(method, path, body, token=PUBLISHER_TOKEN)[0], 401)
        # Original principals still work, with their original response shape.
        self.assertEqual(self.agent({"action": "status"})[0], 200)
        self.assertNotIn("principal", self.agent({"action": "records"})[1])
        self.assertEqual(self.call("GET", "/x4/v1/frame", token=DEVICE_TOKEN)[0], 200)
        self.assertEqual(self.call("GET", "/x4/v1/glance.png?page=agenda", token=AGENT_TOKEN)[0], 200)

    def test_stale_device_frames_cannot_answer_or_ack_recreated_records(self):
        for kind in ("decision", "report"):
            with self.subTest(kind=kind):
                self.put(record(kind=kind))
                for button in ("back", "down", "down", "confirm", "confirm", "confirm"):
                    # Reset to glance for the second scenario before navigating.
                    status, shown, _ = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
                    seq = getattr(self, "seq", 0) + 1
                    self.seq = seq
                    batch = {"device": "x4-01", "boot": 1, "events": [{"seq": seq, "card": shown["X-Card"],
                             "etag": shown["ETag"], "button": button, "press": "short", "wake": "button"}]}
                    self.assertEqual(self.call("POST", "/x4/v1/events", batch)[0], 200)
                if kind == "decision":
                    status, shown, _ = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
                    self.seq += 1
                    self.call("POST", "/x4/v1/events", {"device": "x4-01", "boot": 1, "events": [{"seq": self.seq,
                              "card": shown["X-Card"], "etag": shown["ETag"], "button": "confirm", "press": "short", "wake": "button"}]})
                _, old, _ = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
                old_revision = self.app.records.get("elsie.release")["revision"]
                self.publisher({"action": "record_remove", "id": "elsie.release"})
                self.put(record(kind=kind, summary="Different replacement content"))
                self.seq += 1
                with patch.object(self.app, "forward_once"):
                    status, _, _ = self.call("POST", "/x4/v1/events", {"device": "x4-01", "boot": 1, "events": [{
                        "seq": self.seq, "card": old["X-Card"], "etag": old["ETag"], "button": "confirm", "press": "short", "wake": "button"}]})
                self.assertEqual(status, 200)
                got = self.publisher({"action": "record_get", "id": "elsie.release"})[1]["result"]
                self.assertGreater(got["revision"], old_revision)
                self.assertEqual(got["status"], "open" if kind == "decision" else "unread")
                self.assertIsNone(got["answer"])
                self.publisher({"action": "record_remove", "id": "elsie.release"})
                self.call("GET", "/x4/v1/frame", headers={"X-Wake": "timer"})

    def test_overflow_expiry_returns_redacted_400(self):
        status, out = self.publisher({"action": "record_put", "record": record(expires_at="0001-01-01T00:00:00+14:00")})
        self.assertEqual((status, out), (400, {"ok": False, "error": "invalid publisher request"}))

    def test_device_answer_is_revision_bound_and_publisher_cannot_forge_it(self):
        self.put(record(kind="decision"))
        buttons = ("back", "down", "down", "confirm", "confirm", "confirm", "confirm", "confirm")
        with patch.object(self.app, "forward_once"):
            for seq, button in enumerate(buttons, 1):
                status, shown, _ = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
                self.assertEqual(status, 200)
                batch = {"device": "x4-01", "boot": 1, "events": [{"seq": seq, "card": shown["X-Card"],
                         "etag": shown["ETag"], "button": button, "press": "short", "wake": "button"}]}
                self.assertEqual(self.call("POST", "/x4/v1/events", batch)[0], 200)
        got = self.publisher({"action": "record_get", "id": "elsie.release"})[1]["result"]
        self.assertEqual((got["status"], got["answered_revision"], got["answer"]), ("answered", 1, "approve"))

    def test_read_ack_answer_and_revision_do_not_confer_human_approval(self):
        self.put()
        self.app.records.ack("elsie.release", 1, now=1)
        self.assertEqual(self.publisher({"action": "record_get", "id": "elsie.release"})[1]["result"]["status"], "read")
        self.put(record(kind="decision"))
        self.assertEqual(self.app.records.answer("elsie.release", 1, "approve", now=2, source="x4-01")["outcome"], "stale")
        self.app.records.answer("elsie.release", 2, "reject", now=3, source="x4-01")
        got = self.publisher({"action": "record_get", "id": "elsie.release"})[1]["result"]
        self.assertEqual((got["revision"], got["answered_revision"], got["answer"]), (2, 2, "reject"))
        revised = self.put(record(kind="decision", summary="Revised question"))["result"]
        self.assertEqual((revised["revision"], revised["status"], revised["answer"], revised["answered_revision"]),
                         (3, "open", None, None))

    def test_validation_errors_and_logs_do_not_echo_request_or_token(self):
        marker = "PRIVATE-MARKER-" + PUBLISHER_TOKEN
        requests = [{"action": "record_get", "id": "elsie.release", marker: marker},
                    {"action": "record_put", "record": record(options=[{}, {}], kind="decision")},
                    {"action": "record_put", "record": record(summary=marker * 20)}]
        with patch.object(self.app, "forward_once") as forward, contextlib.redirect_stderr(io.StringIO()) as logs:
            for req in requests:
                status, out = self.publisher(req)
                self.assertEqual(status, 400, out)
                self.assertNotIn(marker, json.dumps(out))
            forward.assert_not_called()
        self.assertNotIn(PUBLISHER_TOKEN, logs.getvalue())
        self.assertNotIn(marker, logs.getvalue())

    def test_bad_json_and_content_lengths_are_bounded_redacted_errors(self):
        import urllib.error
        import urllib.request
        for data, headers in ((b"{\"PRIVATE-MARKER\"", {}), (b"", {"Content-Length": "-1"}),
                              (b"", {"Content-Length": "oops"}), (b"", {"Content-Length": "16385"})):
            req = urllib.request.Request(self.base + "/x4/v1/publisher", data=data,
                                         headers=dict(headers, Authorization="Bearer " + PUBLISHER_TOKEN), method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(req, timeout=2)
            with caught.exception as err:
                self.assertEqual(err.code, 400)
                self.assertEqual(json.loads(err.read()), {"ok": False, "error": "invalid publisher request"})

    def test_network_allowlist_applies_to_publisher(self):
        self.app.cfg["allow"] = ["192.0.2.0/24"]
        self.assertEqual(self.publisher({"action": "records"})[0], 403)

    def test_runtime_publisher_config_requires_0600_but_original_config_is_unchanged(self):
        path = Path(self.dir.name) / "runtime.json"
        path.write_text(json.dumps(self.app.cfg))
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "0600"):
            x4d.load_config(path)
        path.chmod(0o600)
        self.assertEqual(x4d.load_config(path), self.app.cfg)
        original = dict(self.app.cfg)
        del original["elsie_publisher_token_sha256"]
        path.write_text(json.dumps(original))
        path.chmod(0o644)
        self.assertEqual(x4d.load_config(path), original)

    def test_config_rejects_malformed_or_reused_digests_without_echoing_them(self):
        for digest in ("PRIVATE-MARKER", None, {}, sha(""), sha(AGENT_TOKEN), sha(DEVICE_TOKEN)):
            with self.subTest(digest_type=type(digest).__name__):
                with self.assertRaises(ValueError) as caught:
                    x4d.App(dict(self.app.cfg, elsie_publisher_token_sha256=digest))
                self.assertNotIn("PRIVATE-MARKER", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
