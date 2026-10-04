"""Synthetic public GitHub fixtures; no network or deployment."""
import copy
import importlib
import importlib.util
import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

NOW = 1791021600.0
REPO = "acgh213/alicenet-x4"
HEAD = "a" * 40
OLD = "b" * 40
ISO = "2026-10-03T08:00:00Z"


def config(**extra):
    return dict(repository=REPO, interval_s=600, stale_after_s=1800, **extra)


def pr(number=7, title="FIXTURE: readable work"):
    return {"number": number, "title": title, "user": {"login": "fixture-author"},
            "draft": False, "base": {"ref": "master"},
            "updated_at": ISO, "html_url": f"https://github.com/{REPO}/pull/{number}"}


def run(**extra):
    body = {"id": 91, "name": "tests", "head_branch": "master", "head_sha": HEAD,
            "status": "completed", "conclusion": "success", "updated_at": ISO,
            "html_url": f"https://github.com/{REPO}/actions/runs/91"}
    body.update(extra)
    return body


class GitHub:
    def __init__(self, prs=None, runs=None):
        self.prs = [pr()] if prs is None else prs
        self.runs = [run()] if runs is None else runs
        self.calls = []
        self.fail = set()
        self.branch = "master"
        self.private = False
        self.pages = {}

    def __call__(self, path, deadline):
        self.calls.append(path)
        if any(x in path for x in self.fail):
            raise OSError("untrusted response with secret text")
        if path in self.pages:
            return copy.deepcopy(self.pages[path])
        if path == f"/repos/{REPO}":
            return {"full_name": REPO, "private": self.private, "default_branch": self.branch}
        if "/branches/" in path:
            return {"name": self.branch, "commit": {"sha": HEAD}}
        if "/pulls?" in path:
            return copy.deepcopy(self.prs)
        if "/issues?" in path:
            return []
        if "/actions/runs?" in path:
            return {"workflow_runs": copy.deepcopy(self.runs)}
        raise AssertionError(path)


class Collector(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("x4_work"), "Work collector is not implemented")
        self.w = importlib.import_module("x4_work")
        self.api = GitHub()

    def collect(self, **kw):
        return self.w.collect(config(), now=NOW, http_get=self.api, **kw)

    def test_public_allowlist(self):
        for bad in ("other/repo", "", "../repo", REPO + "?token=bad"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.w.validate_config(config() | {"repository": bad})
        self.api.private = True
        snap = self.collect()
        self.assertFalse(snap["prs"]["available"])
        self.assertFalse(snap["build"]["available"])
        self.assertNotIn("secret", json.dumps(snap))

    def test_empty_prs_and_no_runs_are_available(self):
        self.api = GitHub([], [])
        snap = self.collect()
        self.assertEqual(snap["schema"], 2)
        self.assertEqual(snap["repository"]["head_sha"], HEAD)
        self.assertEqual(snap["prs"]["data"], {"items": [], "truncated": False})
        self.assertIsNone(snap["build"]["data"]["run"])
        self.assertTrue(snap["prs"]["available"])
        self.assertTrue(snap["build"]["available"])

    def test_partial_failure_keeps_original_age(self):
        old = self.collect()
        self.api.fail = {"pulls"}
        snap = self.w.collect(config(), now=NOW + 600, previous=old, http_get=self.api)
        self.assertEqual(snap["prs"]["collected_at"], NOW)
        self.assertTrue(snap["prs"]["refresh_failed"])
        self.assertEqual(snap["build"]["collected_at"], NOW + 600)
        self.assertNotIn("secret", json.dumps(snap))

    def test_changed_config_drops_cache(self):
        old = self.collect()
        old["config_key"] = "former"
        self.api.fail = {"pulls"}
        snap = self.collect(previous=old)
        self.assertFalse(snap["prs"]["available"])
        self.assertIsNone(snap["prs"]["data"])

    def test_default_branch_changes_drop_build_cache(self):
        old = self.collect()
        self.api.branch = "main"
        self.api.fail = {"actions/runs"}
        snap = self.collect(previous=old)
        self.assertFalse(snap["build"]["available"])
        self.assertTrue(snap["prs"]["available"])
        self.assertEqual(snap["repository"]["default_branch"], "main")

    def test_projection_is_bounded_and_does_not_copy_bodies(self):
        self.api.prs[0].update(title="long " * 200, body="private text", token="secret")
        item = self.collect()["prs"]["data"]["items"][0]
        self.assertLessEqual(len(item["title"]), 240)
        self.assertEqual(item["id"], f"gh:{REPO}:pr:7")
        self.assertNotIn("body", item)
        self.assertNotIn("token", item)
        self.w.validate_snapshot(self.collect(), config())

    def test_two_page_limit_marks_truncated(self):
        base = f"/repos/{REPO}/pulls?state=open&sort=updated&direction=desc&per_page=50&page="
        self.api.pages[base + "1"] = [pr(i) for i in range(1, 51)]
        self.api.pages[base + "2"] = [pr(i) for i in range(51, 101)]
        snap = self.collect()
        self.assertEqual(len(snap["prs"]["data"]["items"]), 100)
        self.assertTrue(snap["prs"]["data"]["truncated"])
        self.assertEqual(len([p for p in self.api.calls if "/pulls?" in p]), 2)

    def test_future_timestamp_rejected(self):
        self.api.prs[0]["updated_at"] = "2099-01-01T00:00:00Z"
        self.assertFalse(self.collect()["prs"]["available"])

    def test_wrong_branch_run_not_current(self):
        self.api.runs[0]["head_branch"] = "another"
        self.assertFalse(self.collect()["build"]["available"])

    def test_older_run_sha_preserved(self):
        self.api.runs[0]["head_sha"] = OLD
        snap = self.collect()
        self.assertEqual(snap["build"]["data"]["run"]["head_sha"], OLD)
        self.assertEqual(snap["repository"]["head_sha"], HEAD)

    def test_deadline_exhaustion_does_not_fetch_more(self):
        ticks = iter([0, 16, 16, 16])
        snap = self.w.collect(config(), now=NOW, http_get=self.api, monotonic=lambda: next(ticks))
        self.assertFalse(snap["prs"]["available"])
        self.assertEqual(self.api.calls, [])

    def test_atomic_write_survives_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            path.write_text('{"original": true}')
            with patch.object(self.w.os, "replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    self.w.write_snapshot(path, self.collect())
            self.assertEqual(json.loads(path.read_text()), {"original": True})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_reader_requires_current_config_and_rejects_corrupt_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            cfg = Path(directory) / "work.json"
            cfg.write_text(json.dumps(config()))
            self.w.write_snapshot(path, self.collect())
            reader = self.w.Work(path, cfg)
            self.assertEqual(reader.snapshot()["repository"]["full_name"], REPO)
            cfg.unlink()
            self.assertIsNone(reader.snapshot())
            cfg.write_text(json.dumps(config()))
            path.write_text("not json")
            self.assertIsNone(reader.snapshot())

    def test_cli_writes_failure_state_without_error_body(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / "work.json"
            out = Path(directory) / "snapshot.json"
            cfg.write_text(json.dumps(config()))
            with patch.object(self.w, "http_get", side_effect=OSError("secret")):
                self.assertEqual(self.w.main(["--config", str(cfg), "--output", str(out)]), 1)
            self.assertFalse(json.loads(out.read_text())["prs"]["available"])
            self.assertNotIn("secret", out.read_text())

    def test_transport_rejects_arbitrary_origin_and_redirects(self):
        with self.assertRaises(ValueError):
            self.w.http_get("https://evil.invalid/repos/a/b", 100)
        handler = self.w.NoRedirect()
        with self.assertRaises(self.w.urllib.error.HTTPError):
            handler.redirect_request(None, None, 302, "", {}, "https://evil.invalid")

    def test_transport_caps_response_and_timeout(self):
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read1(self, size): return b"x" * size
        class Opener:
            timeout = None
            def open(inner, req, timeout):
                inner.timeout = timeout
                self.assertEqual(req.get_method(), "GET")
                self.assertIsNone(req.get_header("Authorization"))
                return Response()
        opener = Opener()
        with patch.object(self.w.time, "monotonic", return_value=0), patch.object(
                self.w.urllib.request, "build_opener", return_value=opener):
            with self.assertRaises(ValueError):
                self.w.http_get(f"/repos/{REPO}", 15)
        self.assertEqual(opener.timeout, 5)

    def test_reader_rejects_future_and_forged_section_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path, cfg = Path(directory) / "snapshot", Path(directory) / "config"
            cfg.write_text(json.dumps(config()))
            snap = self.collect()
            snap["prs"]["data"]["items"][0]["id"] = "gh:other/repo:pr:7"
            self.w.write_snapshot(path, snap)
            self.assertIsNone(self.w.Work(path, cfg).snapshot())

    def test_new_branch_head_failure_drops_old_build(self):
        old = self.collect()
        self.api.branch = "main"
        self.api.fail = {"branches"}
        snap = self.collect(previous=old)
        self.assertEqual(snap["repository"]["default_branch"], "main")
        self.assertFalse(snap["build"]["available"])

    def test_repository_redirect_or_not_found_drops_old_data(self):
        old = self.collect()
        for code in (301, 302, 404):
            with self.subTest(code=code):
                def failed(path, deadline):
                    raise self.w.urllib.error.HTTPError(path, code, "gone", {}, None)
                snap = self.w.collect(config(), now=NOW, previous=old, http_get=failed)
                self.assertFalse(snap["prs"]["available"])
                self.assertFalse(snap["build"]["available"])

    def test_corrupt_unavailable_section_is_rejected(self):
        snap = self.collect()
        snap["prs"] = {"available": False, "collected_at": None, "refresh_failed": True,
                       "error": {}, "data": None}
        with self.assertRaises(ValueError):
            self.w.validate_snapshot(snap, config())

    def test_malformed_branch_response_writes_failure_snapshot(self):
        old = self.collect()
        def reader(path, deadline):
            return [] if "/branches/" in path else self.api(path, deadline)
        snap = self.w.collect(config(), now=NOW + 600, previous=old, http_get=reader)
        self.assertTrue(snap["prs"]["refresh_failed"])
        self.assertTrue(snap["build"]["refresh_failed"])
        self.assertEqual(snap["build"]["collected_at"], NOW)

    def test_cli_shared_lock_prevents_overlapping_collection(self):
        self.assertTrue(callable(getattr(self.w, "collection_lock", None)), "Shared collector lock is missing")
        with tempfile.TemporaryDirectory() as directory:
            cfg, out = Path(directory) / "config.json", Path(directory) / "snapshot.json"
            cfg.write_text(json.dumps(config()))
            old = self.collect()
            self.w.write_snapshot(out, old)
            with self.w.collection_lock(out) as locked:
                self.assertTrue(locked)
                with patch.object(self.w, "collect", side_effect=AssertionError("collector must not start")):
                    self.assertEqual(self.w.main(["--config", str(cfg), "--output", str(out)]), 1)
                self.assertEqual(json.loads(out.read_text()), old)
            with self.w.collection_lock(out) as locked:
                self.assertTrue(locked)

    def test_absolute_deadline_includes_slow_http_headers(self):
        stop = threading.Event()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2)
        def serve():
            try:
                with listener.accept()[0] as connection:
                    connection.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    for _ in range(60):
                        if stop.wait(0.02):
                            return
                        connection.sendall(b"x")
                    connection.sendall(b"\r\nContent-Length: 2\r\n\r\n{}")
            except OSError:
                pass
        worker = threading.Thread(target=serve)
        worker.start()
        try:
            started = time.monotonic()
            with patch.object(self.w, "API", f"http://127.0.0.1:{listener.getsockname()[1]}"):
                with self.assertRaises(TimeoutError):
                    self.w.http_get(f"/repos/{REPO}", started + 0.15)
            self.assertLess(time.monotonic() - started, 0.35)
        finally:
            stop.set()
            listener.close()
            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())

    def test_head_without_branch_and_malformed_head_are_rejected(self):
        for head in ({"bad": 1}, HEAD):
            with self.subTest(head=head):
                snap = self.collect()
                snap["repository"].update(default_branch=None, head_sha=head)
                for section in ("prs", "build"):
                    snap[section] = {"available": False, "collected_at": None,
                                     "refresh_failed": True, "error": "unavailable", "data": None}
                with self.assertRaises(ValueError):
                    self.w.validate_snapshot(snap, config())

    @unittest.skipUnless(hasattr(__import__("signal"), "setitimer"), "Linux collector deadline")
    def test_absolute_deadline_interrupts_blocked_resolver(self):
        import signal
        prior_handler = signal.getsignal(signal.SIGALRM)
        prior_timer = signal.getitimer(signal.ITIMER_REAL)
        started = time.monotonic()
        with patch.object(socket, "getaddrinfo", side_effect=lambda *args, **kwargs: time.sleep(1)):
            with self.assertRaises(OSError):
                self.w.http_get(f"/repos/{REPO}", started + 0.15)
        self.assertLess(time.monotonic() - started, 0.35)
        self.assertEqual(signal.getsignal(signal.SIGALRM), prior_handler)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), prior_timer)

    def test_nested_json_snapshot_and_config_fail_closed(self):
        nested = "[" * 10000 + "0" + "]" * 10000
        with tempfile.TemporaryDirectory() as directory:
            cfg, out = Path(directory) / "config.json", Path(directory) / "snapshot.json"
            cfg.write_text(json.dumps(config()))
            out.write_text(nested)
            self.assertIsNone(self.w.Work(out, cfg).snapshot())
            cfg.write_text(nested)
            self.assertFalse(self.w.Work(out, cfg).configured())
            self.assertIsNone(self.w.Work(out, cfg).snapshot())
            self.assertEqual(self.w.main(["--config", str(cfg), "--output", str(out)]), 1)

    def test_transport_nested_json_becomes_invalid_input_failure(self):
        raw = ("[" * 10000 + "0" + "]" * 10000).encode()
        class Response:
            headers = {}
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read1(self, size):
                result, self.body = self.body[:size], self.body[size:]
                return result
        response = Response()
        response.body = raw
        class Opener:
            def open(self, *args, **kwargs): return response
        with patch.object(self.w.urllib.request, "build_opener", return_value=Opener()):
            with self.assertRaises(ValueError):
                self.w.http_get(f"/repos/{REPO}", time.monotonic() + 1)

    def test_nested_ignored_cache_fields_cannot_crash_failure_retention(self):
        for location in ("root", "repository", "section", "data"):
            with self.subTest(location=location), tempfile.TemporaryDirectory() as directory:
                cfg, out = Path(directory) / "config.json", Path(directory) / "snapshot.json"
                cfg.write_text(json.dumps(config()))
                snap = self.collect()
                target = {"root": snap, "repository": snap["repository"],
                          "section": snap["prs"], "data": snap["prs"]["data"]}[location]
                target["extra"] = "NESTED_PLACEHOLDER"
                raw = json.dumps(snap).replace('"NESTED_PLACEHOLDER"', "[" * 600 + "0" + "]" * 600)
                json.loads(raw)  # This is parseable, but too deep for deepcopy.
                out.write_text(raw)
                self.assertIsNone(self.w.Work(out, cfg).snapshot())
                with patch.object(self.w, "http_get", side_effect=OSError("offline")):
                    self.assertEqual(self.w.main(["--config", str(cfg), "--output", str(out)]), 1)
                failure = self.w.Work(out, cfg).snapshot()
                self.assertIsNotNone(failure)
                self.assertTrue(failure["prs"]["refresh_failed"])
                self.assertFalse(failure["prs"]["available"])

    def test_network_decoder_failure_writes_honest_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg, out = Path(directory) / "config.json", Path(directory) / "snapshot.json"
            cfg.write_text(json.dumps(config()))
            self.w.write_snapshot(out, self.collect())
            with patch.object(self.w, "http_get", side_effect=RecursionError("deep provider JSON")):
                self.assertEqual(self.w.main(["--config", str(cfg), "--output", str(out)]), 1)
            snap = json.loads(out.read_text())
            self.assertTrue(snap["prs"]["refresh_failed"])
            self.assertEqual(snap["prs"]["error"], "invalid response")


if __name__ == "__main__":
    unittest.main()
