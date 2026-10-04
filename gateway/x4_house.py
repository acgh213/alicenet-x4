"""House: allowlisted Home Assistant entities, read-only, normalized for the X4 panel.

The allowlist lives in ~/.config/x4d/house.json (rooms are Cassie's names, not HA
areas, which are often wrong). Rules the panel depends on:

- unavailable, unknown and unreadable are never shown as off, closed or zero;
- people, trackers and media titles never enter this data path;
- there is no action field yet: the first control will be added deliberately;
- `"protected": true` marks a device that must NEVER be switched from the X4 (Cassie's
  washer smart plug). Any control path must go through controllable(), which refuses it.
"""
import datetime as dt
import math
import re

CONTROLLABLE_DOMAINS = ("light",)  # the first control is a light toggle; widen deliberately
DOMAINS = ("light", "switch", "climate", "sensor", "binary_sensor", "media_player", "fan", "cover", "lock")
LIMITS = {"rooms": 8, "entities": 8, "name": 24, "label": 24}
_ENTITY = re.compile(r"^[a-z_]+\.[a-z0-9_]{1,80}$")
_BINARY = {"door": ("open", "closed"), "window": ("open", "closed"), "opening": ("open", "closed"),
           "garage_door": ("open", "closed"), "motion": ("motion", "clear"), "occupancy": ("occupied", "clear"),
           "presence": ("home", "away"), "moisture": ("wet", "dry"), "lock": ("unlocked", "locked"),
           "plug": ("plugged in", "unplugged"), "power": ("power", "no power")}
_ON = {"on", "open", "opening", "playing", "paused", "unlocked", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"}


class HouseError(Exception):
    pass


def _exact(value, allowed, label):
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object.")
    extra = set(value) - set(allowed)
    if extra:
        raise ValueError(f"{label} has unknown field(s): {', '.join(sorted(extra))}.")


def _name(value, label, limit):
    if type(value) is not str or not value.strip() or len(value) > limit or "\n" in value:
        raise ValueError(f"{label} must be one line of at most {limit} characters.")
    return value.strip()


def validate_config(body):
    _exact(body, {"stale_after_s", "rooms"}, "house config")
    stale = body.get("stale_after_s", 1800)
    if type(stale) is not int or not 60 <= stale <= 86400:
        raise ValueError("stale_after_s must be an integer from 60 to 86400.")
    rooms = body.get("rooms")
    if type(rooms) is not list or not 1 <= len(rooms) <= LIMITS["rooms"]:
        raise ValueError(f"rooms must list 1-{LIMITS['rooms']} rooms.")
    out = []
    for room in rooms:
        _exact(room, {"name", "entities"}, "room")
        entities = room.get("entities")
        if type(entities) is not list or not 1 <= len(entities) <= LIMITS["entities"]:
            raise ValueError(f"each room lists 1-{LIMITS['entities']} entities.")
        items = []
        for entry in entities:
            _exact(entry, {"entity", "label", "protected"}, "entity entry")
            entity = entry.get("entity")
            if type(entity) is not str or not _ENTITY.fullmatch(entity) or entity.split(".")[0] not in DOMAINS:
                raise ValueError(f"entity {entity!r} must be a {', '.join(DOMAINS)} entity id.")
            protected = entry.get("protected", False)
            if type(protected) is not bool:
                raise ValueError("protected must be true or false.")
            items.append({"entity": entity, "label": _name(entry.get("label"), "label", LIMITS["label"]),
                          "protected": protected})
        out.append({"name": _name(room.get("name"), "room name", LIMITS["name"]), "entities": items})
    return {"stale_after_s": stale, "rooms": out}


def _epoch(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp() if parsed.tzinfo else None


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    number = round(number, 1)
    return str(int(number)) if number == int(number) else str(number)


def normalize(raw, label):
    entity = raw["entity_id"]
    domain = entity.split(".")[0]
    value = str(raw.get("state", "unknown"))
    attrs = raw.get("attributes") or {}
    item = {"label": label, "entity": entity, "domain": domain, "available": True, "state": value,
            "detail": "", "on": None, "changed_at": _epoch(raw.get("last_changed"))}
    if value in ("unavailable", "unknown", ""):
        return dict(item, available=False, state=value or "unknown")
    if domain == "light":
        item["on"] = value == "on"
        if value == "on" and isinstance(attrs.get("brightness"), (int, float)):
            item["detail"] = f"{round(attrs['brightness'] / 255 * 100)}%"
    elif domain == "climate":
        item["on"] = value != "off"
        parts = []
        if _num(attrs.get("current_temperature")) is not None:
            parts.append(f"{_num(attrs['current_temperature'])}° now")
        if value != "off" and _num(attrs.get("temperature")) is not None:
            parts.append(f"set {_num(attrs['temperature'])}°")
        if value != "off" and isinstance(attrs.get("hvac_action"), str):
            parts.append(attrs["hvac_action"].replace("_", " "))
        item["detail"] = " · ".join(parts)
    elif domain == "sensor":
        number, unit = _num(value), str(attrs.get("unit_of_measurement") or "")
        shown = number if number is not None else value[:24]
        gap = "" if unit in ("%", "°F", "°C", "°") or not unit else " "
        item["state"] = f"{shown}{gap}{unit}"
    elif domain == "binary_sensor":
        words = _BINARY.get(str(attrs.get("device_class") or ""), ("on", "off"))
        item["on"] = value == "on"
        item["state"] = words[0] if value == "on" else words[1]
    else:  # switch, fan, media_player, cover, lock: the state word only, never titles or apps
        item["on"] = value in _ON
        item["state"] = value.replace("_", " ")[:24]
    return item


def controllable(item):
    """The single gate every X4 control must pass. Protected devices are never switched."""
    return (item.get("protected") is False and item.get("available") is True
            and item.get("domain") in CONTROLLABLE_DOMAINS)


def collect(config, fetch, now):
    """fetch(entity) -> HA state dict. Raises HouseError only when nothing could be read."""
    rooms, read = [], 0
    for room in config["rooms"]:
        items = []
        for entry in room["entities"]:
            try:
                raw = fetch(entry["entity"])
                if not isinstance(raw, dict) or raw.get("entity_id") != entry["entity"]:
                    raise HouseError("unexpected response")
                items.append(dict(normalize(raw, entry["label"]), protected=entry["protected"]))
                read += 1
            except Exception:
                # No error text, URLs or tokens: just an honest marker.
                items.append({"label": entry["label"], "entity": entry["entity"], "domain": entry["entity"].split(".")[0],
                              "available": False, "state": "couldn't read", "detail": "", "on": None,
                              "changed_at": None, "protected": entry["protected"]})
        rooms.append({"name": room["name"], "items": items})
    if not read:
        raise HouseError("Home Assistant unreachable")
    return {"available": True, "observed_at": now, "checked_at": now,
            "stale_after_s": config["stale_after_s"], "rooms": rooms}
