"""Persistent destinations for the X4: the interaction grammar in docs/interaction-model.md.

The glance dashboard (Home/Weather/Agenda) stays the ambient default and keeps its own
paging. Back on Home opens the destinations menu; everything else is entered
deliberately. Agent records never displace a view; they surface as counts.

Views: glance -> menu -> {agents -> agent -> record -> choice, reports -> record,
status, soon.<name>}. All state lives here on the host, per device, in SQLite.
"""
import json
import re
import time

from PIL import Image, ImageDraw

from x4_dashboard import _age, _font, _state, _text

DESTINATIONS = ("home", "work", "agents", "house", "life", "reports", "inbox", "status")
TITLES = {"home": "Home", "work": "Work", "agents": "Agents", "house": "House", "life": "Life",
          "reports": "Reports", "inbox": "Inbox", "status": "Status"}
BLURBS = {"home": "Clock, weather, agenda", "work": "PRs, issues, CI",
          "agents": "Decisions and agent reports", "house": "Home Assistant",
          "life": "Routines and reminders", "reports": "Everything to read",
          "inbox": "Approved message threads", "status": "Device and sources"}
PLANNED = {"work": "Read-only pull requests, review requests, issues and CI state.",
           "life": "Consented routines, reminders and transitions. Rest stays a valid plan.",
           "inbox": "Approved Telegram threads only, read-only. Replies need a stronger confirmation."}
ROWS = 6          # list rows per screen
BODY = (24, 128, 776, 404)
LINE = 26         # body line pitch at 19 px
SCHEMA = ("CREATE TABLE IF NOT EXISTS menu_state (device TEXT PRIMARY KEY, body TEXT NOT NULL)")


def _plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def attention(summary):
    """Short counts, e.g. '2 decisions · 1 unread', or '' when nothing needs Cassie."""
    decisions = sum(row["decisions"] for row in summary)
    unread = sum(row["reports"] for row in summary)
    parts = ([_plural(decisions, "decision")] if decisions else []) + ([f"{unread} unread"] if unread else [])
    return " · ".join(parts)


def _minutes(age):
    age = max(0, age)
    return f"{round(age / 60)} min" if age < 7200 else f"{round(age / 3600)} h"


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "room"


def house_freshness(house, now):
    """One plain line: fresh, stale, or failed-and-showing-old. Never silently old."""
    age = now - house.get("observed_at", now)
    stale = age > house.get("stale_after_s", 1800)
    if house.get("refresh_failed"):
        return f"refresh failed · showing {_minutes(age)} old" + (" · STALE" if stale else "")
    if stale:
        return f"STALE · observed {_minutes(age)} ago"
    return "observed just now" if age < 60 else f"observed {_minutes(age)} ago"


def room_summary(room):
    items = room["items"]
    parts = [f"{len(items)} device{'' if len(items) == 1 else 's'}"]
    on = sum(1 for i in items if i.get("on") is True)
    gone = sum(1 for i in items if not i.get("available"))
    if on:
        parts.append(f"{on} on")
    if gone:
        parts.append(f"{gone} unavailable")
    return " · ".join(parts)


def _wrap(text, width, size=19, bold=False):
    """Measured greedy wrap; paragraphs are kept, overlong words are hard-broken."""
    font = _font(size, bold)
    out = []
    for paragraph in str(text).split("\n"):
        words, line = paragraph.split(), ""
        if not words:
            out.append("")
            continue
        for word in words:
            trial = f"{line} {word}" if line else word
            if font.getlength(trial) <= width:
                line = trial
                continue
            if line:
                out.append(line)
            line = ""
            for char in word:
                if line and font.getlength(line + char) > width:
                    out.append(line)
                    line = ""
                line += char
        out.append(line)
    return out


def paginate(record, width=BODY[2] - BODY[0], height=BODY[3] - BODY[1]):
    """Turn a record into screens: summary, then each section, wrapped to fit."""
    per_page = height // LINE
    blocks = [("Summary", record["summary"])] + [(s["heading"], s["body"]) for s in record.get("sections", [])]
    pages = []
    for heading, body in blocks:
        lines = _wrap(body, width)
        chunk = per_page - 1  # a heading row on every page of the block
        for start in range(0, max(1, len(lines)), chunk):
            label = heading if start == 0 else f"{heading} (cont.)"
            pages.append({"heading": label, "lines": lines[start:start + chunk]})
    return pages


class Menu:
    def __init__(self, glance, records):
        self.glance, self.records, self.store = glance, records, glance.store
        with self.store.lock, self.store._db() as db:
            db.execute(SCHEMA)

    # ---- state ------------------------------------------------------------------------
    def state(self, device):
        with self.store._db() as db:
            row = db.execute("SELECT body FROM menu_state WHERE device=?", (device,)).fetchone()
        base = {"view": "glance", "selected": "home", "agent": None, "record": None, "revision": None,
                "page": 0, "pages": 1, "cursor": 0, "choice": None, "notice": None}
        return dict(base, **json.loads(row["body"])) if row else base

    def _save(self, device, state):
        with self.store.lock, self.store._db() as db:
            db.execute("INSERT INTO menu_state VALUES (?,?) ON CONFLICT(device) DO UPDATE SET body=excluded.body",
                       (device, json.dumps(state)))

    def wake(self, device, wake):
        """An unattended timer/boot wake puts the ambient dashboard back; the glance page is kept."""
        if wake in ("timer", "boot") and self.state(device)["view"] != "glance":
            self._save(device, dict(self.state(device), view="glance", notice=None))

    def go_home(self, device):
        self._save(device, dict(self.state(device), view="glance", notice=None))
        self.glance.select(device, "home")

    # ---- frames -------------------------------------------------------------------------
    def frame(self, device, now, tz, session_s):
        state = self._settle(device, self.state(device), now)
        if state["view"] == "glance":
            return self.glance.frame(device, now, tz, session_s, badge=attention(self.records.agents(now)))
        from x4_render import etag, to_pbm
        image, card, extra = self._render(device, state, now, tz)
        pbm = to_pbm(image)
        tag = '"' + etag(pbm) + '"'
        context = {"view": state["view"], "record": state["record"], "revision": state["revision"],
                   "choice": state["choice"], "card": card, **extra}
        with self.store.lock, self.store._db() as db:
            db.execute("INSERT OR IGNORE INTO glance_frames VALUES (?,?,?,?,?)",
                       (device, tag, card, json.dumps(context, ensure_ascii=False), now))
        return {"pbm": pbm, "etag": tag, "card": card, "session": session_s, "image": image,
                "actions": ("confirm", "confirm_long", "back", "up", "down")}

    def _house(self):
        """The house snapshot section, or None when House is not configured."""
        house = self.glance.snapshot().get("house")
        return house if isinstance(house, dict) else None

    def _rooms(self):
        house = self._house()
        return house.get("rooms", []) if house and house.get("available") else []

    def _settle(self, device, state, now):
        """A record or room that vanished drops Cassie back to its list, not an error."""
        if state["view"] == "room" and state.get("room") not in [r["name"] for r in self._rooms()]:
            state = dict(state, view="house", room=None, cursor=0)
            self._save(device, state)
        if state["view"] in ("record", "choice") and state["record"]:
            live = {r["id"] for r in self.records.list(now)}
            if state["record"] not in live:
                state = dict(state, view="agent" if state["agent"] else "reports", record=None, cursor=0,
                             notice="That item is gone.")
                self._save(device, state)
        return state

    def _shown(self, device, ev):
        if not ev.get("etag") or not ev.get("card"):
            return None
        with self.store._db() as db:
            row = db.execute("SELECT body FROM glance_frames WHERE device=? AND etag=? AND card=?",
                             (device, ev["etag"], ev["card"])).fetchone()
        return json.loads(row["body"]) if row else None

    # ---- buttons ------------------------------------------------------------------------
    def handle(self, device, ev, now, tz):
        """Apply one new (deduplicated) event. Returns what x4d must do next."""
        state = self.state(device)
        button, press = ev["button"], ev["press"]
        result = {"moved": False, "label": None, "refresh": False}
        if state["view"] == "glance":
            if button == "back" and press == "short" and self.glance.state(device)["page"] == "home":
                self._save(device, dict(state, view="menu", selected="home", notice=None))
                return dict(result, moved=True)
            if button == "confirm":
                if press == "long":
                    return dict(result, refresh=True)
                return dict(result, label="brief")
            return dict(result, moved=self.glance.navigate(device, button, press, now=now, tz=tz))
        if button in ("left", "right"):
            return result  # left/right page the glance dashboard only, for now
        shown = self._shown(device, ev)
        handler = getattr(self, "_on_" + state["view"])
        state = dict(state, notice=None)
        return handler(device, state, button, press, shown, now, result)

    def _move(self, state, button, length):
        step = 1 if button == "down" else -1
        return (state["cursor"] + step) % max(1, length)

    def _on_menu(self, device, state, button, press, shown, now, result):
        index = DESTINATIONS.index(state["selected"])
        if button in ("up", "down"):
            state["selected"] = DESTINATIONS[(index + (1 if button == "down" else -1)) % len(DESTINATIONS)]
        elif button == "back":
            self.go_home(device)
            return dict(result, moved=True)
        elif button == "confirm" and press == "short":
            target = state["selected"]
            if target == "home":
                self.go_home(device)
                return dict(result, moved=True)
            state.update(view=target if target in ("agents", "reports", "status", "house") else "soon." + target,
                         cursor=0, agent=None, record=None)
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _list_back(self, device, state):
        state.update(view="menu", cursor=0)
        self._save(device, state)

    def _on_agents(self, device, state, button, press, shown, now, result):
        agents = self.records.agents(now)
        if button in ("up", "down"):
            state["cursor"] = self._move(state, button, len(agents))
        elif button == "back":
            self._list_back(device, state)
            return dict(result, moved=True)
        elif button == "confirm" and press == "short" and agents:
            state.update(view="agent", agent=agents[min(state["cursor"], len(agents) - 1)]["agent"], cursor=0)
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _items(self, state, now):
        """The one list both rendering and Confirm use. Reports holds reports; decisions live under Agents."""
        if state["view"] == "agent":
            return self.records.list(now, agent=state["agent"])
        return self.records.list(now, kind="report")

    def _open_list(self, device, state, button, press, shown, now, result, parent):
        items = self._items(state, now)
        if button in ("up", "down"):
            state["cursor"] = self._move(state, button, len(items))
        elif button == "back":
            if parent == "agents":
                state.update(view="agents", agent=None, cursor=0)
            else:
                state.update(view="menu", cursor=0)
        elif button == "confirm" and press == "short" and items:
            # Open what was highlighted on the frame Cassie saw, even if the list has since reordered.
            wanted = (shown or {}).get("highlighted")
            item = next((i for i in items if i["id"] == wanted), None)
            if item is None:
                if shown is None:
                    return result  # never act on a frame we did not render
                item = items[min(state["cursor"], len(items) - 1)]
            state.update(view="record", record=item["id"], revision=item["revision"], page=0,
                         pages=len(paginate(item["record"])), return_view=state["view"])
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _on_agent(self, device, state, button, press, shown, now, result):
        return self._open_list(device, state, button, press, shown, now, result, "agents")

    def _on_reports(self, device, state, button, press, shown, now, result):
        return self._open_list(device, state, button, press, shown, now, result, "menu")

    def _on_record(self, device, state, button, press, shown, now, result):
        item = self.records.get(state["record"])
        if item is None:
            return result
        if button in ("up", "down"):
            step = 1 if button == "down" else -1
            state["page"] = max(0, min(state["pages"] - 1, state["page"] + step))
        elif button == "back":
            state.update(view=state.get("return_view") or "reports", record=None, page=0)
        elif button == "confirm" and press == "long":
            # Fuller context comes from Muse; holding Confirm never approves anything.
            return dict(result, label="context", context=json.dumps(
                {"record": item["id"], "revision": state["revision"], "agent": item["agent"],
                 "title": item["record"]["title"]}, ensure_ascii=False))
        elif button == "confirm" and shown is not None:
            if item["kind"] == "report":
                outcome = self.records.ack(item["id"], shown.get("revision"), now)
                state["notice"] = "Marked read." if outcome["outcome"] == "read" else "This report changed."
                self._save(device, state)
                return dict(result, moved=True, report=outcome)
            if item["status"] != "open":
                state["notice"] = f"Already answered: {item['answer']}"
            else:
                options = item["record"]["options"]
                pick = item["record"].get("recommendation") or options[0]
                state.update(view="choice", choice=pick)
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _on_choice(self, device, state, button, press, shown, now, result):
        item = self.records.get(state["record"])
        if item is None:
            return result
        options = item["record"]["options"]
        if button in ("up", "down"):
            index = options.index(state["choice"]) if state["choice"] in options else 0
            state["choice"] = options[(index + (1 if button == "down" else -1)) % len(options)]
        elif button == "back":
            state.update(view="record", choice=None)
        elif button == "confirm" and press == "short":
            if shown is None or shown.get("view") != "choice":
                return result  # never answer from a frame we did not render
            outcome = self.records.answer(item["id"], shown["revision"], shown["choice"], now=now, source=device)
            if outcome["outcome"] == "stale":
                state.update(view="record", choice=None, revision=outcome["current_revision"], page=0,
                             pages=len(paginate(self.records.get(item["id"])["record"])),
                             notice="This decision changed while it was on screen. Review it again.")
            else:
                state.update(view="record", choice=None, notice=None)
            self._save(device, state)
            if outcome["outcome"] != "answered":
                return dict(result, moved=True, decision=outcome)
            context = json.dumps({"record": item["id"], "agent": item["agent"], "title": item["record"]["title"],
                                  "revision": shown["revision"], "answer": shown["choice"]}, ensure_ascii=False)
            return dict(result, moved=True, decision=outcome, label="answer", context=context)
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _on_house(self, device, state, button, press, shown, now, result):
        rooms = self._rooms()
        if button == "confirm" and press == "long":
            return dict(result, refresh=True)  # same as the dashboard: hold refreshes sources
        if button in ("up", "down") and rooms:
            state["cursor"] = self._move(state, button, len(rooms))
        elif button == "back":
            self._list_back(device, state)
            return dict(result, moved=True)
        elif button == "confirm" and rooms and shown is not None:
            names = [r["name"] for r in rooms]
            wanted = shown.get("highlighted")
            state.update(view="room", room=wanted if wanted in names else names[min(state["cursor"], len(names) - 1)])
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _on_room(self, device, state, button, press, shown, now, result):
        if button == "confirm" and press == "long":
            return dict(result, refresh=True)
        if button == "back":
            state.update(view="house", room=None)
            self._save(device, state)
            return dict(result, moved=True)
        return result  # read-only: no control exists on this page yet

    def _on_status(self, device, state, button, press, shown, now, result):
        if button == "back":
            self._list_back(device, state)
            return dict(result, moved=True)
        return result

    def __getattr__(self, name):
        if name.startswith("_on_soon."):
            return self._on_status
        raise AttributeError(name)

    # ---- rendering ----------------------------------------------------------------------
    def _canvas(self, title, crumb, tz, now):
        image = Image.new("1", (800, 480), 1)
        image.info["layout"] = []
        _text(image, crumb.upper(), (24, 17, 620, 39), 14, True)
        _text(image, title, (24, 52, 776, 100), 30, True)
        ImageDraw.Draw(image).line((24, 112, 776, 112), fill=0)
        return image

    def _footer(self, image, hint, notice=None):
        draw = ImageDraw.Draw(image)
        draw.line((24, 418, 776, 418), fill=0)
        if notice:
            _text(image, notice, (24, 424, 776, 444), 14, True)
        _text(image, hint, (24, 456, 776, 476), 13, True)

    def _rows(self, image, rows, cursor, split=460):
        top = 124
        start = (cursor // ROWS) * ROWS
        for index, (left, right) in enumerate(rows[start:start + ROWS]):
            y = top + index * 47
            mark = start + index == cursor
            if mark:
                ImageDraw.Draw(image).rectangle((24, y, 776, y + 42), outline=0, width=2)
            _text(image, ("▶ " if mark else "   ") + left, (34, y + 9, split, y + 35), 20, mark)
            if right:
                _text(image, right, (split + 10, y + 11, 766, y + 33), 17, mark)
        if len(rows) > ROWS:
            _text(image, f"{start + 1}–{min(start + ROWS, len(rows))} of {len(rows)}", (600, 396, 776, 414), 13)

    def _render(self, device, state, now, tz):
        view = state["view"]
        if view == "menu":
            image = self._canvas("Destinations", "Alicenet · X4", tz, now)
            agents = self.records.agents(now)
            house = self._house()
            counts = {"agents": attention(agents),
                      "reports": f"{sum(r['reports'] for r in agents)} unread" if any(r["reports"] for r in agents) else "",
                      "house": "not set up" if house is None else
                               "unreachable" if not house.get("available") else
                               f"{len(house['rooms'])} room{'' if len(house['rooms']) == 1 else 's'}"}
            rows = [(TITLES[d], counts.get(d) or ("soon" if d in PLANNED else BLURBS[d])) for d in DESTINATIONS]
            # All eight destinations fit when rows are a little tighter than lists.
            for index, (left, right) in enumerate(rows):
                y = 120 + index * 36
                mark = DESTINATIONS[index] == state["selected"]
                if mark:
                    ImageDraw.Draw(image).rectangle((24, y, 776, y + 33), outline=0, width=2)
                _text(image, ("▶ " if mark else "   ") + left, (34, y + 6, 330, y + 29), 19, mark)
                _text(image, right, (340, y + 8, 766, y + 28), 16, mark)
            self._footer(image, "▲ ▼ choose · Confirm: open · Back: home")
            return image, "menu", {}
        if view == "agents":
            image = self._canvas("Agents", "Destinations › Agents", tz, now)
            agents = self.records.agents(now)
            rows = []
            for row in agents:
                parts = ([_plural(row["decisions"], "decision")] if row["decisions"] else []) + \
                        (["report ready" if row["reports"] == 1 else f"{row['reports']} reports ready"]
                         if row["reports"] else [])
                rows.append((row["agent"].capitalize(), " · ".join(parts) or "nothing waiting"))
            if rows:
                self._rows(image, rows, min(state["cursor"], len(rows) - 1))
            else:
                _text(image, "No agent is waiting on you.", (24, 150, 776, 190), 24, True)
                _text(image, "Decisions and reports agents publish will appear here.", (24, 200, 776, 226), 17)
            self._footer(image, "▲ ▼ choose · Confirm: open · Back: destinations", state["notice"])
            return image, "agents", {}
        if view in ("agent", "reports"):
            title = state["agent"].capitalize() if view == "agent" else "Reports"
            crumb = f"Agents › {title}" if view == "agent" else "Destinations › Reports"
            image = self._canvas(title, crumb, tz, now)
            items = self._items(state, now)
            rows = [(i["record"]["title"], self._badge(i, now, view == "reports")) for i in items]
            extra = {}
            if rows:
                cursor = min(state["cursor"], len(rows) - 1)
                self._rows(image, rows, cursor)
                extra["highlighted"] = items[cursor]["id"]
            else:
                _text(image, "Nothing here right now.", (24, 150, 776, 190), 24, True)
            self._footer(image, "▲ ▼ choose · Confirm: open · Back: up a level", state["notice"])
            return image, view if view == "reports" else f"agent.{state['agent']}"[:48], extra
        if view in ("record", "choice"):
            return self._render_record(state, now, tz)
        if view == "status":
            return self._render_status(device, state, now, tz), "status", {}
        if view in ("house", "room"):
            return self._render_house(state, now, tz)
        name = view.split(".", 1)[1]
        image = self._canvas(TITLES[name], f"Destinations › {TITLES[name]}", tz, now)
        _text(image, "Not connected yet", (24, 140, 776, 180), 26, True)
        _text(image, PLANNED[name], (24, 196, 776, 300), 19, lines=4)
        _text(image, "Planned in docs/future-surface.md. Nothing here is live data.", (24, 330, 776, 352), 15)
        self._footer(image, "Back: destinations")
        return image, view, {}

    def _render_house(self, state, now, tz):
        house = self._house()
        if state["view"] == "room":
            room = next(r for r in self._rooms() if r["name"] == state["room"])
            image = self._canvas(room["name"], "House › " + room["name"], tz, now)
            pitch = min(50, 286 // max(1, len(room["items"])))  # 8 rows fit; fewer rows breathe
            for index, item in enumerate(room["items"]):
                y = 124 + index * pitch
                _text(image, item["label"], (24, y + 4, 300, y + 30), 19, True)
                if item.get("available"):
                    value = " · ".join(p for p in (item["state"], item.get("detail")) if p)
                    _text(image, value, (310, y + 4, 776, y + 30), 19)
                else:
                    _text(image, "— " + item["state"].upper(), (310, y + 6, 776, y + 30), 17, True)
            self._footer(image, "read-only for now · hold: refresh · Back: rooms", house_freshness(house, now))
            return image, ("house." + _slug(room["name"]))[:48], {}
        image = self._canvas("House", "Destinations › House", tz, now)
        if house is None:
            _text(image, "House isn't set up yet", (24, 140, 776, 180), 26, True)
            _text(image, "List rooms and entities in ~/.config/x4d/house.json; the ten-minute collector "
                  "reads them from Home Assistant.", (24, 196, 776, 300), 19, lines=4)
            self._footer(image, "Back: destinations")
            return image, "house", {}
        if not house.get("available"):
            _text(image, "Home Assistant unreachable", (24, 140, 776, 180), 26, True)
            _text(image, "No earlier readings to show. Hold Confirm to try again.", (24, 196, 776, 230), 19)
            self._footer(image, "hold: refresh · Back: destinations")
            return image, "house", {}
        rooms = house["rooms"]
        cursor = min(state["cursor"], len(rooms) - 1)
        self._rows(image, [(r["name"], room_summary(r)) for r in rooms], cursor, split=330)
        self._footer(image, "▲ ▼ choose · Confirm: open · hold: refresh · Back", house_freshness(house, now))
        return image, "house", {"highlighted": rooms[cursor]["name"]}

    def _badge(self, item, now, with_agent):
        status = {"open": "needs you", "unread": "unread", "answered": f"answered: {item['answer']}",
                  "read": "read"}[item["status"]]
        age = _age(item["updated_at"], now)
        return " · ".join(([item["agent"].capitalize()] if with_agent else []) + [status, age])

    def _render_record(self, state, now, tz):
        item = self.records.get(state["record"])
        record = item["record"]
        kind = "Decision" if item["kind"] == "decision" else "Report"
        image = self._canvas(record["title"], f"{item['agent'].capitalize()} › {kind} · rev {item['revision']} · "
                             f"{_age(item['updated_at'], now)} old", tz, now)
        # Device event cards are capped at 48 characters; the ETag disambiguates truncation.
        card = ("record." + item["id"])[:48]
        if state["view"] == "choice":
            options = record["options"]
            if record.get("recommendation"):
                _text(image, f"Recommended: {record['recommendation']}", (24, 124, 776, 148), 17, True)
            for index, option in enumerate(options):
                y = 160 + index * 52
                mark = option == state["choice"]
                if mark:
                    ImageDraw.Draw(image).rectangle((24, y, 420, y + 44), outline=0, width=3)
                _text(image, ("▶ " if mark else "   ") + option.upper(), (40, y + 10, 410, y + 38), 22, mark)
            _text(image, "Confirm records this choice for revision "
                  f"{item['revision']} only. Back cancels.", (440, 170, 776, 260), 16, lines=4)
            self._footer(image, "▲ ▼ choose · Confirm: record answer · Back: cancel", state["notice"])
            return image, ("choice." + item["id"])[:48], {}
        pages = paginate(record)
        page = pages[min(state["page"], len(pages) - 1)]
        _text(image, page["heading"], (24, 124, 600, 148), 18, True)
        _text(image, f"{min(state['page'], len(pages) - 1) + 1}/{len(pages)}", (640, 124, 776, 146), 15, True)
        for index, line in enumerate(page["lines"]):
            if line:
                y = BODY[1] + LINE * (index + 1)
                _text(image, line, (24, y, 776, y + LINE - 2), 19)
        if item["kind"] == "decision":
            answered = item["status"] == "answered"
            hint = ("▲ ▼ page · Confirm: choose · hold: ask for context · Back" if not answered
                    else "▲ ▼ page · hold: ask for context · Back")
            notice = state["notice"] or (f"Answered: {item['answer']} (rev {item['answered_revision']})"
                                         if answered else None)
        else:
            hint = "▲ ▼ page · Confirm: mark read · hold: ask for context · Back"
            notice = state["notice"] or ("Read." if item["status"] == "read" else None)
        self._footer(image, hint, notice)
        return image, card, {}

    def _render_status(self, device, state, now, tz):
        image = self._canvas("Status", "Destinations › Status", tz, now)
        status = self.store.status(now=now)
        dev = status["devices"].get(device, {})
        snapshot = self.glance.snapshot()
        lines = [
            ("Device", device),
            ("Firmware", dev.get("fw") or "unknown"),
            ("Battery", f"{dev['battery']}%" if dev.get("battery") else "not reported"),
            ("Wi-Fi", f"{dev['rssi']} dBm" if dev.get("rssi") is not None else "not reported"),
            ("Last contact", f"{_age(dev['last_seen'], now)} ago ({dev.get('last_wake') or '?'} wake)"
                             if dev.get("last_seen") else "never"),
            ("Weather", _state(snapshot.get("weather", {}), now)
                        + (" · refresh failed" if snapshot.get("weather", {}).get("refresh_failed") else "")),
            ("Calendar", _state(snapshot.get("calendar", {}), now)
                         + (" · refresh failed" if snapshot.get("calendar", {}).get("refresh_failed") else "")),
            ("Muse queue", f"{status['pending_forwards']} waiting to forward"),
        ]
        for index, (label, value) in enumerate(lines):
            y = 126 + index * 34
            _text(image, label.upper(), (24, y + 4, 220, y + 24), 14, True)
            _text(image, str(value), (230, y, 776, y + 28), 20)
        self._footer(image, "Back: destinations · this is the gateway's view, sampled when drawn")
        return image
