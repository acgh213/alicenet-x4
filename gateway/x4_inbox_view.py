"""Read-only Inbox UI mixin, kept separate from parallel destination lanes."""
import unicodedata
from functools import lru_cache

from x4_dashboard import _age, _font, _text
from x4_inbox import fingerprint


@lru_cache(maxsize=4096)
def _supported(char):
    # Pillow exposes the actual selected font's raster, not a Unicode coverage
    # claim. Unsupported glyphs use the same .notdef mask as U+10FFFF.
    for bold in (False, True):
        font = _font(19, bold)
        mask, missing = font.getmask(char), font.getmask('\U0010ffff')
        if mask.size == missing.size and bytes(mask) == bytes(missing):
            return False
    return True


def display_text(value):
    """NFC; escape combining stacks and missing glyphs as ASCII code points.

    Supported accents remain native. BMP escapes use \\uXXXX; non-BMP escapes
    use \\UXXXXXXXX. Apply before wrapping to body, titles and source labels.
    """
    def visible(char):
        if char.isspace() or (not unicodedata.category(char).startswith('M') and _supported(char)):
            return char
        return f'\\u{ord(char):04x}' if ord(char) <= 0xffff else f'\\U{ord(char):08x}'
    return ''.join(visible(c) for c in unicodedata.normalize('NFC', value))


def display_record(record):
    return {"summary": display_text(record["summary"]),
            "sections": [{"heading": s["heading"], "body": display_text(s["body"])}
                         for s in record["sections"]]}


def empty_snapshot():
    return {"state": "not_configured", "items": [], "sources": []}


class InboxViews:
    def _inbox(self, now):
        return self.inbox.snapshot(now) if self.inbox is not None else empty_snapshot()

    def _settle_inbox(self, device, state, snapshot):
        if state["view"] == "inbox_detail":
            item = next((i for i in snapshot["items"] if i["key"] == state.get("inbox_key")), None)
            if item is None or item["revision"] != state.get("inbox_revision"):
                state = dict(state, view="inbox", inbox_key=None, inbox_revision=None, page=0, cursor=0,
                             notice="That report changed or its scope was revoked.")
                self._save(device, state)
        return state

    def _on_inbox(self, device, state, button, press, shown, now, result):
        snapshot = self._inbox(now)
        items = snapshot["items"]
        if button == "back":
            self._list_back(device, state)
            return dict(result, moved=True)
        if button in ("up", "down") and items:
            state["cursor"] = self._move(state, button, len(items))
        elif button == "confirm" and press == "short" and shown and shown.get("view") == "inbox":
            item = next((i for i in items if i["key"] == shown.get("inbox_key")
                         and i["revision"] == shown.get("inbox_revision")), None)
            if item is None:
                state["notice"] = "That report changed or its scope was revoked."
            else:
                state.update(view="inbox_detail", inbox_key=item["key"], inbox_revision=item["revision"], page=0)
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _on_inbox_detail(self, device, state, button, press, shown, now, result):
        from x4_menu import paginate
        snapshot = self._inbox(now)
        item = next((i for i in snapshot["items"] if i["key"] == state.get("inbox_key")), None)
        if button == "back" or item is None:
            state.update(view="inbox", inbox_key=None, page=0)
        elif button in ("up", "down"):
            if (not shown or shown.get("inbox_key") != item["key"]
                    or shown.get("inbox_revision") != item["revision"]
                    or shown.get("inbox_page") != state["page"]):
                return result
            pages = paginate(display_record(item["record"]))
            state["page"] = max(0, min(len(pages) - 1, state["page"] + (1 if button == "down" else -1)))
        else:
            return result  # No reply, mark-read, context request, refresh, or side effects.
        self._save(device, state)
        return dict(result, moved=True)

    def _render_inbox(self, state, snapshot, now, tz):
        from x4_menu import paginate, BODY, LINE
        items = snapshot["items"]
        status = snapshot["state"]
        freshness = {"ok": "Source available · sampled reports", "stale": "STALE · showing cached reports",
                     "error": "Source error · cached reports may be old",
                     "unavailable": "Unavailable · no successful source observation",
                     "not_configured": "Inbox not configured · explicit consent required"}[status]
        if state["view"] == "inbox_detail":
            item = next(i for i in items if i["key"] == state["inbox_key"])
            image = self._canvas(display_text(item["record"]["title"]), '', tz, now)
            # _canvas uppercases breadcrumbs, which would turn BMP \\u escapes
            # into invalid \\U forms. Draw the literal source fallback unchanged.
            _text(image, 'INBOX › ' + display_text(item['source']), (24, 17, 620, 39), 14, True)
            pages = paginate(display_record(item["record"]))
            index = min(state["page"], len(pages) - 1)
            page = pages[index]
            # Timestamp is provenance, not wall-clock freshness; both remain visible.
            _text(image, item["time"] + f" · received {_age(item['received_at'], now)} ago"
                  + f" · page {index + 1}/{len(pages)}", (24, 120, 776, 143), 15, True)
            for n, line in enumerate(page["lines"]):
                y = BODY[1] + LINE * (n + 1)
                if line:
                    _text(image, line, (24, y, 776, y + LINE - 2), 19)
            notice = freshness + (" · STALE report" if item["stale"] and status != "stale" else "")
            self._footer(image, "▲ ▼ page · read-only · Back: reports", notice)
            return image, "inbox." + item["key"][:32], {"inbox_key": item["key"],
                    "inbox_revision": item["revision"], "inbox_page": index}
        image = self._canvas("Inbox", "Destinations › Inbox · read-only", tz, now)
        extra = {}
        if items:
            cursor = min(state["cursor"], len(items) - 1)
            self._rows(image, [(display_text(i["record"]["title"]), display_text(i["source"])) for i in items], cursor)
            extra = {"inbox_key": items[cursor]["key"], "inbox_revision": items[cursor]["revision"]}
        else:
            _text(image, {"not_configured": "Inbox not configured", "error": "Inbox source error",
                          "unavailable": "Inbox Unavailable", "stale": "STALE source · no reports",
                          "ok": "No reports in approved threads"}[status], (24, 145, 776, 185), 26, True)
            _text(image, "Only explicitly scoped, agent-published reports appear here.\n"
                  "No Telegram polling, replies, or history scanning.", (24, 200, 776, 290), 19, lines=3)
        self._footer(image, "▲ ▼ choose · Confirm: read · Back: destinations", state["notice"] or freshness)
        # Context, not just pixels, participates in identity. A truncated title can
        # hide a scope/item change; never INSERT OR IGNORE it under an old ETag.
        return image, "inbox", extra

    @staticmethod
    def _inbox_etag(pbm, context):
        import hashlib
        return '"' + hashlib.sha256(pbm + fingerprint(context).encode()).hexdigest()[:48] + '"'
