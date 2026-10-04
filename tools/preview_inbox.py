"""Render Inbox synthetic views and exact layout transcriptions. No live sources."""
import json
import sys
import tempfile
from pathlib import Path

import x4_glance
import x4_inbox
import x4_menu
import x4_records
from x4_store import Store
from test_x4_dashboard import NOW, TZ, fixture
from test_x4_inbox import config, message


def main(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    transcript = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        glance, cfg = root / "glance.json", root / "consent.json"
        glance.write_text(json.dumps(fixture()))
        store = Store(str(root / "db"))
        inbox = x4_inbox.Inbox(store, cfg)
        menu = x4_menu.Menu(x4_glance.Glance(store, glance), x4_records.Records(store), inbox=inbox)
        menu._save("x4-01", dict(menu.state("x4-01"), view="inbox"))

        def shot(name, now=NOW):
            frame = menu.frame("x4-01", now, TZ, 120)
            frame["image"].save(out / (name + ".png"))
            (out / (name + ".pbm")).write_bytes(frame["pbm"])
            transcript.append(name + "\n" + "\n".join(r["text"] for r in frame["image"].info["layout"]))
            return frame

        def press(button):
            frame = menu.frame("x4-01", NOW, TZ, 120)
            menu.handle("x4-01", {"card": frame["card"], "etag": frame["etag"],
                        "button": button, "press": "short"}, NOW, TZ)

        shot("01-not-configured")
        cfg.write_text(json.dumps(config()))
        shot("02-unavailable")
        inbox.put(message(title="FIXTURE · Lab report", text="Synthetic page one. " * 60 + "END sentinel"), NOW)
        shot("03-list")
        press("confirm")
        shot("04-detail")
        for n in range(1, 8):
            press("down")
            shot(f"05-page-{n + 1}")
        shot("06-stale", NOW + 601)
        inbox.set_status({"source": "telegram", "chat": "fixture-chat", "thread": "fixture-thread",
                          "state": "error"}, NOW + 601)
        shot("07-source-error", NOW + 602)
        cfg.unlink()
        shot("08-revoked")
        cfg.write_text("invalid policy")
        shot("09-policy-error")
    path = out / "transcriptions.txt"
    path.write_text("\n\n".join(transcript) + "\n")
    print(path)


if __name__ == "__main__":
    main(sys.argv[1])
