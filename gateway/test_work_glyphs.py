"""External Work text must rasterize safely without changing source identity."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_x4_dashboard as dash
import x4_work
import x4_work_view
import x4d
from test_menu_work import WorkMenu
from test_work_issues import IssueAPI, issue
from test_x4_dashboard import fixture, NOW
from test_x4_work import config, pr, run
from test_x4d import Server

STACK = "a" + "\u0301" * 5
VISIBLE = "á" + "\\u0301" * 4


def snapshot(kind="issue", text=STACK):
    api = IssueAPI([issue(title=text)])
    if kind == "pr":
        api.issues = []
        api.prs = [pr(title=text)]
        api.prs[0]["user"]["login"] = text
        api.prs[0]["base"]["ref"] = text
    elif kind == "build":
        api.issues = []
        api.prs = []
        api.branch = text
        api.runs = [run(name=text, head_branch=text)]
    else:
        api.prs = []
        api.issues[0]["user"]["login"] = text
    result = x4_work.collect(config(), now=NOW, http_get=api)
    return x4_work.validate_snapshot(result, config())


def select(menu, snap, kind, detail=False, selected=True):
    item = next(i for i in x4_work_view.work_items(snap) if i["kind"] == kind)
    index = x4_work_view.work_items(snap).index(item)
    state = menu.state("x4-01")
    state.update(view="work_detail" if detail else "work", cursor=index if selected else 0,
                 work_id=item["id"], work_revision=x4_work_view.revision(item), page=0)
    menu._save("x4-01", state)
    return item


class WorkGlyphMenu(WorkMenu):
    def install(self, snap, kind="issue", detail=False, selected=True):
        self.write(snap)
        return select(self.menu, snap, kind, detail, selected)

    def test_combining_issue_selected_unselected_and_detail_rasters(self):
        for detail, selected in ((False, False), (False, True), (True, True)):
            with self.subTest(detail=detail, selected=selected):
                original = snapshot()
                before = copy.deepcopy(original)
                item = self.install(original, detail=detail, selected=selected)
                frame = self.frame()
                dash.Dashboard.assert_layout(self, frame["image"])
                expected = snapshot(text=VISIBLE)
                self.install(expected, detail=detail, selected=selected)
                self.assertEqual(frame["pbm"], self.frame()["pbm"])
                self.assertEqual(original, before)
                self.assertEqual(item["title"], STACK)
                self.assertEqual(item["id"], original["issues"]["data"]["items"][0]["id"])
                self.assertNotEqual(x4_work_view.revision(item), x4_work_view.revision(
                    dict(item, title=VISIBLE)))

    def test_pr_title_author_target_and_build_workflow_branch_rasters(self):
        for kind in ("pr", "build"):
            for detail in (False, True):
                with self.subTest(kind=kind, detail=detail):
                    original = snapshot(kind)
                    before = copy.deepcopy(original)
                    self.install(original, kind, detail)
                    frame = self.frame()
                    dash.Dashboard.assert_layout(self, frame["image"])
                    self.install(snapshot(kind, VISIBLE), kind, detail)
                    self.assertEqual(frame["pbm"], self.frame()["pbm"])
                    self.assertEqual(original, before)

    def test_missing_bmp_and_non_bmp_glyphs_have_literal_distinct_rasters(self):
        rasters = []
        for value, visible in (("北京", "\\u5317\\u4eac"),
                               ("上海", "\\u4e0a\\u6d77"),
                               ("\U0001fae0", "\\U0001fae0")):
            for detail in (False, True):
                with self.subTest(value=value, detail=detail):
                    self.install(snapshot(text=value), detail=detail)
                    frame = self.frame()
                    dash.Dashboard.assert_layout(self, frame["image"])
                    self.install(snapshot(text=visible), detail=detail)
                    self.assertEqual(frame["pbm"], self.frame()["pbm"])
                    if detail:
                        rasters.append(frame["pbm"])
        self.assertEqual(len(set(rasters)), len(rasters))

    def test_long_combining_details_preserve_all_pages_and_navigation(self):
        snap = snapshot(text="a" + "\u0301" * 239)
        self.install(snap, detail=True)
        original_pages = x4_work_view.detail_pages(x4_work_view.work_items(snap)[-1], snap)
        expected = snapshot(text="á" + "\\u0301" * 238)
        # Literal display text exceeds the collector's 240-character source cap;
        # compare the entire transformed source, not a recollected truncated one.
        expected["issues"]["data"]["items"][0].update(
            title="á" + "\\u0301" * 238, author="á" + "\\u0301" * 78)
        expected_pages = x4_work_view.detail_pages(x4_work_view.work_items(expected)[-1], expected)
        self.assertEqual(original_pages, expected_pages)
        self.assertGreater(len(original_pages), 1)
        for page in range(len(original_pages)):
            frame = self.frame()
            dash.Dashboard.assert_layout(self, frame["image"])
            self.assertIn(f"{page + 1}/{len(original_pages)}", self.texts())
            self.press("down")
        self.assertEqual(self.menu.state("x4-01")["page"], len(original_pages) - 1)
        self.press("back")
        self.assertEqual(self.frame()["card"], "work")

    def test_supported_accent_raster_keeps_raw_revision(self):
        snap = snapshot(text="Cafe\u0301")
        item = self.install(snap, detail=True)
        frame = self.frame()
        self.install(snapshot(text="Café"), detail=True)
        self.assertEqual(frame["pbm"], self.frame()["pbm"])
        self.assertEqual(item["title"], "Cafe\u0301")
        with self.store._db() as db:
            shown = json.loads(db.execute("SELECT body FROM glance_frames WHERE etag=?",
                                         (frame["etag"],)).fetchone()[0])
        self.assertEqual(shown["work_revision"], x4_work_view.revision(item))


for _name in dir(WorkMenu):
    if _name.startswith("test_") and _name not in WorkGlyphMenu.__dict__:
        setattr(WorkGlyphMenu, _name, None)
del WorkMenu


class WorkGlyphHTTP(Server):
    def setUp(self):
        super().setUp()
        root = Path(self.dir.name)
        glance, cfg = root / "glance.json", root / "work-config.json"
        self.path = root / "work.json"
        glance.write_text(json.dumps(fixture()))
        cfg.write_text(json.dumps(config()))
        self.app = x4d.App(dict(self.app.cfg, glance_snapshot=str(glance),
                               work_config=str(cfg), work_snapshot=str(self.path)))
        self.httpd.RequestHandlerClass.app = self.app

    def check_frame(self, kind, text=STACK, detail=False, selected=True):
        snap = snapshot(kind, text)
        x4_work.write_snapshot(self.path, snap)
        item = select(self.app.menu, snap, kind, detail, selected)
        with patch("x4d.time.time", return_value=NOW):
            status, headers, body = self.call("GET", "/x4/v1/frame", headers={"X-Wake": "session"})
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 48011)
        self.assertTrue(body.startswith(b"P4\n800 480\n"))
        if detail:
            self.assertTrue(headers["X-Card"].startswith("work."))
            self.assertEqual(self.app.menu.state("x4-01")["work_revision"],
                             x4_work_view.revision(item))

    def test_ordinary_title_control(self):
        self.check_frame("issue", "FIXTURE: public issue")

    def test_combining_issue_overview_selected_and_unselected_http200(self):
        for selected in (False, True):
            with self.subTest(selected=selected):
                self.check_frame("issue", selected=selected)

    def test_combining_issue_detail_http200(self):
        self.check_frame("issue", detail=True)

    def test_combining_pr_and_build_overview_detail_http200(self):
        for kind in ("pr", "build"):
            for detail in (False, True):
                with self.subTest(kind=kind, detail=detail):
                    self.check_frame(kind, detail=detail)


if __name__ == "__main__":
    unittest.main()
