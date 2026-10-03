"""End-to-end: a real x4d on localhost driven by the fake device, the way the firmware will."""
import hashlib
import json
import os
import tempfile
import threading
import unittest

from PIL import Image

import fake_x4
import x4d

TOKEN = "t" * 64
AGENT = "a" * 64


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.forwarded = []
        cfg = {"listen": "127.0.0.1:0", "db": os.path.join(self.dir.name, "x4.db"), "poll_s": 1800,
               "devices": {"x4-01": hashlib.sha256(TOKEN.encode()).hexdigest()},
               "agent_token_sha256": hashlib.sha256(AGENT.encode()).hexdigest(), "tz": "UTC"}
        self.app = x4d.App(cfg)
        self.app.forward_once = self._capture_forward
        self.httpd = x4d.make_server(self.app)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.state = os.path.join(self.dir.name, "dev")
        os.mkdir(self.state)
        self.provision("http://127.0.0.1:%d" % self.httpd.server_address[1])
        self.dev = fake_x4.Device(self.state)

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.dir.cleanup()

    def provision(self, gateway, token=TOKEN):
        with open(os.path.join(self.state, "device.json"), "w") as fh:
            json.dump({"gateway": gateway, "device": "x4-01", "token": token, "timeout_s": 2}, fh)

    def _capture_forward(self):
        for ev in self.app.store.pending_forwards():
            self.forwarded.append((ev["card"], ev["button"], ev["label"]))
            self.app.store.mark_forwarded(ev["rowid"], True)

    def wait_forwarded(self, n, timeout=3.0):
        """x4d forwards on a background thread; wait for it instead of racing it."""
        import time
        deadline = time.monotonic() + timeout
        while len(self.forwarded) < n and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.forwarded

    def publish(self, ident, intent="rotate", actions=None):
        req = {"action": "slide_put", "intent": intent, "slide": {
            "id": ident, "owner": "alice", "actions": actions or {},
            "frame": {"action": "card", "title": ident, "lines": ["body of " + ident]}}}
        self.app.agent(req, now=__import__("time").time())

    def test_first_boot_on_an_empty_deck_keeps_the_status_screen(self):
        self.assertEqual(fake_x4.main(["--state", self.state, "boot"]), 0)
        self.assertEqual(Device(self.state).nvs["frames"], 0)

    def test_publish_wake_draw_then_quiet_wake_does_not_redraw(self):
        self.publish("alice.brief")
        first = self.dev.wake("boot")
        self.assertEqual((first["frame"], first["card"], first["sleep_s"]), ("drawn", "alice.brief", 1800))
        with Image.open(os.path.join(self.state, "screen.png")) as img:
            self.assertEqual((img.size, img.mode), ((800, 480), "1"))
        self.assertEqual(self.dev.wake("timer")["frame"], "kept")
        self.assertEqual(self.dev.nvs["frames"], 1)

    def test_right_press_pages_and_redraws_in_the_same_wake(self):
        self.publish("a.one")
        self.publish("a.two")
        self.dev.wake("boot")
        before = self.dev.nvs["card"]
        self.dev.queue_press("right", "short")
        result = self.dev.wake("button")
        self.assertEqual(result["frame"], "drawn")
        self.assertNotEqual(self.dev.nvs["card"], before)
        self.assertEqual(result["queued"], 0)
        self.assertEqual(self.wait_forwarded(1, timeout=0.5), [])  # navigation is never forwarded

    def test_assigned_press_reaches_the_agent_with_its_label(self):
        self.publish("alice.q", actions={"confirm": "yes", "down": "no"})
        result = self.dev.wake("boot")
        self.assertEqual(result["session_s"], 30)
        self.dev.queue_press("confirm", "short")
        self.dev.wake("button")
        self.assertEqual(self.wait_forwarded(1), [("alice.q", "confirm", "yes")])

    def test_gateway_down_keeps_screen_queues_events_and_backs_off(self):
        self.publish("a.one")
        self.dev.wake("boot")
        self.provision("http://127.0.0.1:9")  # nothing listens on discard
        dev = fake_x4.Device(self.state)
        dev.queue_press("confirm", "short")
        sleeps = [dev.wake("timer")["sleep_s"] for _ in range(3)]
        self.assertEqual(sleeps, [300, 600, 1200])
        self.assertEqual((dev.nvs["frames"], len(dev.nvs["queue"])), (1, 1))

    def test_bad_token_shows_not_provisioned_and_draws_nothing(self):
        self.publish("a.one")
        self.provision("http://127.0.0.1:%d" % self.httpd.server_address[1], token="wrong")
        dev = fake_x4.Device(self.state)
        self.assertEqual(dev.wake("boot")["frame"], "unauthorised")
        self.assertEqual(dev.nvs["frames"], 0)

    def test_queue_overflow_drops_the_oldest(self):
        for _ in range(20):
            self.dev.queue_press("up", "short")
        self.assertEqual([e["seq"] for e in self.dev.nvs["queue"]], list(range(5, 21)))

    def test_events_queued_before_power_off_survive_the_reboot(self):
        self.publish("alice.q", actions={"confirm": "yes"})
        self.dev.wake("boot")
        self.dev.queue_press("confirm", "short")
        self.dev.save()  # power dies before the wake reaches the network
        self.assertEqual(fake_x4.main(["--state", self.state, "boot"]), 0)
        self.assertEqual(self.wait_forwarded(1), [("alice.q", "confirm", "yes")])


Device = fake_x4.Device

if __name__ == "__main__":
    unittest.main()
