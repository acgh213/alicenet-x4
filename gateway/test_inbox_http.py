"""Real HTTP authenticated normalized report ingress; no live Telegram use."""
import json
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

import test_x4d as http
from test_x4_dashboard import fixture
from test_x4_inbox import config, message
import x4d


class InboxHTTP(http.Server):
    def setUp(self):
        super().setUp()
        self.root = Path(self.dir.name)
        self.cfg, self.glance = self.root / "consent.json", self.root / "glance.json"
        self.cfg.write_text(json.dumps(config()))
        self.glance.write_text(json.dumps(fixture()))
        self.app = x4d.App(dict(self.app.cfg, inbox_config=str(self.cfg), glance_snapshot=str(self.glance)))
        self.httpd.RequestHandlerClass.app = self.app

    def inbox(self, body, token=http.AGENT_TOKEN):
        status, _, raw = self.call("POST", "/x4/v1/inbox", body, token=token)
        return status, json.loads(raw)

    def put(self, **extra):
        return self.inbox({"op": "put", "message": message(**extra)})

    def test_ingress_requires_agent_not_device_token(self):
        for token in (None, "wrong", http.DEVICE_TOKEN):
            self.assertEqual(self.inbox({"op": "put", "message": message()}, token)[0], 401)
        self.assertEqual(self.put()[0], 200)

    def test_explicit_scope_denial_has_no_private_echo(self):
        self.cfg.unlink()
        status, body = self.put(chat="private-not-authorized")
        self.assertEqual(status, 403)
        self.assertNotIn("private-not-authorized", json.dumps(body))
        self.assertEqual(self.inbox({"op": "list"})[1]["result"]["items"], [])

    def test_authenticated_readback_dedupe_remove_and_revocation(self):
        status, first = self.put()
        self.assertEqual(status, 200)
        self.assertEqual(self.put()[1], first)
        snap = self.inbox({"op": "list"})[1]["result"]
        self.assertEqual(len(snap["items"]), 1)
        self.assertIn("Literal report", snap["items"][0]["record"]["summary"])
        self.assertNotIn("fixture-chat", json.dumps(snap))
        self.assertEqual(self.put(text="conflict")[0], 400)
        key = first["result"]["key"]
        self.assertTrue(self.inbox({"op": "remove", "key": key})[1]["result"]["removed"])
        self.assertEqual(self.inbox({"op": "list"})[1]["result"]["items"], [])
        self.put()
        self.cfg.unlink()
        self.assertEqual(self.inbox({"op": "list"})[1]["result"]["items"], [])
        self.assertEqual(self.app.store.pending_forwards(), [])
        self.assertEqual(self.app.records.list(9999999999), [])

    def test_source_failure_can_be_published_without_exception_text_leak(self):
        status, body = self.inbox({"op": "status", "source": "telegram", "chat": "fixture-chat",
                                  "thread": "fixture-thread", "state": "error"})
        self.assertEqual(status, 200)
        self.assertEqual(self.inbox({"op": "list"})[1]["result"]["state"], "error")

    def test_malformed_and_oversized_requests_rejected_without_echo(self):
        for body in (None, [], {"op": "reply"}, {"op": "put", "message": None},
                     {"op": "put", "message": message(text="x" * 18000)},
                     {"op": "put", "message": message(text="\ud800")},
                     {"op": "list", "private-field": "private-value"}):
            with self.subTest(body=repr(body)[:70]):
                status, data = self.inbox(body)
                self.assertEqual(status, 400)
                self.assertNotIn("private-value", json.dumps(data))
                self.assertNotIn("private-field", json.dumps(data))

    def test_database_failure_is_generic_503_and_read_fails_closed(self):
        self.assertTrue(hasattr(self.app, "inbox"), "Inbox service not implemented")
        with patch.object(self.app.inbox, "put", side_effect=sqlite3.OperationalError("private db text")):
            status, body = self.put()
        self.assertEqual(status, 503)
        self.assertNotIn("private db text", json.dumps(body))

    def test_real_slow_body_overall_deadline_and_stall_return_generic_json(self):
        from http.client import HTTPConnection
        import time
        import threading
        import test_x4d as http
        payload = json.dumps({'op': 'put', 'message': message(text='PRIVATE slow report')}).encode()
        for trickle in (False, True):
            conn = HTTPConnection(*self.httpd.server_address, timeout=3)
            self.addCleanup(conn.close)
            conn.putrequest('POST', '/x4/v1/inbox')
            conn.putheader('Authorization', 'Bearer ' + http.AGENT_TOKEN)
            conn.putheader('Content-Length', str(len(payload)))
            stop = threading.Event()
            def send_chunks():
                try:
                    conn.send(payload[:1])
                    if trickle:
                        for byte in payload[1:]:
                            if stop.wait(0.05): break
                            conn.send(bytes([byte]))
                except OSError:
                    pass
            with patch.object(x4d, 'BODY_TIMEOUT_S', 0.3):
                started = time.monotonic()
                conn.endheaders()
                writer = threading.Thread(target=send_chunks)
                writer.start()
                try:
                    response = conn.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 408)
                    self.assertEqual(json.loads(body), {'ok': False, 'error': 'Request body timed out'})
                    self.assertLess(time.monotonic() - started, 0.8)
                finally:
                    stop.set(); writer.join(timeout=2); conn.close()
            self.assertEqual(self.app.inbox.snapshot(time.time())['items'], [])

    def test_real_remove_revoked_after_policy_read_denies_existence_without_echo(self):
        key = self.put()[1]['result']['key']
        policy = self.app.inbox._policy
        def revoke():
            result = policy()
            self.cfg.unlink(missing_ok=True)
            return result
        with patch.object(self.app.inbox, '_policy', side_effect=revoke):
            status, body = self.inbox({'op': 'remove', 'key': key})
        self.assertEqual(status, 403)
        self.assertEqual(body, {'ok': False, 'error': 'Inbox scope not authorized'})
        with self.app.store._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox_messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM inbox_sources').fetchone()[0], 0)

    def test_real_default_deadline_rejects_chunks_at_zero_three_six_seconds(self):
        import socket
        import time
        with socket.create_connection(self.httpd.server_address, timeout=8) as sock:
            sock.sendall((f'POST /x4/v1/inbox HTTP/1.1\r\nHost: localhost\r\n'
                          f'Authorization: Bearer {http.AGENT_TOKEN}\r\nContent-Length: 13\r\n\r\n').encode())
            started = time.monotonic()
            sock.sendall(b'{"op"')
            time.sleep(3)
            sock.sendall(b':"li')
            response = sock.makefile('rb')
            with response:
                status = response.readline()
                headers = []
                while True:
                    line = response.readline()
                    if line == b'\r\n': break
                    self.assertTrue(line)
                    headers.append(line)
                length = next(int(h.split(b':', 1)[1]) for h in headers if h.lower().startswith(b'content-length:'))
                body = response.read(length)
            elapsed = time.monotonic() - started
            self.assertTrue(status.startswith(b'HTTP/1.0 408'), status)
            self.assertEqual(json.loads(body), {'ok': False, 'error': 'Request body timed out'})
            self.assertGreaterEqual(elapsed, 4.5)
            self.assertLess(elapsed, 5.8)
            # Planned final chunk at t=6 is too late; the response is already final.
        self.assertEqual(self.app.inbox.snapshot(time.time())['items'], [])

    def test_body_wire_bounds_and_decode_errors_return_generic_400(self):
        import socket
        for length, payload in (('16385', b''), ('-1', b''), ('private-length', b''),
                                ('2', b'\xff\xff'), ('50', b'{')):
            with self.subTest(length=length), socket.create_connection(self.httpd.server_address, timeout=2) as sock:
                sock.sendall((f'POST /x4/v1/inbox HTTP/1.1\r\nHost: localhost\r\n'
                              f'Authorization: Bearer {http.AGENT_TOKEN}\r\nContent-Length: {length}\r\n\r\n').encode() + payload)
                sock.shutdown(socket.SHUT_WR)
                response = sock.makefile('rb')
                with response:
                    data = response.read()
                self.assertTrue(data.startswith(b'HTTP/1.0 400'), data)
                self.assertIn(b'Invalid Inbox request', data)
                self.assertNotIn(b'private-length', data)

    def test_negative_length_rejected_before_body_read(self):
        import io
        handler = object.__new__(x4d.Handler)
        handler.headers = {"Content-Length": "-1"}
        handler.rfile = io.BytesIO(b'{}' + b' ' * 20000)
        with self.assertRaises(ValueError):
            handler._body()
        self.assertEqual(handler.rfile.tell(), 0)

    def test_legacy_slide_collision_cannot_queue_inbox_forward(self):
        self.publish("inbox", actions={"confirm_long": "reply", "confirm": "ack"})
        self.put()
        self.app.menu._save("x4-01", dict(self.app.menu.state("x4-01"), view="inbox"))
        for seq, kind in enumerate(("long", "short"), 1):
            _, headers, _ = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
            batch = {"device": "x4-01", "boot": 1, "events": [{"seq": seq, "card": headers["X-Card"],
                     "etag": headers["ETag"], "button": "confirm", "press": kind}]}
            self.assertEqual(self.call("POST", "/x4/v1/events", batch)[0], 200)
            self.assertEqual(self.app.store.pending_forwards(), [])

    def test_real_button_flow_no_reply_or_forward(self):
        self.assertEqual(self.put()[0], 200)
        seq = 0
        def press(button, kind="short"):
            nonlocal seq
            status, headers, _ = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
            self.assertEqual(status, 200)
            seq += 1
            batch = {"device": "x4-01", "boot": 1, "events": [{"seq": seq,
                     "card": headers["X-Card"], "etag": headers["ETag"], "button": button,
                     "press": kind}]}
            self.assertEqual(self.call("POST", "/x4/v1/events", batch)[0], 200)
            return batch
        press("back")
        for _ in range(6):
            press("down")
        press("confirm")
        self.assertEqual(self.app.menu.state("x4-01")["view"], "inbox")
        batch = press("confirm")
        self.assertEqual(self.app.menu.state("x4-01")["view"], "inbox_detail")
        self.call("POST", "/x4/v1/events", batch)  # retry, not a second action
        press("confirm", "long")
        press("confirm")
        self.assertEqual(self.app.store.pending_forwards(), [])
        self.assertEqual(self.app.menu.records.list(9999999999), [])


if __name__ == "__main__":
    unittest.main()
