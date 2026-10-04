"""Work views bind Confirm to the displayed public source identity."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import x4_glance
import x4_menu
import x4_records
import x4_work
from x4_store import Store
import test_x4_dashboard as dash
from test_x4_dashboard import NOW, TZ, fixture
from test_x4_work import config, GitHub, HEAD, OLD, pr, run


class WorkMenu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.glance_path = self.root / "glance.json"
        self.glance_path.write_text(json.dumps(fixture()))
        self.path, self.cfg = self.root / "work.json", self.root / "work-config.json"
        self.cfg.write_text(json.dumps(config()))
        self.api = GitHub([pr(7), pr(8)])
        self.snapshot = x4_work.collect(config(), now=NOW, http_get=self.api)
        self.write(self.snapshot)
        self.store = Store(str(self.root / "db"))
        self.glance = x4_glance.Glance(self.store, self.glance_path)
        self.assertIn("work", x4_menu.Menu.__init__.__code__.co_varnames, "Work menu is not implemented")
        self.menu = x4_menu.Menu(self.glance, x4_records.Records(self.store),
                                x4_work.Work(self.path, self.cfg))

    def write(self, snapshot):
        x4_work.write_snapshot(self.path, snapshot)

    def frame(self, now=NOW):
        return self.menu.frame("x4-01", now, TZ, 120)

    def press(self, button, press="short", shown=None):
        shown = self.frame() if shown is None else shown
        return self.menu.handle("x4-01", {"button": button, "press": press, "card": shown["card"],
                                        "etag": shown["etag"]}, NOW, TZ)

    def open_work(self):
        self.press("back")
        self.press("down")
        self.press("confirm")

    def texts(self, now=NOW):
        return "\n".join(row["text"] for row in self.frame(now)["image"].info["layout"])

    def test_work_opens_overview_and_back_returns_parent(self):
        self.open_work()
        self.assertEqual(self.frame()["card"], "work")
        self.assertIn("GITHUB", self.texts())
        self.assertIn("acgh213/alicenet-x4", self.texts())
        self.assertIn("2 open PRs", self.texts())
        self.press("confirm")
        self.assertEqual(self.menu.state("x4-01")["view"], "work_detail")
        self.press("back")
        self.assertEqual(self.frame()["card"], "work")
        self.press("back")
        self.assertEqual(self.frame()["card"], "menu")

    def test_empty_prs_keeps_build_row_and_absent_build_is_not_failure(self):
        self.write(x4_work.collect(config(), now=NOW, http_get=GitHub([], [])))
        self.open_work()
        self.assertIn("No open pull requests", self.texts())
        self.assertIn("No default-branch builds found", self.texts())
        self.assertNotIn("success", self.texts())

    def test_confirm_uses_shown_identity_after_reorder(self):
        self.open_work()
        self.press("down")  # PR 7 after build
        shown = self.frame()
        changed = copy.deepcopy(self.snapshot)
        changed["prs"]["data"]["items"].reverse()
        self.write(changed)
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["work_id"], "gh:acgh213/alicenet-x4:pr:7")

    def test_removed_item_does_not_open_cursor_replacement(self):
        self.open_work()
        self.press("down")
        shown = self.frame()
        changed = copy.deepcopy(self.snapshot)
        changed["prs"]["data"]["items"].pop(0)
        self.write(changed)
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["view"], "work")
        self.assertIn("item changed", self.texts())

    def test_changed_revision_has_notice(self):
        self.open_work()
        self.press("down")
        shown = self.frame()
        changed = copy.deepcopy(self.snapshot)
        changed["prs"]["data"]["items"][0]["title"] = "Revised title"
        self.write(changed)
        self.press("confirm", shown=shown)
        self.assertIn("changed", self.texts())
        self.assertIn("Revised title", self.texts())

    def test_unknown_frame_does_not_open_any_item(self):
        self.open_work()
        self.press("confirm", shown={"card": "work", "etag": '"unknown"'})
        self.assertEqual(self.menu.state("x4-01")["view"], "work")

    def test_work_hold_requests_only_work_refresh(self):
        self.open_work()
        outcome = self.press("confirm", "long")
        self.assertTrue(outcome["refresh"])
        self.assertEqual(outcome["refresh_target"], "work")
        self.assertIsNone(outcome["label"])

    def test_timer_wake_returns_home(self):
        self.open_work()
        self.menu.wake("x4-01", "timer")
        self.assertEqual(self.frame()["card"], "glance.home")

    def test_missing_config_and_corrupt_snapshot_stay_usable(self):
        self.cfg.unlink()
        self.open_work()
        self.assertIn("Work isn't set up yet", self.texts())
        self.press("back")
        self.cfg.write_text(json.dumps(config()))
        self.path.write_text("not json")
        self.press("confirm")
        self.assertIn("Unavailable", self.texts())

    def test_stale_partial_failure_and_older_head_are_explicit(self):
        changed = copy.deepcopy(self.snapshot)
        changed["prs"].update(collected_at=NOW - 3600, refresh_failed=True, error="rate-limited")
        changed["build"]["data"]["run"]["head_sha"] = OLD
        self.write(changed)
        self.open_work()
        text = self.texts()
        self.assertIn("STALE", text)
        self.assertIn("Refresh failed", text)
        self.assertIn("older head", text)
        self.press("confirm")
        self.assertIn(OLD[:8], self.texts())
        self.assertIn(HEAD[:8], self.texts())

    def test_build_outcomes_and_long_titles_fit(self):
        self.open_work()
        for conclusion in ("success", "failure", "cancelled", "skipped", "timed_out", "neutral"):
            with self.subTest(conclusion=conclusion):
                self.api.runs = [run(conclusion=conclusion)]
                self.api.prs = [pr(7, "very long title " * 20)]
                self.write(x4_work.collect(config(), now=NOW, http_get=self.api))
                self.assertIn(conclusion.replace("_", " "), self.texts())
                dash.Dashboard.assert_layout(self, self.frame()["image"])
                self.press("down")
                self.press("confirm")
                dash.Dashboard.assert_layout(self, self.frame()["image"])
                self.press("back")
                self.press("up")
        self.api.runs = [run(status="in_progress", conclusion=None)]
        self.write(x4_work.collect(config(), now=NOW, http_get=self.api))
        self.assertIn("in progress", self.texts())

    def test_detail_pagination_and_removed_item_settles(self):
        self.api.prs = [pr(7, "long " * 100)]
        self.write(x4_work.collect(config(), now=NOW, http_get=self.api))
        self.open_work()
        self.press("down")
        self.press("confirm")
        first = self.frame()
        self.press("down")
        self.assertNotEqual(self.frame()["etag"], first["etag"])
        self.write(x4_work.collect(config(), now=NOW, http_get=GitHub([], [])))
        self.assertEqual(self.frame()["card"], "work")

    def test_absent_build_and_many_prs_fit_without_overlap(self):
        self.write(x4_work.collect(config(), now=NOW, http_get=GitHub([pr(n) for n in range(1, 11)], [])))
        self.open_work()
        self.assertIn("No default-branch builds found", self.texts())
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_snapshot_replaced_between_settle_and_render_does_not_crash(self):
        self.open_work()
        self.press("down")
        self.press("confirm")
        vanished = x4_work.collect(config(), now=NOW, http_get=GitHub([], []))
        with patch.object(self.menu.work, "snapshot", side_effect=[self.snapshot, vanished]):
            frame = self.frame()
        self.assertTrue(frame["pbm"].startswith(b"P4\n800 480\n"))

    def test_malformed_head_reader_returns_unavailable_and_menu_remains_usable(self):
        changed = copy.deepcopy(self.snapshot)
        changed["repository"].update(default_branch=None, head_sha={"bad": 1})
        for section in ("prs", "build"):
            changed[section] = {"available": False, "collected_at": None, "refresh_failed": True,
                                "error": "unavailable", "data": None}
        self.write(changed)
        self.assertIsNone(self.menu.work.snapshot())
        self.open_work()
        self.assertIn("Unavailable", self.texts())
        self.press("back")
        self.assertEqual(self.frame()["card"], "menu")


if __name__ == "__main__":
    unittest.main()
