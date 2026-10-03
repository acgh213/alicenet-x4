"""Behavior tests for the x4d SQLite store: deck selection, devices, events."""
import os
import tempfile
import unittest

import x4_protocol as p
import x4_store as s


def put(ident, intent="rotate", expires_at=None, actions=None, title="T", **extra):
    slide = {"id": ident, "owner": "alice", "frame": {"action": "card", "title": title, "lines": [ident]},
             "expires_at": expires_at, "actions": actions or {}}
    return p.validate({"action": "slide_put", "slide": slide, "intent": intent, **extra})


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = s.Store(os.path.join(self.dir.name, "x4.db"), dwell_s=600)
        self.t = 1_000_000.0

    def tearDown(self):
        self.dir.cleanup()

    def current(self, wake="timer"):
        cur = self.store.current(now=self.t, wake=wake)
        return cur["id"] if cur else None


class PrivateDatabase(Base):
    def test_calendar_context_database_is_owner_only(self):
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777,0o600)
        os.chmod(self.store.path,0o644)
        s.Store(self.store.path)
        self.assertEqual(os.stat(self.store.path).st_mode & 0o777,0o600)


class Deck(Base):
    def test_empty_deck_has_no_current_card(self):
        self.assertIsNone(self.current())

    def test_put_get_remove_roundtrip_keeps_canonical_slide(self):
        req = put("a.one")
        self.store.put(req, now=self.t)
        self.assertEqual(self.store.get("a.one")["slide"], req["slide"])
        self.assertTrue(self.store.remove("a.one"))
        self.assertIsNone(self.store.get("a.one"))
        self.assertFalse(self.store.remove("a.one"))

    def test_republishing_same_content_does_not_bump_revision(self):
        self.store.put(put("a.one"), now=self.t)
        rev = self.store.get("a.one")["revision"]
        self.store.put(put("a.one"), now=self.t + 50)
        self.assertEqual(self.store.get("a.one")["revision"], rev)
        self.store.put(put("a.one", title="changed"), now=self.t + 60)
        self.assertGreater(self.store.get("a.one")["revision"], rev)

    def test_timer_wakes_rotate_only_after_dwell(self):
        for i in ("a.one", "a.two", "a.three"):
            self.store.put(put(i), now=self.t)
        first = self.current()
        self.t += 300
        self.assertEqual(self.current(), first)
        self.t += 301
        self.assertNotEqual(self.current(), first)

    def test_button_and_session_wakes_never_auto_rotate(self):
        self.store.put(put("a.one"), now=self.t)
        self.store.put(put("a.two"), now=self.t)
        first = self.current()
        self.t += 5000
        self.assertEqual(self.current(wake="button"), first)
        self.assertEqual(self.current(wake="session"), first)

    def test_next_intent_shows_that_card_on_the_next_fetch(self):
        self.store.put(put("a.one"), now=self.t)
        self.store.put(put("a.two"), now=self.t)
        self.store.put(put("a.urgent", intent="next"), now=self.t)
        self.assertEqual(self.current(), "a.urgent")

    def test_hold_pins_until_it_expires(self):
        self.store.put(put("a.one"), now=self.t)
        self.store.put(put("a.held", intent="hold", hold_s=900), now=self.t)
        self.t += 800
        self.assertEqual(self.current(), "a.held")
        self.t += 2000
        self.store.navigate(+1, now=self.t)
        self.assertEqual(self.store.status(now=self.t)["hold"], None)

    def test_expired_slides_are_skipped(self):
        self.store.put(put("a.gone", expires_at="1970-01-12T13:46:40Z"), now=self.t)  # == 1_000_000
        self.store.put(put("a.live"), now=self.t)
        self.assertEqual(self.current(), "a.live")

    def test_navigate_moves_through_the_deck_and_wraps(self):
        for i in ("a.1", "a.2", "a.3"):
            self.store.put(put(i), now=self.t)
        seen = [self.current(wake="button")]
        for _ in range(3):
            self.store.navigate(+1, now=self.t)
            seen.append(self.current(wake="session"))
        self.assertEqual(seen[0], seen[3])
        self.assertEqual(len(set(seen[:3])), 3)
        self.store.navigate(-1, now=self.t)
        self.assertEqual(self.current(wake="session"), seen[2])

    def test_deck_is_capped(self):
        for i in range(s.MAX_SLIDES):
            self.store.put(put("a.%d" % i), now=self.t)
        with self.assertRaises(ValueError):
            self.store.put(put("a.overflow"), now=self.t)
        self.store.put(put("a.0", title="update ok"), now=self.t)


class Devices(Base):
    def test_seen_records_telemetry(self):
        self.store.seen("x4-01", now=self.t, wake="timer", battery="3.92V,78", rssi=-61, fw="alicenet-x4/0.1.0")
        dev = self.store.status(now=self.t + 10)["devices"]["x4-01"]
        self.assertEqual(dev["battery"], "3.92V,78")
        self.assertEqual(dev["age_s"], 10)


class Events(Base):
    def batch(self, *seqs, button="confirm", boot=1, card="a.one"):
        return p.validate_events({"device": "x4-01", "boot": boot, "events": [
            {"seq": n, "card": card, "etag": "e", "button": button, "press": "short", "wake": "button"} for n in seqs]})

    def test_events_are_idempotent_per_device_boot_seq(self):
        self.assertEqual(len(self.store.record_events(self.batch(1, 2), now=self.t)), 2)
        self.assertEqual([e["seq"] for e in self.store.record_events(self.batch(2, 3), now=self.t)], [3])
        self.assertEqual(len(self.store.record_events(self.batch(1, boot=2), now=self.t)), 1)
        self.assertEqual(len(self.store.events(limit=50)), 4)

    def test_ack_is_highest_seq_received_this_boot(self):
        self.store.record_events(self.batch(4, 5), now=self.t)
        self.assertEqual(self.store.acked("x4-01", 1), 5)
        self.assertEqual(self.store.acked("x4-01", 9), 0)

    def test_pending_forwards_only_presses_the_card_assigned(self):
        self.store.put(put("a.one", actions={"confirm": "got it"}), now=self.t)
        self.store.record_events(self.batch(1, button="confirm"), now=self.t)
        self.store.record_events(self.batch(2, button="up"), now=self.t)
        self.store.record_events(self.batch(3, button="right"), now=self.t)
        pending = self.store.pending_forwards()
        self.assertEqual([(e["seq"], e["label"]) for e in pending], [(1, "got it")])
        self.store.mark_forwarded(pending[0]["rowid"], ok=True)
        self.assertEqual(self.store.pending_forwards(), [])

    def test_long_confirm_maps_to_confirm_long_and_is_not_confused_with_short(self):
        self.store.put(put("a.one", actions={"confirm": "ok", "confirm_long": "snooze"}), now=self.t)
        batch = p.validate_events({"device": "x4-01", "boot": 1, "events": [
            {"seq": 1, "card": "a.one", "button": "confirm", "press": "long"},
            {"seq": 2, "card": "a.one", "button": "confirm", "press": "short"}]})
        self.store.record_events(batch, now=self.t)
        self.assertEqual([e["label"] for e in self.store.pending_forwards()], ["snooze", "ok"])

    def test_unassigned_presses_are_recorded_but_never_pending(self):
        self.store.record_events(self.batch(1, card=None), now=self.t)
        self.assertEqual(self.store.pending_forwards(), [])
        self.assertEqual(self.store.events(limit=5)[0]["forward"], "local")


if __name__ == "__main__":
    unittest.main()
