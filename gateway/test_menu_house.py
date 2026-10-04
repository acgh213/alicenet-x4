"""The House destination: read-only rooms from the allowlisted HA snapshot."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import x4_glance
import x4_menu
import x4_records
import test_x4_dashboard as dash
from test_x4_dashboard import NOW, TZ, fixture
from x4_store import Store


def item(label, state, *, available=True, detail="", on=None, domain="light"):
    return {"label": label, "entity": f"{domain}.{label.lower().replace(' ', '_')}", "domain": domain,
            "available": available, "state": state, "detail": detail, "on": on, "changed_at": NOW - 3600}


def house(observed=NOW - 120, **over):
    body = {"available": True, "observed_at": observed, "checked_at": observed, "stale_after_s": 1800,
            "config_key": "k", "rooms": [
                {"name": "Living Room", "items": [
                    item("Light", "off", on=False),
                    item("Thermostat", "cool", detail="70° now · set 74° · idle", on=True, domain="climate"),
                    item("Old thermostat", "unavailable", available=False, domain="climate"),
                    item("TV", "on", on=True, domain="media_player")]},
                {"name": "Bedroom", "items": [item("Light", "on", detail="65%", on=True)]}]}
    body.update(over)
    return body


class House(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.json"
        self.write(house())
        self.store = Store(str(Path(self.tmp.name) / "db"))
        self.glance = x4_glance.Glance(self.store, str(self.path))
        self.menu = x4_menu.Menu(self.glance, x4_records.Records(self.store))
        self.seq = 0

    def write(self, house_body):
        snap = copy.deepcopy(fixture())
        if house_body is not None:
            snap["house"] = house_body
        self.path.write_text(json.dumps(snap))

    def frame(self):
        return self.menu.frame("x4-01", NOW, TZ, 120)

    def press(self, button, kind="short"):
        frame = self.frame()
        self.seq += 1
        return self.menu.handle("x4-01", {"seq": self.seq, "card": frame["card"], "etag": frame["etag"],
                                          "button": button, "press": kind}, now=NOW, tz=TZ)

    def texts(self):
        return "\n".join(i["text"] for i in self.frame()["image"].info["layout"])

    def open_house(self):
        self.press("back")
        while self.menu.state("x4-01")["selected"] != "house":
            self.press("down")
        self.press("confirm")

    def test_menu_row_shows_rooms_not_soon(self):
        self.press("back")
        text = self.texts()
        self.assertIn("2 rooms", text)

    def test_room_list_summarizes_on_and_unavailable(self):
        self.open_house()
        self.assertEqual(self.frame()["card"], "house")
        text = self.texts()
        self.assertIn("Living Room", text)
        self.assertIn("4 devices · 2 on · 1 unavailable", text)
        self.assertIn("Bedroom", text)
        self.assertIn("observed 2 min ago", text)
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_room_page_shows_states_and_never_calls_unavailable_off(self):
        self.open_house()
        self.press("confirm")
        self.assertEqual(self.frame()["card"], "house.living-room")
        text = self.texts()
        for expected in ("Thermostat", "cool", "70° now · set 74° · idle", "TV", "Old thermostat"):
            self.assertIn(expected, text)
        rows = [i["text"] for i in self.frame()["image"].info["layout"]]
        old = rows.index("Old thermostat")
        self.assertIn("UNAVAILABLE", rows[old + 1])
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_up_down_choose_room_and_back_climbs(self):
        self.open_house()
        self.press("down")
        self.press("confirm")
        self.assertEqual(self.frame()["card"], "house.bedroom")
        self.assertIn("65%", self.texts())
        self.press("back")
        self.assertEqual(self.frame()["card"], "house")
        self.press("back")
        self.assertEqual(self.frame()["card"], "menu")

    def test_confirm_on_a_room_page_does_nothing_yet(self):
        self.open_house()
        self.press("confirm")
        before = self.frame()["etag"]
        result = self.press("confirm")
        self.assertEqual((result["moved"], result["label"], result["refresh"]), (False, None, False))
        self.assertEqual(self.frame()["etag"], before)
        self.assertIn("read-only", self.texts())

    def test_long_confirm_refreshes_house_without_muse(self):
        self.open_house()
        result = self.press("confirm", "long")
        self.assertEqual((result["refresh"], result["label"]), (True, None))
        self.press("confirm")
        self.assertTrue(self.press("confirm", "long")["refresh"])

    def test_stale_and_failed_are_said_plainly(self):
        self.write(house(observed=NOW - 20))
        self.open_house()
        self.assertIn("observed just now", self.texts())
        self.write(house(observed=NOW - 3600))
        self.assertIn("STALE · observed 60 min ago", self.texts())
        self.write(house(observed=NOW - 900, refresh_failed=True))
        self.assertIn("refresh failed · showing 15 min old", self.texts())

    def test_small_rooms_use_the_space(self):
        self.open_house()
        self.press("confirm")
        rows = {i["text"]: i["box"] for i in self.frame()["image"].info["layout"]}
        self.assertGreaterEqual(rows["TV"][1] - rows["Old thermostat"][1], 45)
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_unreachable_without_old_data(self):
        self.write({"available": False, "refresh_failed": True})
        self.open_house()
        self.assertIn("Home Assistant unreachable", self.texts())
        self.assertEqual(self.press("confirm")["moved"], False)

    def test_not_configured_says_how_to_set_up(self):
        self.write(None)
        self.open_house()
        self.assertIn("house.json", self.texts())
        self.press("back")  # House -> menu
        self.assertIn("not set up", self.texts())

    def test_rooms_removed_while_open_falls_back_to_room_list(self):
        self.open_house()
        self.press("down")
        self.press("confirm")
        smaller = house()
        smaller["rooms"] = smaller["rooms"][:1]
        self.write(smaller)
        self.assertEqual(self.frame()["card"], "house")

    def test_timer_wake_from_house_returns_home(self):
        self.open_house()
        self.menu.wake("x4-01", "timer")
        self.assertEqual(self.frame()["card"], "glance.home")


if __name__ == "__main__":
    unittest.main()
