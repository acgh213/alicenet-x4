"""Offline X4 glance pages; only approved weather/calendar fields are rendered.

800x480, one bit. Device owns (640, 0, 800, 40). `image.info['layout']`
contains visible text, its allotted box and actual ink bounds for layout audits.
Snapshot and source observation ages are deliberately separate. No forecasts,
locations, descriptions, external fetches or inferred calendar availability.
"""
import datetime as dt
import math
from functools import lru_cache
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

PAGES = ("home", "weather", "agenda")
STATUS_CORNER = (640, 0, 800, 40)
FONT_DIR = "/usr/share/fonts/truetype/dejavu/"


@lru_cache(maxsize=32)
def _font(size, bold=False):
    return ImageFont.truetype(FONT_DIR + ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"), size)


def _text(image, text, box, size=20, bold=False, lines=1, shrink=False):
    """Word-wrap/hard-break within a measured box, then explicitly ellipsize."""
    draw = ImageDraw.Draw(image)
    text = " ".join(str(text).split())[:4096]
    width, height = box[2] - box[0], box[3] - box[1]
    font = _font(size, bold)
    def fits(value):
        bounds = font.getbbox(value)
        return bounds[2] - bounds[0] <= width and font.getlength(value) <= width
    while shrink and size > 22 and not fits(text):
        size -= 2
        font = _font(size, bold)
    capacity = min(lines, max(1, height // (size + 4)))
    wrapped, current = [], ""
    for word in text.split():
        if current and not fits(current + " " + word):
            wrapped.append(current)
            current = ""
        for char in ((" " if current else "") + word):
            if current and not fits(current + char):
                wrapped.append(current.rstrip())
                current = ""
            current += char
    if current:
        wrapped.append(current)
    if len(wrapped) > capacity or len(str(text)) == 4096:
        wrapped = wrapped[:capacity]
        last = wrapped[-1].rstrip()
        while last and not fits(last + "…"):
            last = last[:-1]
        wrapped[-1] = last + "…"
    for index, line in enumerate(wrapped):
        x, y = box[0], box[1] + index * (size + 4)
        left = draw.textbbox((0, 0), line, font=font, anchor="lt")[0]
        xy = (x - left, y)
        ink = draw.textbbox(xy, line, font=font, anchor="lt")
        if ink[2] > box[2] or ink[3] > box[3]:
            raise ValueError("Text exceeds its layout box")
        draw.text(xy, line, font=font, fill=0, anchor="lt")
        image.info["layout"].append({"text": line, "box": box, "ink": ink})


def _age(stamp, now):
    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
        return "age unknown"
    seconds = now - stamp
    if seconds < 0:
        return "clock skew"
    return "<1m" if seconds < 60 else f"{int(seconds // 60)}m" if seconds < 3600 else f"{int(seconds // 3600)}h" if seconds < 86400 else f"{int(seconds // 86400)}d"


def _state(source, now):
    if not source.get("available", False):
        return "unavailable"
    stamp = source.get("observed_at")
    if not isinstance(stamp, (int, float)) or not math.isfinite(stamp) or stamp > now:
        return "age unknown"
    return "STALE" if now - stamp >= source.get("stale_after_s", 1800) else "fresh"


def _number(source, key, unit):
    value = source.get(key)
    return "Not reported" if value is None else f"{value:g}{unit}" if isinstance(value, (int, float)) and math.isfinite(value) else "Not reported"


def _date(value, tz):
    try:
        if len(value) == 10:
            return dt.datetime.combine(dt.date.fromisoformat(value), dt.time(), tz)
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(tz) if result.tzinfo is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def _events(source, now, tz):
    upcoming = []
    for event in source.get("events", []):
        end = _date(event.get("end"), tz)
        if end is not None and end.timestamp() <= now:
            continue
        start = _date(event.get("start"), tz)
        upcoming.append((start, event))
    return sorted(upcoming, key=lambda pair: pair[0].timestamp() if pair[0] else float("inf"))


def _clock(stamp):
    return stamp.strftime("%I:%M %p").lstrip("0")


def _icon(image, condition, box):
    """Crisp outlined weather glyphs, drawn at native panel resolution."""
    x, y, width = box
    draw = ImageDraw.Draw(image)
    def coord(a, b):
        return (round(x + a * width), round(y + b * width))
    def ellipse(a, b, c, d, fill=1):
        draw.ellipse((*coord(a, b), *coord(c, d)), fill=fill, outline=0, width=2)
    def line(points, weight=2):
        draw.line([coord(*point) for point in points], fill=0, width=weight)
    condition = condition.lower()
    sun = condition in ("sunny", "clear", "partlycloudy", "partly cloudy")
    night = condition == "clear-night"
    if night:
        ellipse(.2, .18, .8, .78, fill=0)
        draw.ellipse((*coord(.4, .08), *coord(.93, .64)), fill=1)
    cloud = condition in ("cloudy", "overcast", "partlycloudy", "partly cloudy", "rainy", "pouring", "snowy", "snowy-rainy", "lightning", "lightning-rainy")
    if sun:
        ellipse(.24, .18, .62, .56)
        for angle in range(0, 360, 45):
            a = math.radians(angle)
            line([(.43 + math.cos(a) * .25, .37 + math.sin(a) * .25), (.43 + math.cos(a) * .34, .37 + math.sin(a) * .34)])
    if cloud:
        ellipse(.15, .43, .52, .76)
        ellipse(.33, .29, .76, .76)
        ellipse(.57, .46, .92, .77)
        draw.rectangle((*coord(.29, .51), *coord(.76, .75)), fill=1)
        line([(.28, .76), (.78, .76)])
    if "rain" in condition or condition == "pouring":
        for a in (.3, .53, .76):
            line([(a, .85), (a - .06, .98)])
    if "snow" in condition:
        for a in (.3, .54, .78):
            line([(a - .04, .9), (a + .04, .98)])
            line([(a + .04, .9), (a - .04, .98)])
    if "lightning" in condition:
        draw.polygon([coord(.52, .76), coord(.39, .91), coord(.52, .91), coord(.46, 1.03), coord(.69, .84), coord(.55, .84)], fill=0)
    if condition in ("fog", "mist", "hazy"):
        for a, b in ((.15, .85), (.25, .75), (.1, .9)):
            line([(a, .35 + a), (b, .35 + a)])
    elif not sun and not cloud and not night:
        ellipse(.16, .16, .84, .84)
        line([(.37, .38), (.43, .31), (.59, .31), (.65, .38), (.65, .47), (.5, .57), (.5, .64)])
        ellipse(.48, .72, .52, .76, fill=0)


def _agenda(image, source, now, tz, box, count, offset=0):
    x0, y0, x1, y1 = box
    state = _state(source, now)
    if state == "unavailable":
        _text(image, "Calendar unavailable", (x0, y0, x1, y0 + 58), 24, True, 2)
        _text(image, "No calendar data to display", (x0, y0 + 70, x1, y0 + 125), 18, lines=2)
        return
    events = _events(source, now, tz)
    if not events:
        _text(image, "No events in saved window" if state != "fresh" else "No upcoming events", (x0, y0, x1, y0 + 60), 24, True, 2)
        _text(image, "In fetched window", (x0, y0 + 75, x1, y0 + 98), 16)
        _text(image, f"{source.get('window_start', 'Unknown')[:10]} → {source.get('window_end', 'Unknown')[:10]}", (x0, y0 + 105, x1, y0 + 130), 16)
        return
    offset = min(max(0, int(offset)), ((len(events) - 1) // count) * count)
    step = (y1 - y0 - 20) // count
    for index, (start, event) in enumerate(events[offset:offset + count]):
        top = y0 + step * index
        when = "Time unavailable" if start is None else ("Today" if start.date() == dt.datetime.fromtimestamp(now, tz).date() else f"{start.strftime('%a')} {start.day}") + " · " + ("All day" if len(event.get("start", "")) == 10 else _clock(start))
        _text(image, when, (x0, top, x1, top + 20), 15)
        _text(image, event.get("summary") or "Untitled event", (x0, top + 22, x1, top + step - 3), 18 if count == 5 else 20, True, 2)
    if len(events) > count:
        _text(image, f"{offset + 1}–{min(offset + count, len(events))} of {len(events)} in fetched window", (x0, y1 - 19, x1, y1), 14)


def render_snapshot(snapshot, page="home", now=None, tz=None, *, offset=0):
    """Render supplied data only. Caller supplies sample epoch; default is system time."""
    if page not in PAGES:
        raise ValueError(f"Unknown dashboard page: {page}")
    tz = tz or ZoneInfo("America/New_York")
    now = dt.datetime.now(tz).timestamp() if now is None else now
    stamp = dt.datetime.fromtimestamp(now, tz)
    weather, calendar = snapshot.get("weather", {}), snapshot.get("calendar", {})
    image = Image.new("1", (800, 480), 1)
    image.info["layout"] = []
    draw = ImageDraw.Draw(image)
    _text(image, ("FIXTURE · " if snapshot.get("fixture") else "") + "AT A GLANCE", (24, 17, 440, 39), 14, True)
    _text(image, page.upper(), (470, 17, 630, 39), 14, True)
    if page == "home":
        _text(image, stamp.strftime("%I:%M").lstrip("0"), (24, 50, 318, 138), 82, True, shrink=True)
        _text(image, stamp.strftime("%p"), (320, 103, 367, 135), 22, True)
        _text(image, f"{stamp.strftime('%A %B')} {stamp.day}", (390, 73, 776, 103), 24, shrink=True)
        _text(image, f"SAMPLED · {stamp.tzname()} · NOT LIVE", (390, 116, 776, 140), 14)
        draw.line((24, 157, 776, 157), fill=0)
        draw.line((345, 178, 345, 393), fill=0)
        _text(image, "WEATHER", (24, 177, 320, 201), 15, True)
        _text(image, "UPCOMING", (368, 177, 776, 201), 15, True)
        _agenda(image, calendar, now, tz, (368, 212, 776, 399), 3)
    else:
        _text(image, "Weather" if page == "weather" else "Agenda", (24, 57, 350, 98), 32, True)
        _text(image, f"{_clock(stamp)} · {stamp.strftime('%a')} {stamp.day}", (450, 57, 776, 89), 24)
        _text(image, f"SAMPLED · {stamp.tzname()} · NOT LIVE", (450, 94, 776, 116), 14)
        draw.line((24, 119, 776, 119), fill=0)
        if page == "agenda":
            _agenda(image, calendar, now, tz, (24, 135, 776, 398), 5, offset)
    if page != "agenda":
        home = page == "home"
        state = _state(weather, now)
        y, right = (211, 320) if home else (140, 776)
        if state == "unavailable":
            _text(image, "Weather unavailable", (24, y, right, y + 68), 24, True, 2)
            _text(image, "No observation to display", (24, y + 80, right, y + 140), 18, lines=2)
        else:
            _icon(image, weather.get("condition", "unknown"), (24, y, 82) if home else (440, 128, 120))
            _text(image, _number(weather, "temperature", weather.get("temperature_unit", "")), (120, y, 320, y + 72) if home else (24, 147, 410, 253), 58 if home else 86, True, shrink=True)
            condition = weather.get("condition") or "Condition not reported"
            condition = "Partly cloudy" if condition == "partlycloudy" else condition.replace("_", " ").capitalize()
            _text(image, condition, (24, 292, right, 347) if home else (24, 259, 760, 291), 22, lines=2 if home else 1)
            metrics = [("humidity", "Humidity", "%"), ("wind_speed", "Wind", " " + weather.get("wind_speed_unit", "")), ("pressure", "Pressure", " " + weather.get("pressure_unit", ""))]
            for index, (key, label, unit) in enumerate(metrics[:2] if home else metrics):
                if home:
                    _text(image, f"{label}  {_number(weather, key, unit)}", (24, 350 + index * 25, 320, 374 + index * 25), 17)
                else:
                    x = 24 + index * 252
                    _text(image, label.upper(), (x, 314, x + 236, 336), 14, True)
                    _text(image, _number(weather, key, unit), (x, 344, x + 236, 382), 26, shrink=True)
            if not home:
                _text(image, weather.get("attribution") or "Attribution not supplied", (24, 386, 776, 406), 14)
    draw.line((24, 410, 776, 410), fill=0)
    for x, name, source in ((24, "Weather", weather), (410, "Calendar", calendar)):
        state = _state(source, now)
        failure = "REFRESH FAILED · " if source.get("refresh_failed") else ""
        label = f"{name} {failure}{state}" + (" · " + _age(source.get("observed_at"), now) if state != "unavailable" else "")
        _text(image, label, (x, 417, x + 366, 438), 14, True)
    checked = _age(snapshot.get("retrieved_at", snapshot.get("observed_at")), now)
    _text(image, f"Checked {checked} ago · snapshot, not live", (24, 440, 490, 458), 13)
    if page == "agenda":
        _text(image, "▲ ▼ scroll 5", (580, 440, 776, 458), 13, True)
    _text(image, "◀ ▶ pages · Back: home · Confirm: brief · hold: refresh", (24, 461, 620, 479), 13, True)
    _text(image, f"{page.capitalize()} {PAGES.index(page) + 1}/3", (625, 461, 776, 479), 13, True)
    return image
