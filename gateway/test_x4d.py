"""End-to-end HTTP tests for x4d: device endpoints, agent endpoints, auth, long-poll."""
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import x4d

DEVICE_TOKEN = "d" * 64
AGENT_TOKEN = "a" * 64


def sha(t):
    return hashlib.sha256(t.encode()).hexdigest()


class Server(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        cfg = {"listen": "127.0.0.1:0", "db": os.path.join(self.dir.name, "x4.db"), "dwell_s": 3600,
               "poll_s": 900, "session_s": 30, "devices": {"x4-01": sha(DEVICE_TOKEN)},
               "agent_token_sha256": sha(AGENT_TOKEN), "allow": ["127.0.0.0/8"], "tz": "UTC"}
        self.app = x4d.App(cfg)
        self.httpd = x4d.make_server(self.app)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def call(self, method, path, body=None, token=DEVICE_TOKEN, headers=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if token:
            req.add_header("Authorization", "Bearer " + token)
        req.add_header("X-Device", "x4-01")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def agent(self, request):
        status, _, body = self.call("POST", "/x4/v1/agent", request, token=AGENT_TOKEN)
        return status, json.loads(body)

    def publish(self, ident, **extra):
        slide = {"id": ident, "owner": "alice", "frame": {"action": "card", "title": ident, "lines": ["hello"]}}
        slide.update(extra)
        status, body = self.agent({"action": "slide_put", "slide": slide})
        self.assertEqual(status, 200, body)
        return body


class Device(Server):
    def test_wrong_or_missing_token_is_401(self):
        self.assertEqual(self.call("GET", "/x4/v1/frame", token="nope")[0], 401)
        self.assertEqual(self.call("GET", "/x4/v1/frame", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/x4/v1/frame", token=AGENT_TOKEN)[0], 401)

    def test_empty_deck_is_204(self):
        self.assertEqual(self.call("GET", "/x4/v1/frame")[0], 204)

    def test_frame_is_exact_pbm_then_304_with_etag(self):
        self.publish("alice.brief", actions={"confirm": "ack"})
        status, headers, body = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "timer"})
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/x-portable-bitmap")
        self.assertTrue(body.startswith(b"P4\n800 480\n"))
        self.assertEqual(len(body), len(b"P4\n800 480\n") + 48000)
        self.assertEqual(headers["X-Card"], "alice.brief")
        self.assertEqual(headers["X-Card-Actions"], "confirm")
        self.assertEqual(headers["X-Session"], "30")
        self.assertEqual(headers["X-Next-Poll"], "900")
        again = self.call("GET", "/x4/v1/frame", headers={"If-None-Match": headers["ETag"]})
        self.assertEqual(again[0], 304)

    def test_cards_without_actions_ask_for_no_session(self):
        self.publish("alice.brief")
        self.assertEqual(self.call("GET", "/x4/v1/frame")[1]["X-Session"], "0")

    def test_device_telemetry_shows_in_agent_status(self):
        self.call("GET", "/x4/v1/frame", headers={"X-Wake": "button", "X-Battery": "3.92V,78", "X-Rssi": "-61"})
        status = self.agent({"action": "status"})[1]["result"]
        self.assertEqual(status["devices"]["x4-01"]["battery"], "3.92V,78")
        self.assertEqual(status["devices"]["x4-01"]["last_wake"], "button")

    def test_events_ack_dedupe_and_navigation(self):
        self.publish("a.one")
        self.publish("a.two")
        first = self.call("GET", "/x4/v1/frame")[1]["X-Card"]
        batch = {"device": "x4-01", "boot": 1, "events": [
            {"seq": 1, "card": first, "etag": "e", "button": "right", "press": "short", "wake": "button"}]}
        status, headers, body = self.call("POST", "/x4/v1/events", batch)
        self.assertEqual((status, json.loads(body)), (200, {"acked": 1}))
        self.assertEqual(headers.get("X-Frame-Changed"), "1")
        second = self.call("GET", "/x4/v1/frame")[1]["X-Card"]
        self.assertNotEqual(first, second)
        # A retried batch (lost ack) must not navigate twice.
        status, headers, _ = self.call("POST", "/x4/v1/events", batch)
        self.assertIsNone(headers.get("X-Frame-Changed"))
        self.assertEqual(self.call("GET", "/x4/v1/frame")[1]["X-Card"], second)

    def test_event_batch_device_must_match_token(self):
        batch = {"device": "x4-99", "boot": 1, "events": []}
        self.assertEqual(self.call("POST", "/x4/v1/events", batch)[0], 403)

    def test_bad_event_body_is_400(self):
        self.assertEqual(self.call("POST", "/x4/v1/events", {"device": "x4-01"})[0], 400)

    def test_long_poll_returns_when_a_card_arrives(self):
        self.publish("a.one")
        etag = self.call("GET", "/x4/v1/frame")[1]["ETag"]
        urgent = {"action": "slide_put", "intent": "next", "slide": {"id": "a.urgent", "frame": {
            "action": "card", "title": "NOW", "lines": ["x"]}}}
        threading.Timer(0.5, self.agent, args=(urgent,)).start()
        start = time.monotonic()
        status, headers, _ = self.call("GET", "/x4/v1/frame?wait=10",
                                       headers={"If-None-Match": etag, "X-Wake": "session"})
        self.assertEqual((status, headers["X-Card"]), (200, "a.urgent"))
        self.assertLess(time.monotonic() - start, 5)

    def test_long_poll_times_out_with_304(self):
        self.publish("a.one")
        etag = self.call("GET", "/x4/v1/frame")[1]["ETag"]
        status = self.call("GET", "/x4/v1/frame?wait=1", headers={"If-None-Match": etag})[0]
        self.assertEqual(status, 304)


class Agent(Server):
    def test_agent_api_needs_the_agent_token(self):
        status, _, _ = self.call("POST", "/x4/v1/agent", {"action": "status"}, token=DEVICE_TOKEN)
        self.assertEqual(status, 401)

    def test_capabilities_and_validation_errors(self):
        status, body = self.agent({"action": "capabilities"})
        self.assertEqual(body["result"]["delivery"]["shown"], "next_wake")
        status, body = self.agent({"action": "slide_put", "slide": {"id": "BAD ID", "frame": {}}})
        self.assertEqual(status, 400)
        self.assertFalse(body["ok"])

    def test_unrenderable_slide_is_rejected_before_storing(self):
        status, body = self.agent({"action": "slide_put", "slide": {"id": "a.huge", "frame": {
            "action": "card", "title": "x", "lines": ["W" * 74] * 12}}})
        self.assertEqual(status, 400)
        self.assertIn("fit", body["error"])
        self.assertIsNone(self.agent({"action": "slide_get", "id": "a.huge"})[1]["result"])

    def test_put_reports_honest_delivery_and_slides_lists(self):
        body = self.publish("a.one")
        self.assertEqual(body["delivery"], "next_wake")
        listed = self.agent({"action": "slides"})[1]["result"]
        self.assertEqual([s["slide"]["id"] for s in listed], ["a.one"])

    def test_preview_png(self):
        self.publish("a.one")
        status, headers, body = self.call("GET", "/x4/v1/preview.png?id=a.one", token=AGENT_TOKEN)
        self.assertEqual((status, headers["Content-Type"]), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))

    def test_allow_list(self):
        self.assertTrue(x4d.allowed("192.168.18.40", ["192.168.18.0/24"]))
        self.assertFalse(x4d.allowed("10.0.0.5", ["192.168.18.0/24"]))
        self.assertFalse(x4d.allowed("garbage", ["192.168.18.0/24"]))


if __name__ == "__main__":
    unittest.main()
