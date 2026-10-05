"""Public issue browsing through the existing read-only Work path."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import x4_work
import x4_work_view
from test_x4_work import GitHub, config, NOW, REPO, ISO, pr
from test_menu_work import WorkMenu
import test_x4_dashboard as dash


def issue(number=3, title="FIXTURE: public issue"):
    return {"number": number, "title": title, "user": {"login": "fixture-author"},
            "state": "open", "updated_at": ISO, "html_url": f"https://github.com/{REPO}/issues/{number}"}


class IssueAPI(GitHub):
    def __init__(self, issues=None):
        super().__init__()
        self.issues = [issue()] if issues is None else issues

    def __call__(self, path, deadline):
        if "/issues?" in path:
            self.calls.append(path)
            if "issues" in self.fail:
                raise OSError("secret provider error")
            return copy.deepcopy(self.pages.get(path, self.issues))
        return super().__call__(path, deadline)


class IssueCollector(unittest.TestCase):
    def collect(self, api=None, **extra):
        return x4_work.collect(config(), now=NOW, http_get=api or IssueAPI(), **extra)

    def test_issue_projection_filters_pull_requests_and_omits_bodies(self):
        pull = pr(9) | {"pull_request": {"url": "ignored"}}
        snap = self.collect(IssueAPI([pull, issue() | {"body": "secret body", "token": "secret"}]))
        self.assertEqual(snap["schema"], 2)
        self.assertEqual([i["number"] for i in snap["issues"]["data"]["items"]], [3])
        self.assertEqual(snap["issues"]["data"]["items"][0]["id"], f"gh:{REPO}:issue:3")
        self.assertNotIn("secret", json.dumps(snap))
        x4_work.validate_snapshot(snap, config())

    def test_two_mixed_pages_have_raw_record_cap_and_honest_truncation(self):
        api = IssueAPI()
        prefix = f"/repos/{REPO}/issues?state=open&sort=updated&direction=desc&per_page=50&page="
        api.pages[prefix + "1"] = [pr(i) | {"pull_request": {}} for i in range(1, 51)]
        api.pages[prefix + "2"] = [issue(i) for i in range(51, 101)]
        snap = self.collect(api)
        self.assertEqual(len(snap["issues"]["data"]["items"]), 50)
        self.assertTrue(snap["issues"]["data"]["truncated"])
        self.assertEqual(len([p for p in api.calls if "/issues?" in p]), 2)

    def test_issue_failures_keep_age_without_affecting_pr_or_build(self):
        old = self.collect()
        api = IssueAPI()
        api.fail = {"issues"}
        snap = x4_work.collect(config(), now=NOW + 600, previous=old, http_get=api)
        self.assertEqual(snap["issues"]["collected_at"], NOW)
        self.assertTrue(snap["issues"]["refresh_failed"])
        self.assertFalse(snap["prs"]["refresh_failed"])
        self.assertFalse(snap["build"]["refresh_failed"])
        self.assertNotIn("secret", json.dumps(snap))
        with tempfile.TemporaryDirectory() as directory:
            cfg, out = Path(directory) / "config", Path(directory) / "snapshot"
            cfg.write_text(json.dumps(config()))
            x4_work.write_snapshot(out, old)
            with patch.object(x4_work, "http_get", side_effect=api):
                self.assertEqual(x4_work.main(["--config", str(cfg), "--output", str(out)]), 1)
            retained = x4_work.Work(out, cfg).snapshot()
            self.assertTrue(retained["issues"]["refresh_failed"])
            self.assertTrue(retained["prs"]["available"])

    def test_legacy_snapshot_keeps_pr_build_and_adds_unavailable_issues(self):
        original = self.collect()
        legacy = copy.deepcopy(original)
        legacy["schema"] = 1
        legacy.pop("issues", None)
        migrated = x4_work.validate_snapshot(legacy, config())
        self.assertEqual(migrated["schema"], 2)
        self.assertEqual(migrated["prs"], legacy["prs"])
        self.assertEqual(migrated["build"], legacy["build"])
        self.assertFalse(migrated["issues"]["available"])
        self.assertNotIn("issues", legacy)
        with tempfile.TemporaryDirectory() as directory:
            cfg, out = Path(directory) / "config", Path(directory) / "snapshot"
            cfg.write_text(json.dumps(config()))
            x4_work.write_snapshot(out, legacy)
            self.assertIsNotNone(x4_work.Work(out, cfg).snapshot())
            with patch.object(x4_work, "http_get", side_effect=IssueAPI()):
                self.assertEqual(x4_work.main(["--config", str(cfg), "--output", str(out)]), 0)
            self.assertTrue(x4_work.Work(out, cfg).snapshot()["issues"]["available"])

    def test_malformed_duplicate_closed_future_and_foreign_issues_fail_closed(self):
        bad_lists = [[issue(), issue()], [issue() | {"state": "closed"}],
                     [issue() | {"updated_at": "2099-01-01T00:00:00Z"}],
                     [issue() | {"html_url": "https://evil.invalid/issues/3"}], [issue() | {"number": True}]]
        for raw in bad_lists:
            with self.subTest(raw=raw):
                snap = self.collect(IssueAPI(raw))
                self.assertFalse(snap["issues"]["available"])
                self.assertEqual(snap["issues"]["error"], "invalid response")
                self.assertTrue(snap["prs"]["available"])

    def test_new_schema_requires_issue_section_and_valid_cached_identity(self):
        snap = self.collect()
        snap.pop("issues", None)
        snap["schema"] = 2
        with self.assertRaises(ValueError):
            x4_work.validate_snapshot(snap, config())
        snap = self.collect()
        snap["issues"]["data"]["items"][0]["id"] = f"gh:{REPO}:pr:3"
        with self.assertRaises(ValueError):
            x4_work.validate_snapshot(snap, config())


class IssueMenu(WorkMenu):
    # Reuse the setup/navigation helpers, without running inherited tests twice.
    def setUp(self):
        super().setUp()
        self.write(x4_work.collect(config(), now=NOW, http_get=IssueAPI()))

    def select_issue(self):
        self.open_work()
        rows = x4_work_view.work_items(self.menu.work.snapshot())
        for _ in range(next(n for n, i in enumerate(rows) if i["kind"] == "issue")):
            self.press("down")

    def test_issue_detail_is_frame_bound_after_reorder_and_removal(self):
        self.write(x4_work.collect(config(), now=NOW, http_get=IssueAPI([issue(3), issue(4)])))
        self.select_issue()
        shown = self.frame()
        changed = self.menu.work.snapshot()
        changed["issues"]["data"]["items"].reverse()
        self.write(changed)
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["work_id"], f"gh:{REPO}:issue:3")
        self.assertIn("Issue #3", self.texts())
        self.press("back")
        self.press("down")
        shown = self.frame()
        changed["issues"]["data"]["items"] = []
        self.write(changed)
        self.press("confirm", shown=shown)
        self.assertEqual(self.menu.state("x4-01")["view"], "work")
        self.assertIn("item changed", self.texts())

    def test_issue_long_details_paginate_and_back_works(self):
        self.write(x4_work.collect(config(), now=NOW, http_get=IssueAPI([issue(title="wide WWW " * 60)])))
        self.select_issue()
        self.press("confirm")
        first = self.frame()
        self.press("down")
        self.assertNotEqual(first["etag"], self.frame()["etag"])
        self.press("back")
        self.assertEqual(self.frame()["card"], "work")
        self.assertEqual(self.press("confirm", "long")["refresh_target"], "work")

    def test_issue_empty_unavailable_stale_and_capped_states_fit(self):
        empty = x4_work.collect(config(), now=NOW, http_get=IssueAPI([]))
        self.write(empty)
        self.open_work()
        self.assertIn("No open issues", self.texts())
        capped = copy.deepcopy(empty)
        capped["issues"]["data"]["truncated"] = True
        self.write(capped)
        self.assertIn("issue list capped", self.texts())
        self.assertNotIn("No open issues", self.texts())
        stale = x4_work.collect(config(), now=NOW, http_get=IssueAPI())
        stale["issues"].update(collected_at=NOW - 3600, refresh_failed=True, error="unreachable")
        self.write(stale)
        self.assertIn("Issues: STALE", self.texts())
        self.assertIn("Refresh failed", self.texts())
        dash.Dashboard.assert_layout(self, self.frame()["image"])
        unavailable = copy.deepcopy(stale)
        unavailable["issues"] = {"available": False, "collected_at": None, "refresh_failed": True,
                                  "error": "unreachable", "data": None}
        self.write(unavailable)
        self.assertIn("Issues: Unavailable", self.texts())
        dash.Dashboard.assert_layout(self, self.frame()["image"])


# Suppress inherited unittest methods; those run once in test_menu_work.
for _name in dir(WorkMenu):
    if _name.startswith("test_") and _name not in IssueMenu.__dict__:
        setattr(IssueMenu, _name, None)
del WorkMenu
