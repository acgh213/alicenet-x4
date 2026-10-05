"""The persistent-destination menu: the button grammar from docs/interaction-model.md."""
import json
import tempfile
import unittest
from pathlib import Path

import x4_glance
import x4_records
import test_x4_dashboard as dash
from test_x4_dashboard import NOW, TZ, fixture
from test_x4_records import decision, report
from x4_store import Store


class Menu(unittest.TestCase):
    def setUp(self):
        import x4_menu
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        path = Path(self.tmp.name) / "snapshot.json"
        path.write_text(json.dumps(fixture()))
        self.store = Store(str(Path(self.tmp.name) / "db"))
        self.glance = x4_glance.Glance(self.store, str(path))
        self.records = x4_records.Records(self.store)
        self.menu = x4_menu.Menu(self.glance, self.records)
        self.seq = 0

    def put(self, body, now=NOW):
        return self.records.put(x4_records.validate_record(body), now=now)

    def frame(self):
        return self.menu.frame("x4-01", NOW, TZ, 120)

    def press(self, button, press="short", frame=None):
        frame = frame or self.frame()
        self.seq += 1
        ev = {"seq": self.seq, "card": frame["card"], "etag": frame["etag"], "button": button, "press": press}
        return self.menu.handle("x4-01", ev, now=NOW, tz=TZ)

    def view(self):
        return self.menu.state("x4-01")["view"]

    def texts(self, frame=None):
        return "\n".join(item["text"] for item in (frame or self.frame())["image"].info["layout"])

    def open_destination(self, name):
        self.press("back")  # Home -> menu
        while self.menu.state("x4-01")["selected"] != name:
            self.press("down")
        self.press("confirm")

    # ---- ambient default stays sacred --------------------------------------------------
    def test_default_is_glance_home_and_glance_paging_still_works(self):
        self.assertEqual(self.frame()["card"], "glance.home")
        self.assertTrue(self.press("right")["moved"])
        self.assertEqual(self.frame()["card"], "glance.weather")
        self.press("back")  # Weather -> Home, not the menu
        self.assertEqual(self.frame()["card"], "glance.home")

    def test_agent_records_never_displace_home(self):
        self.put(decision())
        self.assertEqual(self.frame()["card"], "glance.home")

    def test_open_attention_shows_as_badge_on_home(self):
        before = self.frame()
        self.put(decision())
        after = self.frame()
        self.assertNotEqual(before["etag"], after["etag"])
        self.assertIn("1 decision", self.texts(after))

    def test_glance_confirm_still_briefs_and_long_confirm_refreshes(self):
        self.assertEqual(self.press("confirm")["label"], "brief")
        result = self.press("confirm", "long")
        self.assertTrue(result["refresh"])
        self.assertIsNone(result["label"])

    # ---- the menu -----------------------------------------------------------------------
    def test_back_on_home_opens_destinations_in_order(self):
        self.press("back")
        self.assertEqual(self.view(), "menu")
        frame = self.frame()
        self.assertEqual(frame["card"], "menu")
        text = self.texts(frame)
        positions = [text.index(name) for name in
                     ("Home", "Work", "Agents", "House", "Life", "Reports", "Inbox", "Status")]
        self.assertEqual(positions, sorted(positions))
        self.press("back")
        self.assertEqual(self.frame()["card"], "glance.home")

    def test_menu_selection_wraps_and_confirm_home_returns_to_dashboard(self):
        self.press("back")
        self.press("up")
        self.assertEqual(self.menu.state("x4-01")["selected"], "status")
        self.press("down")
        self.assertEqual(self.menu.state("x4-01")["selected"], "home")
        self.press("confirm")
        self.assertEqual(self.frame()["card"], "glance.home")

    def test_menu_badges_count_open_decisions_and_unread_reports(self):
        self.put(decision())
        self.put(report())
        self.press("back")
        text = self.texts()
        self.assertIn("1 decision", text)
        self.assertIn("1 unread", text)

    def test_integrated_destinations_show_real_read_only_views(self):
        for name, card, text in (("life", "life.", "Life · Now"),
                                 ("inbox", "inbox", "Inbox not configured")):
            with self.subTest(name=name):
                self.menu.go_home("x4-01")
                self.open_destination(name)
                self.assertEqual(self.view(), name)
                self.assertTrue(self.frame()["card"].startswith(card))
                self.assertIn(text, self.texts())
                self.assertNotIn("Not connected yet", self.texts())
                self.press("back")
                self.assertEqual(self.view(), "menu")

    def test_status_shows_device_and_sources(self):
        self.store.seen("x4-01", now=NOW - 30, wake="button", battery="88", rssi=-41, fw="0.3.1-ambient")
        self.open_destination("status")
        text = self.texts()
        for expected in ("x4-01", "0.3.1-ambient", "-41", "WEATHER", "CALENDAR"):
            self.assertIn(expected, text)

    # ---- agents → decision → choice ------------------------------------------------------
    def test_agents_lists_agents_with_their_attention(self):
        self.put(decision())
        self.put(decision(id="pyrrha.two"))
        self.put(report())
        self.open_destination("agents")
        text = self.texts()
        self.assertIn("Pyrrha", text)
        self.assertIn("2 decisions", text)
        self.assertIn("Vesper", text)
        self.assertLess(text.index("Pyrrha"), text.index("Vesper"))
        self.assertIn("report ready", text)

    def test_full_decision_flow_records_the_chosen_option(self):
        self.put(decision())
        self.open_destination("agents")
        self.press("confirm")  # pyrrha
        self.assertEqual(self.view(), "agent")
        self.press("confirm")  # the decision
        self.assertEqual(self.frame()["card"], "record.pyrrha.menu-order")
        self.assertIn("Ship the menu before House?", self.texts())
        self.press("confirm")  # open the choice view
        self.assertEqual(self.view(), "choice")
        self.assertIn("Recommended: approve", self.texts())
        self.assertEqual(self.menu.state("x4-01")["choice"], "approve")
        self.press("down")
        self.assertEqual(self.menu.state("x4-01")["choice"], "reject")
        result = self.press("confirm")
        self.assertEqual(result["decision"]["outcome"], "answered")
        got = self.records.get("pyrrha.menu-order")
        self.assertEqual((got["status"], got["answer"]), ("answered", "reject"))
        self.assertEqual(self.view(), "record")
        self.assertIn("Answered: reject", self.texts())

    def test_back_from_choice_cancels_without_answering(self):
        self.put(decision())
        self.open_destination("agents")
        self.press("confirm")
        self.press("confirm")
        self.press("confirm")
        self.press("back")
        self.assertEqual(self.view(), "record")
        self.assertEqual(self.records.get("pyrrha.menu-order")["status"], "open")

    def test_choice_against_revised_record_is_refused(self):
        self.put(decision())
        self.open_destination("agents")
        self.press("confirm")
        self.press("confirm")
        self.press("confirm")
        shown = self.frame()  # Cassie is looking at revision 1's choices.
        self.put(decision(summary="Agent revised this while it was on screen."))
        result = self.press("confirm", frame=shown)
        self.assertEqual(result["decision"]["outcome"], "stale")
        self.assertEqual(self.records.get("pyrrha.menu-order")["status"], "open")
        self.assertIn("changed", self.texts())

    def test_unknown_frame_never_answers(self):
        self.put(decision())
        self.open_destination("agents")
        self.press("confirm")
        self.press("confirm")
        self.press("confirm")
        forged = dict(self.frame(), etag='"not-a-frame-we-rendered"')
        result = self.press("confirm", frame=forged)
        self.assertNotIn("decision", result)
        self.assertEqual(self.records.get("pyrrha.menu-order")["status"], "open")

    def test_long_confirm_on_record_asks_muse_for_context_not_approval(self):
        self.put(decision())
        self.open_destination("agents")
        self.press("confirm")
        self.press("confirm")
        result = self.press("confirm", "long")
        self.assertEqual(result["label"], "context")
        self.assertEqual(self.records.get("pyrrha.menu-order")["status"], "open")
        self.assertIn("pyrrha.menu-order", result["context"])

    # ---- reports ----------------------------------------------------------------------
    def test_reports_list_opens_the_report_that_is_highlighted(self):
        self.put(decision())
        self.put(report())
        self.put(report(id="eido.run", agent="eido", title="Run complete"))
        self.open_destination("reports")
        self.assertNotIn("Ship the menu", self.texts())  # decisions live under Agents
        self.press("down")
        highlighted = [i["text"] for i in self.frame()["image"].info["layout"] if i["text"].startswith("▶")][0]
        self.press("confirm")
        opened = self.records.get(self.menu.state("x4-01")["record"])
        self.assertIn(opened["record"]["title"], highlighted)

    def test_confirm_opens_what_was_shown_even_if_the_list_reordered(self):
        self.put(decision(id="pyrrha.first", title="First question"))
        self.put(decision(id="pyrrha.second", title="Second question"))
        self.open_destination("agents")
        self.press("confirm")
        shown = self.frame()  # cursor on "First question"
        self.records.answer("pyrrha.first", 1, "approve", now=NOW, source="elsewhere")  # now sorts last
        self.press("confirm", frame=shown)
        self.assertEqual(self.menu.state("x4-01")["record"], "pyrrha.first")

    def test_report_pages_and_confirm_marks_read(self):
        long_body = " ".join(f"word{i}" for i in range(150))  # ~1100 chars, under the 1200 limit
        self.put(report(sections=[{"heading": "Findings", "body": long_body},
                                  {"heading": "Concerns", "body": "One concern."}]))
        self.open_destination("reports")
        self.press("confirm")
        self.assertEqual(self.frame()["card"], "record.vesper.weekly")
        pages = self.menu.state("x4-01")["pages"]
        self.assertGreater(pages, 3)
        for _ in range(pages + 3):
            self.press("down")
        self.assertEqual(self.menu.state("x4-01")["page"], pages - 1)
        self.assertIn("Concerns", self.texts())
        self.press("confirm")
        self.assertEqual(self.records.get("vesper.weekly")["status"], "read")

    def test_record_text_is_wrapped_never_clipped(self):
        self.put(report(summary="Line one\nLine two\n" + "x" * 300))
        self.open_destination("reports")
        self.press("confirm")
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_every_view_is_one_bit_with_clear_status_corner(self):
        self.put(decision())
        self.put(report())
        self.press("back")
        dash.Dashboard.assert_layout(self, self.frame()["image"])
        for name in ("agents", "reports", "status", "work"):
            with self.subTest(name=name):
                self.menu.go_home("x4-01")
                self.open_destination(name)
                dash.Dashboard.assert_layout(self, self.frame()["image"])
        self.menu.go_home("x4-01")
        self.open_destination("agents")
        self.press("confirm")
        dash.Dashboard.assert_layout(self, self.frame()["image"])
        self.press("confirm")
        self.press("confirm")
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_state_is_per_device_and_survives_restart(self):
        self.press("back")
        import x4_menu
        again = x4_menu.Menu(x4_glance.Glance(self.store, str(self.glance.path)), self.records)
        self.assertEqual(again.state("x4-01")["view"], "menu")
        self.assertEqual(again.state("other")["view"], "glance")

    def test_record_removed_while_open_falls_back_to_list(self):
        self.put(report())
        self.open_destination("reports")
        self.press("confirm")
        self.records.remove("vesper.weekly")
        self.assertEqual(self.frame()["card"], "reports")


if __name__ == "__main__":
    unittest.main()
