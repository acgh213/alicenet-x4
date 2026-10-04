"""Work source and refresh over the same HTTP contract used by the device."""
import json
from pathlib import Path
import sys
import time
import unittest

import x4d
import x4_work
import test_x4d as http_fixture
from test_x4_dashboard import fixture, NOW
from test_x4_work import config, GitHub


class WorkHTTP(http_fixture.Server):
    def setUp(self):
        super().setUp()
        self.root = Path(self.dir.name)
        glance = self.root / "glance.json"
        glance.write_text(json.dumps(fixture()))
        self.path, self.cfg = self.root / "work.json", self.root / "config.json"
        self.cfg.write_text(json.dumps(config()))
        self.snapshot = x4_work.collect(config(), now=time.time(), http_get=GitHub())
        x4_work.write_snapshot(self.path, self.snapshot)
        self.count = self.root / "count.txt"
        script = self.root / "refresh.py"
        # Real subprocess, offline: emits a failure-marked source and nonzero exit.
        script.write_text(
            "import json,os,sys\nfrom pathlib import Path\n"
            "p=Path(sys.argv[1]); count=Path(sys.argv[2])\n"
            "count.write_text(str(int(count.read_text())+1) if count.exists() else '1')\n"
            "s=json.loads(p.read_text()); s['prs']['refresh_failed']=True; s['prs']['error']='rate-limited'\n"
            "q=p.with_suffix('.tmp'); q.write_text(json.dumps(s)); os.replace(q,p); sys.exit(1)\n")
        self.command = [sys.executable, str(script), str(self.path), str(self.count)]
        self.app = x4d.App(dict(self.app.cfg, glance_snapshot=str(glance),
                              work_snapshot=str(self.path), work_config=str(self.cfg),
                              work_refresh_cmd=self.command))
        self.httpd.RequestHandlerClass.app = self.app
        self.seq = 0
        self.assertTrue(hasattr(self.app, "refresh_work"), "Work refresh is not implemented")

    def frame(self, wake="session"):
        status, headers, body = self.call("GET", "/x4/v1/frame", headers={"X-Wake": wake})
        self.assertEqual(status, 200)
        return headers, body

    def press(self, button, press="short", batch=None):
        if batch is None:
            shown, _ = self.frame()
            self.seq += 1
            batch = {"device": "x4-01", "boot": 1, "events": [{
                "seq": self.seq, "card": shown["X-Card"], "etag": shown["ETag"],
                "button": button, "press": press, "wake": "button"}]}
        status, headers, body = self.call("POST", "/x4/v1/events", batch)
        self.assertEqual(status, 200)
        return batch, headers

    def open_work(self):
        self.press("back")
        self.press("down")
        self.press("confirm")
        self.assertEqual(self.frame()[0]["X-Card"], "work")

    def wait_refresh(self, before):
        with self.app.changed:
            self.assertTrue(self.app.changed.wait_for(lambda: self.app.generation > before, timeout=4))

    def test_work_hold_only_refreshes_work_and_does_not_forward_muse(self):
        self.open_work()
        before = self.app.generation
        self.press("confirm", "long")
        self.wait_refresh(before)
        self.assertEqual(self.count.read_text(), "1")
        self.assertTrue(x4_work.Work(self.path, self.cfg).snapshot()["prs"]["refresh_failed"])
        self.assertEqual(self.app.store.pending_forwards(), [])
        self.assertEqual(self.app.glance.snapshot()["weather"]["available"], True)

    def test_failed_refresh_snapshot_bumps_frame(self):
        self.open_work()
        shown, old = self.frame()
        before = self.app.generation
        self.assertFalse(self.app.refresh_work())  # real nonzero exit, cache written
        self.assertGreater(self.app.generation, before)
        current, new = self.frame()
        self.assertNotEqual(shown["ETag"], current["ETag"])
        self.assertNotEqual(old, new)

    def test_duplicate_event_runs_once(self):
        self.open_work()
        before = self.app.generation
        batch, _ = self.press("confirm", "long")
        self.wait_refresh(before)
        generation = self.app.generation
        self.press("confirm", "long", batch=batch)
        self.assertEqual(self.count.read_text(), "1")
        self.assertEqual(self.app.generation, generation)

    def test_lock_contention_coalesces(self):
        with self.app.refresh_lock:
            self.assertFalse(self.app.refresh_work())
        self.assertFalse(self.count.exists())

    def test_house_hold_still_refreshes_sources(self):
        # A real command on the existing HA refresh path must remain separate.
        marker = self.root / "house-refresh.txt"
        self.app.cfg["refresh_cmd"] = [sys.executable, "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('HA')", str(marker)]
        self.press("back")
        for _ in range(3):
            self.press("down")
        self.press("confirm")
        self.assertEqual(self.frame()[0]["X-Card"], "house")
        before = self.app.generation
        self.press("confirm", "long")
        self.wait_refresh(before)
        self.assertEqual(marker.read_text(), "HA")
        self.assertFalse(self.count.exists())

    def test_invalid_command_is_not_shell_executed(self):
        self.app.cfg["work_refresh_cmd"] = "echo should-never-run"
        self.assertFalse(self.app.refresh_work())
        self.assertFalse(self.count.exists())

    def test_config_removal_hides_data_and_timer_wake_goes_home(self):
        self.open_work()
        self.cfg.unlink()
        _, body = self.frame()
        self.assertTrue(body.startswith(b"P4\n800 480\n"))
        self.assertIsNone(self.app.work.snapshot())
        self.assertEqual(self.frame("timer")[0]["X-Card"], "glance.home")


if __name__ == "__main__":
    unittest.main()
