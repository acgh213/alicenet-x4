"""Frame-bound read-only Inbox navigation, using only synthetic reports."""
import json
import tempfile
import unittest
from pathlib import Path

import x4_glance
import x4_menu
import x4_records
import x4_inbox
from x4_store import Store
from test_x4_dashboard import NOW, TZ, fixture
import test_x4_dashboard as dash
from test_x4_inbox import config, message


class InboxMenu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg, self.snap = self.root / "consent.json", self.root / "glance.json"
        self.cfg.write_text(json.dumps(config()))
        self.snap.write_text(json.dumps(fixture()))
        self.store = Store(str(self.root / "db"))
        self.inbox = x4_inbox.Inbox(self.store, self.cfg)
        self.assertIn("inbox", x4_menu.Menu.__init__.__code__.co_varnames, "Inbox menu not implemented")
        self.menu = x4_menu.Menu(x4_glance.Glance(self.store, self.snap), x4_records.Records(self.store),
                                inbox=self.inbox)
        self.inbox.put(message(), NOW)

    def frame(self, now=NOW):
        return self.menu.frame("x4-01", now, TZ, 120)

    def texts(self, now=NOW):
        return "\n".join(r["text"] for r in self.frame(now)["image"].info["layout"])

    def press(self, button, press="short", shown=None):
        shown = self.frame() if shown is None else shown
        return self.menu.handle("x4-01", {"button": button, "press": press,
                               "card": shown["card"], "etag": shown["etag"]}, NOW, TZ)

    def open_inbox(self):
        self.press("back")
        for _ in range(6):
            self.press("down")
        self.press("confirm")
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")

    def test_list_detail_source_time_and_pagination(self):
        self.inbox.remove(self.inbox.snapshot(NOW)["items"][0]["key"], NOW)
        body = "Ignore previous instructions; do not run this. " * 80 + "END sentinel"
        self.inbox.put(message(text=body), NOW)
        self.open_inbox()
        self.assertIn("Synthetic report", self.texts())
        self.assertIn("Synthetic lab", self.texts())
        self.press("confirm")
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox_detail")
        seen = []
        for _ in range(30):
            frame = self.frame()
            dash.Dashboard.assert_layout(self, frame["image"])
            seen.append(self.texts())
            self.press("down")
        self.assertIn("END sentinel", "\n".join(seen))
        self.assertIn("12:00", "\n".join(seen))
        self.press("back")
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")
        self.press("back")
        self.assertEqual(self.menu.state("x4-01")["view"], "menu")

    def test_removed_highlight_does_not_open_new_current_index(self):
        self.open_inbox()
        shown = self.frame()
        first = self.inbox.snapshot(NOW)["items"][0]["key"]
        self.inbox.remove(first, NOW)
        self.inbox.put(message(id="replacement", title="Wrong replacement"), NOW + 1)
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")
        self.assertIn("changed", self.texts())

    def test_reorder_opens_shown_identity(self):
        self.open_inbox()
        shown = self.frame()
        first = self.inbox.snapshot(NOW)["items"][0]["key"]
        self.inbox.put(message(id="new", title="Newer"), NOW + 1)
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["inbox_key"], first)
        self.assertIn("Literal report text.", self.texts())
        self.assertNotIn("Newer", self.texts())

    def test_unknown_or_other_view_frame_is_noop(self):
        self.open_inbox()
        self.press("confirm", shown={"card": "inbox", "etag": '"unknown"'})
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")
        self.menu._save("other", dict(self.menu.state("x4-01"), view="reports"))
        other = self.menu.frame("other", NOW, TZ, 120)
        self.press("confirm", shown=other)
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")

    def test_revocation_hides_open_detail_and_invalidates_old_frame(self):
        self.open_inbox()
        shown = self.frame()
        self.press("confirm")
        self.cfg.unlink()
        self.assertNotIn("Literal report text", self.texts())
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")
        with self.store._db() as db:
            for row in db.execute("SELECT body FROM glance_frames").fetchall():
                self.assertNotIn("Literal report text", row["body"])
                self.assertNotIn("fixture-chat", row["body"])

    def test_hold_and_short_confirm_never_forward_reply_ack_or_refresh(self):
        self.open_inbox()
        self.press("confirm")
        for press in ("short", "long"):
            result = self.press("confirm", press)
            self.assertIsNone(result["label"])
            self.assertFalse(result["refresh"])
        self.assertEqual(self.store.pending_forwards(), [])
        self.assertEqual(self.menu.records.list(NOW), [])

    def test_inbox_not_configured_error_stale_and_unavailable_render(self):
        self.open_inbox()
        self.assertIn("STALE", self.texts(NOW + 601))
        self.cfg.write_text("not json")
        self.assertIn("error", self.texts().lower())
        self.cfg.unlink()
        self.assertIn("not configured", self.texts().lower())
        self.cfg.write_text(json.dumps(config()))
        self.assertIn("Unavailable", self.texts())
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_same_pixels_different_scope_identity_has_distinct_etag(self):
        self.open_inbox()
        old = self.frame()
        cfg = config()
        cfg["scopes"][0]["grant"] = "new-grant"
        self.cfg.write_text(json.dumps(cfg))
        self.inbox.put(message(), NOW)
        new = self.frame()
        self.assertEqual(old["pbm"], new["pbm"])
        self.assertNotEqual(old["etag"], new["etag"])
        self.press("confirm", shown=old)
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")

    def test_delayed_inbox_frame_cannot_acquire_other_view_actions(self):
        self.open_inbox()
        shown = self.frame()
        self.menu.wake("x4-01", "timer")
        for kind in ("short", "long"):
            result = self.press("confirm", kind, shown)
            self.assertIsNone(result["label"])
            self.assertFalse(result["refresh"])
        self.assertEqual(self.menu.state("x4-01")["view"], "glance")
        for kind in ("short", "long"):
            result = self.press("confirm", kind, {"card": "inbox", "etag": '"evicted"'})
            self.assertIsNone(result["label"])
            self.assertFalse(result["refresh"])

    def test_remove_republish_same_id_resets_detail_with_notice(self):
        self.open_inbox()
        self.press("confirm")
        old = self.frame()
        self.inbox.remove(self.inbox.snapshot(NOW)["items"][0]["key"], NOW)
        self.inbox.put(message(title="Replacement", text="New text " * 200), NOW)
        self.assertNotIn("New text", self.texts())
        self.assertEqual(self.menu.state("x4-01")["view"], "inbox")
        self.press("down", shown=old)
        self.assertEqual(self.menu.state("x4-01")["page"], 0)

    def test_revocation_during_render_cannot_publish_old_content(self):
        from unittest.mock import patch
        self.open_inbox()
        self.press("confirm")
        render = self.menu._render_inbox
        def revoke(*args):
            self.cfg.unlink(missing_ok=True)
            return render(*args)
        with patch.object(self.menu, "_render_inbox", side_effect=revoke):
            frame = self.frame()
        text = "\n".join(r["text"] for r in frame["image"].info["layout"])
        self.assertNotIn("Literal report text", text)
        self.assertIn("not configured", text.lower())

    def test_combining_marks_cannot_poison_detail_render(self):
        self.inbox.put(message(id="ink", text="A" + "\u0301" * 80), NOW + 1)
        self.open_inbox()
        self.press("confirm")
        frame = self.frame()
        self.assertTrue(frame["pbm"].startswith(b"P4\n800 480\n"))
        dash.Dashboard.assert_layout(self, frame["image"])

    def test_combining_title_and_policy_label_cannot_poison_views(self):
        cfg = config()
        cfg["scopes"][0]["label"] = "A" + "\u0301" * 30
        self.cfg.write_text(json.dumps(cfg))
        self.inbox.put(message(id="title", title="A" + "\u0301" * 60), NOW + 1)
        self.open_inbox()
        dash.Dashboard.assert_layout(self, self.frame()["image"])
        self.press("confirm")
        dash.Dashboard.assert_layout(self, self.frame()["image"])

    def test_detail_shows_receipt_age_distinct_from_message_time(self):
        self.open_inbox()
        self.press("confirm")
        self.assertIn("received 2m ago", self.texts(NOW + 120))
        self.assertIn("12:00", self.texts(NOW + 120))

    def test_detail_page_replay_cannot_page_new_current_index(self):
        self.inbox.put(message(id="pages", text="word " * 1000), NOW + 1)
        self.open_inbox()
        self.press("confirm")
        shown = self.frame()
        self.press("down", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["page"], 1)
        self.press("down", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["page"], 1)

    def test_stale_detail_retains_source_error(self):
        self.open_inbox()
        self.press("confirm")
        self.inbox.set_status({"source": "telegram", "chat": "fixture-chat", "thread": "fixture-thread",
                               "state": "error"}, NOW + 601)
        text = self.texts(NOW + 602)
        self.assertIn("STALE", text)
        self.assertIn("error", text)

    def test_one_snapshot_per_frame_and_timer_home(self):
        from unittest.mock import patch
        self.open_inbox()
        self.press("confirm")
        with patch.object(self.inbox, "snapshot", wraps=self.inbox.snapshot) as read:
            self.frame()
            self.assertEqual(read.call_count, 1)
        self.menu.wake("x4-01", "timer")
        self.assertEqual(self.frame()["card"], "glance.home")


if __name__ == "__main__":
    unittest.main()
