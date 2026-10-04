"""Work-specific model and rendering; no network or remote mutations."""
import hashlib
import json

from PIL import ImageDraw
from x4_dashboard import _age, _text


def revision(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()[:16]


def build_status(section, repo):
    if not section["available"]:
        return "Unavailable"
    run = section["data"]["run"]
    if run is None:
        return "No default-branch builds found"
    status = run["conclusion"] if run["status"] == "completed" else run["status"]
    suffix = (" · head unavailable" if repo["head_sha"] is None else
              " · older head" if run["head_sha"] != repo["head_sha"] else "")
    return status.replace("_", " ") + suffix


def freshness(section, now, stale_after=1800):
    if not section["available"]:
        return "Unavailable" + (" · " + section["error"] if section.get("error") else "")
    stamp = section["collected_at"]
    age = now - stamp
    if age < 0:
        return "Unavailable · clock skew"
    phrase = ("Refresh failed · showing " + _age(stamp, now) + " old"
              if section["refresh_failed"] else "collected " + _age(stamp, now) + " ago")
    return ("STALE · " if age > stale_after else "") + phrase


def work_items(snapshot):
    if snapshot is None:
        return []
    repo = snapshot["repository"]
    section = snapshot["build"]
    run = section["data"]["run"] if section["available"] else None
    build = {"kind": "build", "id": run["id"] if run else f"gh:{repo['full_name']}:build-none",
             "title": "Latest build", "value": build_status(section, repo), "run": run}
    rows = [build]
    if snapshot["prs"]["available"]:
        rows.extend(dict(p, kind="pr", value="draft" if p["draft"] else "ready")
                    for p in snapshot["prs"]["data"]["items"])
    return rows


def detail_pages(item, snapshot):
    from x4_menu import paginate
    repo = snapshot["repository"]
    if item["kind"] == "pr":
        body = (f"{item['title']}\nAuthor: {item['author']}\n"
                f"State: {'draft' if item['draft'] else 'ready'}\nTarget: {item['base']}\n"
                f"Updated: {_utc(item['updated_at'])}\nSource: {item['url']}\n"
                "Read-only: review details on GitHub.")
    else:
        run = item["run"]
        if run is None:
            body = item["value"]
        else:
            observed = repo["head_sha"][:8] if repo["head_sha"] else "unavailable"
            body = (f"Workflow: {run['name']}\nState: {item['value']}\nBranch: {run['branch']}\n"
                    f"Tested: {run['head_sha'][:8]}\nObserved head: {observed}\n"
                    f"Updated: {_utc(run['updated_at'])}\nSource: {run['url']}\n"
                    "Read-only: inspect the build on GitHub.")
    return paginate({"summary": body, "sections": []}, height=234)


def _utc(stamp):
    import datetime as dt
    return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def render_work(state, snapshot, now, tz, chrome):
    """Use the menu's existing header/footer without growing its renderer."""
    image = chrome._canvas("Work", "GitHub · Destinations › Work", tz, now)
    if snapshot is None:
        configured = chrome.work is not None and chrome.work.configured()
        _text(image, "Unavailable" if configured else "Work isn't set up yet",
              (24, 140, 776, 184), 26, True)
        _text(image, "No valid public repository snapshot to show.",
              (24, 204, 776, 250), 19)
        chrome._footer(image, "hold: refresh Work · Back: destinations", state.get("notice"))
        return image, "work", {}
    repo = snapshot["repository"]
    items = work_items(snapshot)
    if state["view"] == "work_detail":
        item = next(row for row in items if row["id"] == state["work_id"])
        title = f"PR #{item['number']}" if item["kind"] == "pr" else "Latest build"
        image = chrome._canvas(title, "GitHub · Work › " + title, tz, now)
        pages = detail_pages(item, snapshot)
        page = min(state["page"], len(pages) - 1)
        _text(image, repo["full_name"], (24, 124, 620, 150), 17, True)
        _text(image, f"{page + 1}/{len(pages)}", (640, 124, 776, 150), 17, True)
        for index, line in enumerate(pages[page]["lines"]):
            if line:
                _text(image, line, (24, 160 + index * 26, 776, 184 + index * 26), 19)
        section = snapshot["prs" if item["kind"] == "pr" else "build"]
        _text(image, freshness(section, now), (24, 392, 776, 414), 15, True)
        chrome._footer(image, "▲ ▼ page · hold: refresh Work · Back: Work", state.get("notice"))
        card = "work." + hashlib.sha256(item["id"].encode()).hexdigest()[:16]
        return image, card, {"work_id": item["id"], "work_revision": revision(item)}
    prs = snapshot["prs"]
    count = len(prs["data"]["items"]) if prs["available"] else None
    label = ("PRs unavailable" if count is None else "Showing first 100 PRs" if prs["data"]["truncated"]
             else f"{count} open PR{'s' if count != 1 else ''}")
    branch = repo["default_branch"] or "branch unavailable"
    sha = (repo["head_sha"] or "")[:8]
    _text(image, f"{repo['full_name']} · {branch} {sha} · {label}",
          (24, 122, 776, 150), 17, True)
    cursor = min(state["cursor"], len(items) - 1)
    start = (cursor // 4) * 4
    for index, item in enumerate(items[start:start + 4]):
        y = 162 + index * 46
        marked = start + index == cursor
        if marked:
            ImageDraw.Draw(image).rectangle((24, y, 776, y + 40), outline=0, width=2)
        label = item["title"] if item["kind"] == "build" else f"#{item['number']} {item['title']}"
        _text(image, ("▶ " if marked else "   ") + label, (34, y + 7, 465, y + 33), 18, marked)
        value_size = 14 if item["value"] == "No default-branch builds found" else 16
        _text(image, item["value"], (475, y + 8, 766, y + 32), value_size, marked)
    if count == 0:
        _text(image, "No open pull requests", (24, 218, 776, 246), 20)
    if len(items) > 4:
        _text(image, f"{start + 1}–{min(start + 4, len(items))} of {len(items)}",
              (590, 350, 776, 370), 13)
    _text(image, "PRs: " + freshness(prs, now), (24, 372, 776, 390), 13, True)
    _text(image, "Build: " + freshness(snapshot["build"], now), (24, 396, 776, 414), 13, True)
    chrome._footer(image, "▲ ▼ choose · Confirm: details · hold: refresh Work · Back", state.get("notice"))
    item = items[cursor]
    return image, "work", {"work_id": item["id"], "work_revision": revision(item)}
