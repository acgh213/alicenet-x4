"""Render synthetic previews of every menu view for a human look. Never live data."""
import json
import sys
import tempfile
from pathlib import Path

import x4_glance
import x4_menu
import x4_records
from test_x4_dashboard import NOW, TZ, fixture
from test_x4_records import decision, report
from x4_store import Store

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    snap = Path(tmp) / "s.json"
    snap.write_text(json.dumps(fixture()))
    store = Store(str(Path(tmp) / "db"))
    store.seen("x4-01", now=NOW - 40, wake="button", battery="97", rssi=-39, fw="0.3.1-ambient")
    records = x4_records.Records(store)
    put = lambda b: records.put(x4_records.validate_record(b), now=NOW - 600)
    put(decision(title="FIXTURE · Ship the menu before House?",
                 summary="Agents first keeps one interaction grammar before anything can mutate the house. "
                         "House waits about a day.",
                 sections=[{"heading": "Findings", "body": "The menu, records and answers share one revision rule."},
                           {"heading": "Concerns", "body": "Timer wakes now return the menu to Home."}]))
    put(decision(id="pyrrha.pr-12", title="FIXTURE · Merge the CI fixture PR?", recommendation=None))
    put(report(title="FIXTURE · Weekly lab report",
               summary="Three runs finished and converged.\nOne regression is still open.",
               sections=[{"heading": "Findings", "body": " ".join(["Convergence held across seeds."] * 18)},
                         {"heading": "Recommendation", "body": "Keep the current schedule."}]))
    put(report(id="eido.run-7", agent="eido", title="FIXTURE · Run 7 complete", summary="Done in 24 min."))
    menu = x4_menu.Menu(x4_glance.Glance(store, str(snap)), records)
    seq = [0]

    def shot(name):
        menu.frame("x4-01", NOW, TZ, 120)["image"].save(out / f"{name}.png")

    def press(button, kind="short"):
        frame = menu.frame("x4-01", NOW, TZ, 120)
        seq[0] += 1
        menu.handle("x4-01", {"seq": seq[0], "card": frame["card"], "etag": frame["etag"],
                              "button": button, "press": kind}, now=NOW, tz=TZ)

    shot("1-home-badge")
    press("back"); press("down"); press("down"); shot("2-menu")
    press("confirm"); shot("3-agents")
    press("confirm"); shot("4-agent-pyrrha")
    press("confirm"); shot("5-decision")
    press("confirm"); shot("6-choice")
    press("down"); press("up"); press("confirm"); shot("7-answered")
    menu.go_home("x4-01"); press("back")
    for _ in range(5): press("down")
    press("confirm"); shot("8-reports")
    press("down"); press("confirm"); press("down"); shot("9-report-page2")
    menu.go_home("x4-01"); press("back")
    for _ in range(7): press("down")
    press("confirm"); shot("10-status")
    menu.go_home("x4-01"); press("back"); press("down"); press("confirm"); shot("11-work-soon")
print("ok", sorted(p.name for p in out.iterdir()))
