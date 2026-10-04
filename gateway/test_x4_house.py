"""House data: allowlisted Home Assistant entities, normalized for a 1-bit panel.

Unavailable, unknown and unreadable are never shown as off, closed or zero.
"""
import unittest

import x4_house

NOW = 1_790_000_000


def state(entity, value, **attrs):
    return {"entity_id": entity, "state": value, "attributes": attrs,
            "last_changed": "2026-10-03T20:00:00+00:00", "last_updated": "2026-10-03T20:00:00+00:00"}


class Normalize(unittest.TestCase):
    def norm(self, raw, label="Thing"):
        return x4_house.normalize(raw, label)

    def test_light_on_shows_brightness_percent_off_is_off(self):
        on = self.norm(state("light.a", "on", brightness=166))
        self.assertEqual((on["available"], on["state"], on["detail"], on["on"]), (True, "on", "65%", True))
        off = self.norm(state("light.a", "off"))
        self.assertEqual((off["state"], off["detail"], off["on"]), ("off", "", False))

    def test_unavailable_and_unknown_are_not_off(self):
        for value in ("unavailable", "unknown"):
            item = self.norm(state("light.a", value))
            self.assertEqual((item["available"], item["state"], item["on"]), (False, value, None))

    def test_climate_reads_like_a_thermostat(self):
        item = self.norm(state("climate.t", "cool", current_temperature=70, temperature=74, hvac_action="idle"))
        self.assertEqual((item["state"], item["detail"]), ("cool", "70° now · set 74° · idle"))
        off = self.norm(state("climate.t", "off", current_temperature=69.5, temperature=None))
        self.assertEqual((off["state"], off["detail"], off["on"]), ("off", "69.5° now", False))

    def test_sensor_value_rounded_with_unit(self):
        item = self.norm(state("sensor.t", "68.99999", unit_of_measurement="°F"))
        self.assertEqual((item["state"], item["detail"]), ("69°F", ""))
        self.assertEqual(self.norm(state("sensor.h", "65", unit_of_measurement="%"))["state"], "65%")
        self.assertEqual(self.norm(state("sensor.w", "0.0", unit_of_measurement="W"))["state"], "0 W")

    def test_binary_sensor_uses_device_class_words(self):
        self.assertEqual(self.norm(state("binary_sensor.d", "on", device_class="door"))["state"], "open")
        self.assertEqual(self.norm(state("binary_sensor.d", "off", device_class="door"))["state"], "closed")
        self.assertEqual(self.norm(state("binary_sensor.m", "on", device_class="motion"))["state"], "motion")
        self.assertEqual(self.norm(state("binary_sensor.m", "off", device_class="motion"))["state"], "clear")
        self.assertEqual(self.norm(state("binary_sensor.x", "on"))["state"], "on")

    def test_media_player_state_only_never_what_is_playing(self):
        item = self.norm(state("media_player.tv", "playing", media_title="Something private", app_name="App"))
        self.assertEqual(item["state"], "playing")
        self.assertNotIn("private", repr(item))
        self.assertNotIn("App", repr(item))

    def test_switch_and_changed_at(self):
        item = self.norm(state("switch.washer", "on"), "Washer plug")
        self.assertEqual((item["label"], item["state"], item["on"]), ("Washer plug", "on", True))
        self.assertEqual(item["changed_at"], 1791057600.0)


def config(**over):
    body = {"stale_after_s": 1800, "rooms": [
        {"name": "Living Room", "entities": [{"entity": "light.lr", "label": "Light"},
                                             {"entity": "climate.t", "label": "Thermostat"}]},
        {"name": "Laundry", "entities": [{"entity": "switch.washer", "label": "Washer plug"}]}]}
    body.update(over)
    return body


class Config(unittest.TestCase):
    def test_valid_config_passes_and_is_canonical(self):
        out = x4_house.validate_config(config())
        self.assertEqual([r["name"] for r in out["rooms"]], ["Living Room", "Laundry"])

    def test_rejections(self):
        bad = [
            config(rooms=[]),
            config(rooms=[{"name": "R", "entities": []}]),
            config(rooms=[{"name": "R", "entities": [{"entity": "LIGHT.X", "label": "L"}]}]),
            config(rooms=[{"name": "R", "entities": [{"entity": "person.cassie", "label": "Me"}]}]),
            config(rooms=[{"name": "R", "entities": [{"entity": "device_tracker.phone", "label": "P"}]}]),
            config(rooms=[{"name": "R", "entities": [{"entity": "light.x", "label": "L" * 25}]}]),
            config(rooms=[{"name": "R" * 25, "entities": [{"entity": "light.x", "label": "L"}]}]),
            config(rooms=[{"name": "R", "entities": [{"entity": "light.x", "label": "L"}] * 9}]),
            config(rooms=[{"name": f"R{i}", "entities": [{"entity": "light.x", "label": "L"}]} for i in range(9)]),
            config(extra=True),
            config(rooms=[{"name": "R", "entities": [{"entity": "light.x", "label": "L", "action": "toggle"}]}]),
        ]
        for body in bad:
            with self.assertRaises(ValueError, msg=repr(body)[:120]):
                x4_house.validate_config(body)


class Collect(unittest.TestCase):
    def fetch(self, table):
        def get(entity):
            value = table[entity]
            if isinstance(value, Exception):
                raise value
            return value
        return get

    def test_rooms_items_and_freshness(self):
        out = x4_house.collect(x4_house.validate_config(config()), self.fetch({
            "light.lr": state("light.lr", "off"),
            "climate.t": state("climate.t", "cool", current_temperature=70, temperature=74, hvac_action="idle"),
            "switch.washer": state("switch.washer", "on")}), now=NOW)
        self.assertEqual((out["available"], out["observed_at"], out["stale_after_s"]), (True, NOW, 1800))
        self.assertEqual([i["label"] for i in out["rooms"][0]["items"]], ["Light", "Thermostat"])
        self.assertEqual(out["rooms"][1]["items"][0]["state"], "on")

    def test_one_unreadable_entity_is_marked_not_hidden(self):
        out = x4_house.collect(x4_house.validate_config(config()), self.fetch({
            "light.lr": RuntimeError("404"), "climate.t": state("climate.t", "off"),
            "switch.washer": state("switch.washer", "off")}), now=NOW)
        item = out["rooms"][0]["items"][0]
        self.assertEqual((item["available"], item["state"], item["on"]), (False, "couldn't read", None))

    def test_wrong_entity_in_response_is_unreadable(self):
        out = x4_house.collect(x4_house.validate_config(config()), self.fetch({
            "light.lr": state("light.other", "on"), "climate.t": state("climate.t", "off"),
            "switch.washer": state("switch.washer", "off")}), now=NOW)
        self.assertEqual(out["rooms"][0]["items"][0]["state"], "couldn't read")

    def test_everything_unreadable_raises_so_old_data_is_kept_and_marked(self):
        with self.assertRaises(x4_house.HouseError):
            x4_house.collect(x4_house.validate_config(config()), self.fetch({
                "light.lr": OSError(), "climate.t": OSError(), "switch.washer": OSError()}), now=NOW)


if __name__ == "__main__":
    unittest.main()


class Protected(unittest.TestCase):
    """Some devices must never be switched from the X4 (e.g. the washer's smart plug)."""

    def setUp(self):
        body = config()
        body["rooms"][1]["entities"][0]["protected"] = True
        self.cfg = x4_house.validate_config(body)
        self.house = x4_house.collect(self.cfg, lambda e: state(e, "on", brightness=255), now=NOW)

    def items(self):
        return {i["entity"]: i for room in self.house["rooms"] for i in room["items"]}

    def test_protected_flag_is_validated_and_carried_to_the_snapshot(self):
        self.assertTrue(self.items()["switch.washer"]["protected"])
        self.assertFalse(self.items()["light.lr"]["protected"])
        with self.assertRaises(ValueError):
            x4_house.validate_config(config(rooms=[{"name": "R", "entities": [
                {"entity": "switch.x", "label": "X", "protected": "yes"}]}]))

    def test_protected_unreadable_entity_stays_protected(self):
        house = x4_house.collect(self.cfg, lambda e: state(e, "on") if e != "switch.washer" else 1 / 0, now=NOW)
        washer = [i for r in house["rooms"] for i in r["items"] if i["entity"] == "switch.washer"][0]
        self.assertTrue(washer["protected"])

    def test_controllable_excludes_protected_unavailable_and_non_lights(self):
        items = self.items()
        self.assertTrue(x4_house.controllable(items["light.lr"]))
        self.assertFalse(x4_house.controllable(items["switch.washer"]))           # protected
        self.assertFalse(x4_house.controllable(dict(items["switch.washer"], protected=False)))  # switches: no
        self.assertFalse(x4_house.controllable(items["climate.t"]))
        self.assertFalse(x4_house.controllable(dict(items["light.lr"], available=False)))
        self.assertFalse(x4_house.controllable(dict(items["light.lr"], protected=True)))
        self.assertFalse(x4_house.controllable({"domain": "light", "available": True}))  # no flag = not proven safe
