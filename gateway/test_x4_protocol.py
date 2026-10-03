"""Behavior tests for the X4 slide/event contract."""
import unittest

import x4_protocol as p


def card(**overrides):
    frame = {"action": "card", "title": "MORNING", "lines": ["Rain after 3pm.", "Take the blue coat."]}
    frame.update(overrides)
    return frame


def put(slide=None, **request):
    base = {"id": "alice.brief", "owner": "alice", "kind": "note", "frame": card()}
    if slide:
        base.update(slide)
    return {"action": "slide_put", "slide": base, **request}


class SlidePut(unittest.TestCase):
    def test_minimal_slide_gets_explicit_defaults(self):
        got = p.validate(put())
        self.assertEqual(got["intent"], "rotate")
        s = got["slide"]
        self.assertEqual(s["stale_after_s"], 3600)
        self.assertIsNone(s["expires_at"])
        self.assertEqual(s["actions"], {})
        self.assertEqual(s["frame"]["footer"], "")
        self.assertEqual(s["frame"]["avatar"], "none")
        self.assertEqual(s["frame"]["lines"], ["Rain after 3pm.", "Take the blue coat."])

    def test_validation_is_idempotent(self):
        once = p.validate(put({"actions": {"confirm": "ack"}}, intent="hold", hold_s=600))
        self.assertEqual(p.validate(once), once)

    def test_literal_backslash_n_in_text_becomes_a_line_break(self):
        got = p.validate(put({"frame": {"action": "card", "title": "T", "text": "one\\ntwo"}}))
        self.assertEqual(got["slide"]["frame"]["text"], "one\ntwo")

    def test_lines_are_literal_and_never_escape_decoded(self):
        got = p.validate(put({"frame": card(lines=["a\\nb"])}))
        self.assertEqual(got["slide"]["frame"]["lines"], ["a\\nb"])

    def test_x4_limits_are_larger_than_the_clock(self):
        twelve = ["line %d" % i for i in range(12)]
        p.validate(put({"frame": card(lines=twelve, title="x" * 32, footer="f" * 64)}))
        for bad in (card(lines=twelve + ["13"]), card(title="x" * 33), card(footer="f" * 65),
                    card(lines=["x" * 901])):
            with self.assertRaises(ValueError):
                p.validate(put({"frame": bad}))

    def test_unknown_fields_are_rejected_everywhere(self):
        for bad in (put(extra=1), put({"colour": "red"}), put({"frame": card(font="big")})):
            with self.assertRaises(ValueError):
                p.validate(bad)

    def test_control_characters_are_rejected(self):
        with self.assertRaises(ValueError):
            p.validate(put({"frame": card(title="bell\x07")}))

    def test_actions_map_buttons_to_short_labels(self):
        got = p.validate(put({"actions": {"confirm": "ack", "confirm_long": "snooze", "up": "yes", "down": "no"}}))
        self.assertEqual(got["slide"]["actions"]["confirm_long"], "snooze")

    def test_left_right_are_reserved_for_navigation(self):
        for button in ("left", "right", "power", "sideways"):
            with self.assertRaises(ValueError):
                p.validate(put({"actions": {button: "x"}}))

    def test_action_labels_are_bounded_single_line(self):
        for label in ("", "x" * 13, "two\nlines"):
            with self.assertRaises(ValueError):
                p.validate(put({"actions": {"confirm": label}}))

    def test_hold_requires_bounded_hold_s(self):
        self.assertEqual(p.validate(put(intent="hold"))["hold_s"], 900)
        for bad in (put(intent="hold", hold_s=60), put(intent="hold", hold_s=4000), put(hold_s=600)):
            with self.assertRaises(ValueError):
                p.validate(bad)

    def test_expiry_must_be_timezone_aware_and_is_normalised_to_utc(self):
        got = p.validate(put({"expires_at": "2026-10-03T20:00:00-04:00"}))
        self.assertEqual(got["slide"]["expires_at"], "2026-10-04T00:00:00Z")
        with self.assertRaises(ValueError):
            p.validate(put({"expires_at": "2026-10-03T20:00:00"}))

    def test_custom_avatar_rows_are_accepted(self):
        rows = ["01" * 16] * 32
        got = p.validate(put({"frame": card(avatar={"rows": rows})}))
        self.assertEqual(got["slide"]["frame"]["avatar"], {"rows": rows})
        with self.assertRaises(ValueError):
            p.validate(put({"frame": card(avatar={"rows": rows[:31]})}))


class OtherActions(unittest.TestCase):
    def test_simple_actions(self):
        for req in ({"action": "slides"}, {"action": "status"}, {"action": "capabilities"},
                    {"action": "slide_get", "id": "a.b"}, {"action": "slide_remove", "id": "a.b"},
                    {"action": "events", "limit": 10}):
            self.assertEqual(p.validate(req)["action"], req["action"])

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            p.validate({"action": "show", "text": "hi"})

    def test_capabilities_are_honest_about_pull_delivery(self):
        caps = p.capabilities()
        self.assertEqual(caps["geometry"], {"width": 800, "height": 480, "mode": "1"})
        self.assertEqual(caps["delivery"]["model"], "pull")
        self.assertEqual(caps["delivery"]["shown"], "next_wake")
        self.assertIn("confirm", caps["buttons"]["assignable"])


class DeviceEvents(unittest.TestCase):
    def batch(self, **event):
        base = {"seq": 1, "card": "alice.brief", "etag": "r1", "button": "confirm", "press": "short", "wake": "button"}
        base.update(event)
        return {"device": "x4-01", "boot": 3, "events": [base]}

    def test_valid_batch(self):
        got = p.validate_events(self.batch())
        self.assertEqual(got["events"][0]["button"], "confirm")

    def test_bad_events_rejected(self):
        for bad in (self.batch(seq=0), self.batch(button="jump"), self.batch(press="triple"),
                    self.batch(card="../etc"), self.batch(extra=True)):
            with self.assertRaises(ValueError):
                p.validate_events(bad)

    def test_at_most_sixteen_events(self):
        body = self.batch()
        body["events"] = [dict(body["events"][0], seq=i + 1) for i in range(17)]
        with self.assertRaises(ValueError):
            p.validate_events(body)

    def test_card_may_be_null_for_presses_on_the_status_screen(self):
        self.assertIsNone(p.validate_events(self.batch(card=None, etag=None))["events"][0]["card"])


if __name__ == "__main__":
    unittest.main()
