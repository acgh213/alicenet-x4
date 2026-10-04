"""Synthetic dashboard fixtures: never real weather or calendar data."""
import copy
import datetime as dt
import importlib
import importlib.util
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
NOW = dt.datetime(2026, 10, 3, 10, 30, tzinfo=TZ).timestamp()


def fixture():
    return {"observed_at": NOW, "retrieved_at": NOW - 60, "fixture": True,
            "weather": {"available": True, "observed_at": NOW - 240,
                        "stale_after_s": 1800, "condition": "partlycloudy",
                        "temperature": 68, "temperature_unit": "°F", "humidity": 56,
                        "wind_speed": 7, "wind_speed_unit": "mph", "pressure": 1018,
                        "pressure_unit": "hPa", "attribution": "FIXTURE weather"},
            "calendar": {"available": True, "observed_at": NOW - 120,
                         "stale_after_s": 1800, "window_start": "2026-10-03T00:00:00-04:00",
                         "window_end": "2026-10-06T00:00:00-04:00", "events": [
                             {"summary": "FIXTURE · Long walk", "start": "2026-10-03T11:00:00-04:00", "end": "2026-10-03T12:00:00-04:00"},
                             {"summary": "FIXTURE · Reading hour", "start": "2026-10-03T15:00:00-04:00", "end": "2026-10-03T16:00:00-04:00"},
                             {"summary": "FIXTURE · Studio day", "start": "2026-10-04", "end": "2026-10-05"}]}}


class Dashboard(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.find_spec("x4_dashboard")
        self.assertIsNotNone(spec, "dashboard renderer has not been implemented")
        self.renderer = importlib.import_module("x4_dashboard")

    def render(self, snapshot=None, page="home"):
        return self.renderer.render_snapshot(snapshot if snapshot is not None else fixture(), page=page, now=NOW, tz=TZ)

    def texts(self, image):
        return "\n".join(item["text"] for item in image.info["layout"])

    def assert_layout(self, image):
        self.assertEqual((image.mode, image.size), ("1", (800, 480)))
        self.assertEqual(image.crop((640, 0, 800, 40)).convert("L").getextrema(), (255, 255))
        for item in image.info["layout"]:
            left, top, right, bottom = item["ink"]
            x0, y0, x1, y1 = item["box"]
            self.assertTrue(0 <= x0 <= left <= right <= x1 <= 800, item)
            self.assertTrue(0 <= y0 <= top <= bottom <= y1 <= 480, item)
            self.assertFalse(left < 800 and right > 640 and top < 40 and bottom > 0, item)
        for index, a in enumerate(image.info["layout"]):
            for b in image.info["layout"][index + 1:]:
                ax0, ay0, ax1, ay1 = a["ink"]
                bx0, by0, bx1, by1 = b["ink"]
                self.assertFalse(ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1, (a, b))

    def test_all_explicit_pages_are_one_bit_and_reserve_status(self):
        for page in ("home", "weather", "agenda"):
            with self.subTest(page=page):
                image = self.render(page=page)
                self.assert_layout(image)
                text = self.texts(image)
                self.assertIn("◀", text)
                self.assertIn("▶", text)
                self.assertIn("Confirm", text)
                self.assertIn(f"{('home', 'weather', 'agenda').index(page) + 1}/3", text)

    def test_home_samples_local_time_and_shows_multiple_items(self):
        text = self.texts(self.render())
        for expected in ("10:30", "AM", "Saturday October 3", "SAMPLED", "NOT LIVE", "Long walk", "Reading hour", "Studio day"):
            self.assertIn(expected, text)

    def test_sample_time_stays_fixed_while_source_age_advances(self):
        snapshot = fixture()
        snapshot['display_at'] = NOW
        later = NOW + 3600
        image = self.renderer.render_snapshot(snapshot, now=later, tz=TZ)
        text = self.texts(image)
        self.assertIn('10:30', text)
        self.assertNotIn('11:30', text)
        self.assertIn('STALE', text)
        self.assertIn('Checked 1h ago', text)
        self.assert_layout(image)

    def test_empty_is_not_unavailable_or_stale(self):
        snapshot = fixture()
        snapshot["calendar"]["events"] = []
        self.assertIn("No upcoming events", self.texts(self.render(snapshot)))
        snapshot["calendar"]["available"] = False
        text = self.texts(self.render(snapshot))
        self.assertIn("Calendar unavailable", text)
        self.assertNotIn("No upcoming events", text)
        snapshot["calendar"]["available"] = True
        snapshot["calendar"]["observed_at"] = NOW - 7200
        text = self.texts(self.render(snapshot))
        self.assertIn("STALE", text)
        self.assertIn("saved window", text)

    def test_missing_sources_are_unavailable_not_empty(self):
        text = self.texts(self.render({"retrieved_at": NOW}))
        self.assertIn("Weather unavailable", text)
        self.assertIn("Calendar unavailable", text)
        self.assertNotIn("No upcoming events", text)

    def test_weather_metrics_preserve_zero_and_unknown_distinctly(self):
        snapshot = fixture()
        snapshot["weather"].update(temperature=0, humidity=0, wind_speed=0, pressure=None)
        text = self.texts(self.render(snapshot, "weather"))
        for expected in ("0°F", "0%", "0 mph", "Not reported", "FIXTURE weather"):
            self.assertIn(expected, text)
        self.assertNotIn("forecast", text.lower())

    def test_source_age_differs_from_retrieval_age(self):
        snapshot = fixture()
        snapshot["weather"]["observed_at"] = NOW - 7200
        text = self.texts(self.render(snapshot))
        self.assertIn("Weather STALE · 2h", text)
        self.assertIn("Checked 1m ago", text)

    def test_ended_events_are_filtered_and_aware_dates_sorted(self):
        snapshot = fixture()
        snapshot["calendar"]["events"] = [
            {"summary": "Later", "start": "2026-10-04T12:00:00+00:00"},
            {"summary": "Ended", "start": "2026-10-03T08:00:00-04:00", "end": "2026-10-03T09:00:00-04:00"},
            {"summary": "Today all day", "start": "2026-10-03", "end": "2026-10-04"},
            {"summary": "Ongoing", "start": "2026-10-03T10:00:00-04:00", "end": "2026-10-03T11:00:00-04:00"},
            {"summary": "Past all day", "start": "2026-10-02", "end": "2026-10-03"}]
        text = self.texts(self.render(snapshot, "agenda"))
        self.assertNotIn("Ended", text)
        self.assertNotIn("Past all day", text)
        self.assertLess(text.index("Today all day"), text.index("Ongoing"))
        self.assertLess(text.index("Ongoing"), text.index("Later"))
        self.assertIn("All day", text)

    def test_long_titles_and_fields_fit_without_leaking_private_fields(self):
        snapshot = fixture()
        snapshot["calendar"]["events"] *= 4
        for event in snapshot["calendar"]["events"]:
            event.update(summary="Longword" * 120, description="SECRET_DESCRIPTION", location="SECRET_LOCATION")
        snapshot["weather"]["condition"] = "W" * 600
        snapshot["weather"]["attribution"] = "Source " * 100
        for page in ("home", "weather", "agenda"):
            image = self.render(snapshot, page)
            self.assert_layout(image)
            text = self.texts(image)
            self.assertIn("…", text)
            self.assertNotIn("SECRET_", text)

    def test_naive_and_malformed_dates_do_not_crash_or_guess_local_times(self):
        snapshot = fixture()
        snapshot["calendar"]["events"] = [{"summary": "Unknown time", "start": "bad"},
                                             {"summary": "Naive time", "start": "2026-10-03T12:00:00"}]
        text = self.texts(self.render(snapshot, "agenda"))
        self.assertIn("Time unavailable", text)
        self.assertNotIn("12:00 PM", text)

    def test_rendering_is_pure_deterministic_and_rejects_unknown_page(self):
        snapshot = fixture()
        original = copy.deepcopy(snapshot)
        self.assertEqual(self.render(snapshot).tobytes(), self.render(snapshot).tobytes())
        self.assertEqual(snapshot, original)
        with self.assertRaises(ValueError):
            self.render(snapshot, "carousel")

    def test_controls_describe_brief_hold_refresh_back_and_agenda_scroll(self):
        for page in ("home", "weather", "agenda"):
            text = self.texts(self.render(page=page))
            back = "Back: menu" if page == "home" else "Back: home"
            for expected in ("Confirm: brief", "hold: refresh", back):
                self.assertIn(expected, text)
            if page == "agenda":
                self.assertIn("▲ ▼ scroll 5", text)

    def test_attention_badge_is_quiet_and_inside_the_header(self):
        plain = self.render()
        badged = self.renderer.render_snapshot(fixture(), now=NOW, tz=TZ, badge="1 decision · 2 unread")
        self.assertNotIn("decision", self.texts(plain))
        self.assertIn("1 decision · 2 unread", self.texts(badged))
        self.assert_layout(badged)
        badge = [item for item in badged.info["layout"] if "decision" in item["text"]][0]
        self.assertLess(badge["ink"][3], 45)  # header row only; never displaces the dashboard

    def test_agenda_offset_scrolls_five_items_and_home_ignores_it(self):
        snapshot = fixture()
        snapshot["calendar"]["events"] = [{"summary": f"Event {i:02}", "start": "2026-10-04"} for i in range(12)]
        first = self.renderer.render_snapshot(snapshot, page="agenda", now=NOW, tz=TZ)
        second = self.renderer.render_snapshot(snapshot, page="agenda", now=NOW, tz=TZ, offset=5)
        self.assertIn("Event 04", self.texts(first))
        self.assertNotIn("Event 05", self.texts(first))
        self.assertIn("Event 05", self.texts(second))
        self.assertIn("Event 09", self.texts(second))
        self.assertNotIn("Event 04", self.texts(second))
        self.assert_layout(second)
        self.assertEqual(self.render(snapshot).tobytes(), self.renderer.render_snapshot(snapshot, now=NOW, tz=TZ, offset=5).tobytes())

    def test_failed_refresh_keeps_cached_values_but_exposes_failure(self):
        snapshot = fixture()
        for source in (snapshot["weather"], snapshot["calendar"]):
            source.update(refresh_failed=True, checked_at=NOW)
        snapshot["weather"]["observed_at"] = NOW - 7200
        text = self.texts(self.render(snapshot))
        self.assertIn("Weather REFRESH FAILED", text)
        self.assertIn("Calendar REFRESH FAILED", text)
        self.assertIn("STALE", text)
        self.assertIn("68°F", text)
        self.assertIn("Long walk", text)

    def test_source_checked_at_never_freshens_old_observation(self):
        snapshot = fixture()
        snapshot["weather"].update(observed_at=NOW - 7200, checked_at=NOW)
        self.assertIn("Weather STALE · 2h", self.texts(self.render(snapshot)))

    def test_weather_icons_are_distinct_monochrome_primitives(self):
        images = []
        for condition in ("sunny", "clear-night", "cloudy", "rainy", "snowy", "lightning", "fog", "unknown"):
            snapshot = fixture()
            snapshot["weather"]["condition"] = condition
            images.append(self.render(snapshot, "weather").crop((440, 120, 600, 250)).tobytes())
        self.assertEqual(len(set(images)), len(images))


def write_fixture_previews(directory):
    """Explicit fixture-only opt-in; no network/data collector is called."""
    import x4_dashboard
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for page in ("home", "weather", "agenda"):
        image = x4_dashboard.render_snapshot(fixture(), page=page, now=NOW, tz=TZ)
        image.save(directory / f"FIXTURE-{page}.png")
    for state in ("unavailable", "stale", "empty", "refresh-failed"):
        snapshot = fixture()
        if state == "unavailable":
            snapshot["weather"]["available"] = snapshot["calendar"]["available"] = False
        elif state == "refresh-failed":
            snapshot["weather"].update(observed_at=NOW - 7200, refresh_failed=True)
            snapshot["calendar"]["refresh_failed"] = True
        elif state == "stale":
            snapshot["weather"]["observed_at"] = snapshot["calendar"]["observed_at"] = NOW - 7200
        else:
            snapshot["calendar"]["events"] = []
        x4_dashboard.render_snapshot(snapshot, now=NOW, tz=TZ).save(directory / f"FIXTURE-home-{state}.png")


if __name__ == "__main__":
    unittest.main()
