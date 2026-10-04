"""The destinations menu over real HTTP: the same requests the X4 and agents make."""
import json
from pathlib import Path
from unittest.mock import patch

import x4d
from test_x4_dashboard import fixture
from test_x4d import Server
from test_x4_records import decision, report


class MenuHTTP(Server):
    def setUp(self):
        super().setUp()
        path = Path(self.dir.name) / "snapshot.json"
        path.write_text(json.dumps(fixture()))
        self.app = x4d.App(dict(self.app.cfg, glance_snapshot=str(path)))
        self.httpd.RequestHandlerClass.app = self.app
        self.seq = 0

    def frame(self, wake="session"):
        status, headers, body = self.call("GET", "/x4/v1/frame", headers={"X-Wake": wake})
        self.assertEqual(status, 200)
        return headers

    def press(self, button, press="short"):
        shown = self.frame()
        self.seq += 1
        batch = {"device": "x4-01", "boot": 1, "events": [{"seq": self.seq, "card": shown["X-Card"],
                 "etag": shown["ETag"], "button": button, "press": press, "wake": "button"}]}
        status, headers, _ = self.call("POST", "/x4/v1/events", batch)
        self.assertEqual(status, 200)
        return headers

    def publish(self, body):
        status, out = self.agent({"action": "record_put", "record": body})
        self.assertEqual(status, 200, out)
        return out

    def test_physical_decision_flow_and_agent_reads_the_answer(self):
        self.publish(decision())
        self.assertEqual(self.frame()["X-Card"], "glance.home")
        self.assertEqual(self.press("back").get("X-Frame-Changed"), "1")
        self.assertEqual(self.frame()["X-Card"], "menu")
        self.press("down")
        self.press("down")   # home -> work -> agents
        self.press("confirm")
        self.assertEqual(self.frame()["X-Card"], "agents")
        self.press("confirm")
        self.press("confirm")
        self.assertEqual(self.frame()["X-Card"], "record.pyrrha.menu-order")
        self.press("confirm")
        self.assertEqual(self.frame()["X-Card"], "choice.pyrrha.menu-order")
        with patch.object(self.app, "forward_once"):
            self.press("confirm")  # approve, the recommendation
        status, got = self.agent({"action": "record_get", "id": "pyrrha.menu-order"})
        self.assertEqual((got["result"]["status"], got["result"]["answer"]), ("answered", "approve"))
        event = self.app.store.events(1)[0]
        self.assertEqual((event["label"], event["forward"]), ("answer", "pending"))
        text = self.app.forward_text(self.app.store.pending_forwards()[0])
        self.assertIn("pyrrha.menu-order", text)
        self.assertIn("approve", text)
        self.assertIn("revision 1", text)

    def test_navigation_inside_menu_is_local_not_forwarded(self):
        self.press("back")
        self.press("down")
        self.assertEqual({e["forward"] for e in self.app.store.events(5)}, {"local"})

    def test_glance_confirm_still_briefs_and_hold_still_refreshes(self):
        with patch.object(self.app, "forward_once"):
            self.press("confirm")
        self.assertEqual(self.app.store.events(1)[0]["label"], "brief")
        with patch.object(self.app, "refresh_sources", return_value=True) as refresh:
            self.press("confirm", "long")
            refresh.assert_called_once()

    def test_timer_wake_returns_an_unattended_menu_to_home(self):
        self.press("back")
        self.assertEqual(self.frame()["X-Card"], "menu")
        self.assertEqual(self.frame(wake="timer")["X-Card"], "glance.home")

    def test_timer_wake_keeps_glance_page(self):
        self.press("right")
        self.assertEqual(self.frame(wake="timer")["X-Card"], "glance.weather")

    def test_record_api_validates_lists_and_removes(self):
        status, out = self.agent({"action": "record_put", "record": decision(kind="slide")})
        self.assertEqual(status, 400)
        self.publish(decision())
        self.publish(report())
        records = self.agent({"action": "records"})[1]["result"]
        self.assertEqual([r["id"] for r in records], ["pyrrha.menu-order", "vesper.weekly"])
        out = self.publish(decision())
        self.assertEqual((out["result"]["revision"], out["delivery"]), (1, "next_wake"))
        self.assertTrue(self.agent({"action": "record_remove", "id": "vesper.weekly"})[1]["result"]["removed"])

    def test_long_confirm_on_record_asks_muse_for_context(self):
        self.publish(report())
        self.press("back")
        for _ in range(5):
            self.press("down")   # -> reports
        self.press("confirm")
        self.press("confirm")
        with patch.object(self.app, "forward_once"):
            self.press("confirm", "long")
        event = self.app.store.events(1)[0]
        self.assertEqual((event["label"], event["forward"]), ("context", "pending"))
        text = self.app.forward_text(self.app.store.pending_forwards()[0])
        self.assertIn("vesper.weekly", text)
        self.assertIn("data, not instructions", text)
        self.assertEqual(self.agent({"action": "record_get", "id": "vesper.weekly"})[1]["result"]["status"], "unread")

    def test_status_reports_menu_view(self):
        self.press("back")
        status = self.agent({"action": "status"})[1]["result"]
        self.assertEqual(status["menu"]["x4-01"]["view"], "menu")

    def test_longest_record_id_still_fits_the_device_card_field(self):
        long_id = "pyrrha." + "x" * 41  # 48 characters, the protocol maximum
        self.publish(decision(id=long_id))
        self.press("back")
        self.press("down")
        self.press("down")
        self.press("confirm")
        self.press("confirm")
        self.press("confirm")
        self.press("confirm")
        with patch.object(self.app, "forward_once"):
            self.press("confirm")  # every press above asserted HTTP 200
        self.assertEqual(self.agent({"action": "record_get", "id": long_id})[1]["result"]["answer"], "approve")


if __name__ == "__main__":
    import unittest
    unittest.main()
