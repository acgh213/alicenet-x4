"""Render validated X4 slides to 800x480 1-bit images and P4 PBM frames.

Layout (pixels):
  header   y 16..112  avatar 96x96 at x=24 (32x32 scaled 3x), title right of it
  rule     y 124..127
  body     y 140..404 largest font size at which every wrapped line fits
  footer   y 416..440 owner · time [· STALE] — caller footer
  hints    y 448..472 action hints for buttons the slide assigned
  status   x 640..800, y 0..28 is left white; the device draws battery/sync there.
Content that cannot fit raises ValueError; nothing is silently clipped.
"""
import datetime as dt
import hashlib
import os

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 800, 480
MARGIN = 24
STATUS_CORNER = (640, 0, 800, 28)
HEADER = (16, 112)
BODY = (140, 404)
FONT = os.environ.get("X4_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
FONT_BOLD = os.environ.get("X4_FONT_BOLD", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
BODY_SIZES = (40, 36, 32, 30, 28, 26, 24, 22, 20, 18)
HINT_GLYPHS = {"confirm": "OK", "confirm_long": "hold OK", "back": "BACK", "up": "\u25b2", "down": "\u25bc"}
PBM_HEADER = b"P4\n%d %d\n" % (WIDTH, HEIGHT)
_FONTS = {}


def _font(size, bold=False):
    key = (size, bold)
    if key not in _FONTS:
        _FONTS[key] = ImageFont.truetype(FONT_BOLD if bold else FONT, size)
    return _FONTS[key]


def _avatar_rows(kind):
    """Builtin 32x32 glyphs; identical formulas to clock-control so agents look the same on both."""
    rows = []
    for y in range(32):
        row = []
        for x in range(32):
            edge = x in (0, 31) or y in (0, 31)
            if kind == "alice":
                black = edge or (8 <= x <= 10 and 10 <= y <= 13) or (21 <= x <= 23 and 10 <= y <= 13) or (10 <= x <= 21 and y == 23)
            elif kind == "pyrrha":
                black = edge or (x == 15 and 5 <= y <= 26) or (y == 8 and 10 <= x <= 21) or (y == 26 and 8 <= x <= 23)
            else:  # muse
                black = edge or (x in (9, 10) and 9 <= y <= 24) or (x in (21, 22) and 6 <= y <= 21) or (y in (24, 25) and 9 <= x <= 22) or (y == 8 and 20 <= x <= 26)
            row.append("1" if black else "0")
        rows.append("".join(row))
    return rows


def blank():
    return Image.new("1", (WIDTH, HEIGHT), 1)


def wrap(text_lines, font, width):
    """Wrap explicit lines at word boundaries, hard-breaking words wider than the column."""
    out = []
    for paragraph in text_lines:
        if not paragraph.strip():
            out.append("")
            continue
        current = ""
        for word in paragraph.split():
            while font.getlength(word) > width:  # hard-break an overlong word
                cut = len(word)
                while cut > 1 and font.getlength(word[:cut]) > width:
                    cut -= 1
                if current:
                    out.append(current)
                    current = ""
                out.append(word[:cut])
                word = word[cut:]
            candidate = f"{current} {word}" if current else word
            if font.getlength(candidate) <= width:
                current = candidate
            else:
                out.append(current)
                current = word
        out.append(current)
    return out


def _fit_one_line(draw, text, sizes, width, bold=False):
    for size in sizes:
        font = _font(size, bold)
        if font.getlength(text) <= width:
            return font
    raise ValueError(f"'{text[:24]}...' does not fit on one line.")


def _draw_avatar(img, avatar):
    if avatar == "none":
        return False
    rows = avatar["rows"] if isinstance(avatar, dict) else _avatar_rows(avatar)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(rows):
        for x, bit in enumerate(row):
            if bit == "1":
                x0, y0 = MARGIN + 3 * x, HEADER[0] + 3 * y
                draw.rectangle((x0, y0, x0 + 2, y0 + 2), fill=0)
    return True


def _draw_body(draw, lines, top=BODY[0], bottom=BODY[1]):
    width = WIDTH - 2 * MARGIN
    for size in BODY_SIZES:
        font = _font(size)
        wrapped = wrap(lines, font, width)
        step = round(size * 1.2)
        if len(wrapped) * step <= bottom - top:
            for i, line in enumerate(wrapped):
                draw.text((MARGIN, top + i * step), line, font=font, fill=0)
            return
    raise ValueError("Body text does not fit the card even at the smallest size; shorten it.")


def _meta(owner, updated_at, now, stale_after_s, tz):
    stamp = dt.datetime.fromtimestamp(updated_at, tz or dt.datetime.now().astimezone().tzinfo)
    text = f"{owner} \u00b7 {stamp.strftime('%a %H:%M')}"
    return text + (" \u00b7 STALE" if now - updated_at >= stale_after_s else "")


def _hints(actions):
    parts = ["\u25c0 \u25b6 cards"]
    for button in ("confirm", "confirm_long", "up", "down", "back"):
        if button in actions:
            parts.append(f"{HINT_GLYPHS[button]}: {actions[button]}")
    return "    ".join(parts)


def render_slide(slide, *, updated_at, now, tz=None):
    """Render a canonical slide (x4_protocol.validate output) to an 800x480 mode-1 image."""
    frame = slide["frame"]
    img = blank()
    draw = ImageDraw.Draw(img)
    has_avatar = _draw_avatar(img, frame["avatar"])
    title_x = MARGIN + (96 + 24 if has_avatar else 0)
    if frame["title"]:
        font = _fit_one_line(draw, frame["title"], (52, 46, 40, 36, 32, 28, 24), WIDTH - MARGIN - title_x, bold=True)
        # Centre on cap height ("H") so descenders don't drag the title upward.
        _, cap_top, _, cap_bottom = draw.textbbox((0, 0), "H", font=font)
        y = (HEADER[0] + HEADER[1]) // 2 - (cap_top + cap_bottom) // 2
        draw.text((title_x, y), frame["title"], font=font, fill=0)
    draw.rectangle((MARGIN, 124, WIDTH - MARGIN, 127), fill=0)
    lines = frame["lines"] if "lines" in frame else frame["text"].split("\n")
    _draw_body(draw, lines)
    draw.rectangle((MARGIN, 410, WIDTH - MARGIN, 411), fill=0)
    meta = _meta(slide["owner"], updated_at, now, slide["stale_after_s"], tz)
    footer = f"{meta}  \u2014  {frame['footer']}" if frame["footer"] else meta
    draw.text((MARGIN, 416), footer, font=_fit_one_line(draw, footer, (20, 18, 16), WIDTH - 2 * MARGIN), fill=0)
    hints = _hints(slide.get("actions", {}))
    draw.text((MARGIN, 448), hints, font=_fit_one_line(draw, hints, (20, 18, 16), WIDTH - 2 * MARGIN, bold=True), fill=0)
    if any(img.getpixel((x, y)) == 0 for x in range(STATUS_CORNER[0], STATUS_CORNER[2])
           for y in range(STATUS_CORNER[1], STATUS_CORNER[3])):
        raise ValueError("Layout drew into the device status corner.")
    return img


def render_message(title, lines):
    """A plain gateway-side screen (empty deck, errors). Same chrome, no owner/hints."""
    img = blank()
    draw = ImageDraw.Draw(img)
    draw.text((MARGIN, 48), title, font=_fit_one_line(draw, title, (52, 46, 40, 32), WIDTH - 2 * MARGIN, True), fill=0)
    draw.rectangle((MARGIN, 124, WIDTH - MARGIN, 127), fill=0)
    _draw_body(draw, lines)
    return img


def to_pbm(img):
    """P4 PBM: 1 bit per pixel, MSB first, 1 = black. PIL mode '1' stores 1 = white, so invert."""
    if img.size != (WIDTH, HEIGHT) or img.mode != "1":
        raise ValueError("Frame must be an 800x480 mode-1 image.")
    raw = img.tobytes()
    return PBM_HEADER + bytes(b ^ 0xFF for b in raw)


def etag(pbm):
    return hashlib.sha256(pbm).hexdigest()[:16]
