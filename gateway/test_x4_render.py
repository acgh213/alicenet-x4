"""Behavior tests for the 800x480 card renderer and PBM encoding."""
import datetime as dt
import unittest

import x4_protocol
import x4_render as r

NOW = dt.datetime(2026, 10, 3, 14, 30, tzinfo=dt.timezone.utc).timestamp()


def slide(**frame_overrides):
    frame = {"action": "card", "title": "MORNING", "lines": ["Rain after 3pm.", "Take the blue coat."]}
    frame.update(frame_overrides)
    req = {"action": "slide_put", "slide": {"id": "alice.brief", "owner": "alice", "frame": frame}}
    return x4_protocol.validate(req)["slide"]


def render(s, **kw):
    kw.setdefault("updated_at", NOW - 60)
    kw.setdefault("now", NOW)
    kw.setdefault("tz", dt.timezone.utc)
    return r.render_slide(s, **kw)


def black_in(img, box):
    return any(img.getpixel((x, y)) == 0 for x in range(box[0], box[2]) for y in range(box[1], box[3]))


class Render(unittest.TestCase):
    def test_full_panel_one_bit(self):
        img = render(slide())
        self.assertEqual((img.size, img.mode), ((800, 480), "1"))

    def test_status_corner_is_left_white_for_the_device(self):
        img = render(slide(title="Tuesday morning briefing!!!!!!", avatar="alice"))
        self.assertFalse(black_in(img, r.STATUS_CORNER))

    def test_title_too_wide_for_one_line_is_rejected(self):
        with self.assertRaises(ValueError):
            render(slide(title="W" * 32, avatar="alice"))

    def test_rendering_is_deterministic(self):
        self.assertEqual(r.to_pbm(render(slide())), r.to_pbm(render(slide())))

    def test_content_that_cannot_fit_is_rejected_not_clipped(self):
        crowded = ["W" * 74] * 12
        with self.assertRaises(ValueError):
            render(slide(lines=crowded))

    def test_twelve_ordinary_lines_fit(self):
        render(slide(lines=["Line %d of a normal briefing card" % i for i in range(12)]))

    def test_long_words_are_broken_rather_than_overflowing(self):
        img = render(slide(lines=["x" * 120]))
        self.assertFalse(black_in(img, (790, 130, 800, 400)))

    def test_avatar_draws_in_the_header_and_none_leaves_it_white(self):
        avatar_box = (24, 16, 120, 112)
        self.assertTrue(black_in(render(slide(avatar="alice")), avatar_box))
        self.assertFalse(black_in(render(slide(avatar="none", title="")), avatar_box))

    def test_custom_avatar_rows_are_drawn_scaled(self):
        rows = ["1" * 32] + ["0" * 32] * 31
        img = render(slide(avatar={"rows": rows}, title=""))
        self.assertEqual(img.getpixel((24, 16)), 0)
        self.assertEqual(img.getpixel((24 + 95, 16 + 2)), 0)
        self.assertEqual(img.getpixel((24, 16 + 3)), 1)

    def test_action_hints_are_drawn_only_when_assigned(self):
        plain = slide()
        acted = dict(plain, actions={"confirm": "ack", "up": "yes"})
        self.assertNotEqual(r.to_pbm(render(plain)), r.to_pbm(render(acted)))

    def test_stale_is_visible(self):
        fresh = render(slide(), updated_at=NOW - 60)
        stale = render(slide(), updated_at=NOW - 7200)
        self.assertNotEqual(r.to_pbm(fresh), r.to_pbm(stale))

    def test_status_screen_renders_a_message(self):
        img = r.render_message("No cards yet", ["Publish with clockctl --target x4."])
        self.assertTrue(black_in(img, (0, 100, 800, 400)))


class Pbm(unittest.TestCase):
    def test_header_size_and_black_bit_polarity(self):
        img = r.blank()
        img.putpixel((0, 0), 0)
        data = r.to_pbm(img)
        header = b"P4\n800 480\n"
        self.assertTrue(data.startswith(header))
        raster = data[len(header):]
        self.assertEqual(len(raster), 48000)
        self.assertEqual(raster[0], 0x80)
        self.assertEqual(raster[1], 0x00)

    def test_etag_is_stable_and_content_addressed(self):
        a = r.to_pbm(render(slide()))
        self.assertEqual(r.etag(a), r.etag(bytes(a)))
        self.assertNotEqual(r.etag(a), r.etag(r.to_pbm(render(slide(title="EVENING")))))


if __name__ == "__main__":
    unittest.main()
