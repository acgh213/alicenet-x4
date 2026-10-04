"""Public, GET-only Work source. Collection is separate from device rendering."""
import argparse
from contextlib import contextmanager
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

REPOSITORY = "acgh213/alicenet-x4"
API = "https://api.github.com"
BODY_LIMIT = 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{40}")
STATUSES = {"queued", "in_progress", "completed", "waiting", "requested", "pending"}
CONCLUSIONS = {"success", "failure", "neutral", "cancelled", "skipped", "timed_out",
               "action_required", "stale", "startup_failure"}


def validate_config(body):
    if not isinstance(body, dict) or set(body) - {"repository", "interval_s", "stale_after_s"}:
        raise ValueError("invalid Work config")
    if body.get("repository") != REPOSITORY:
        raise ValueError("Work requires the approved public repository")
    out = dict(repository=REPOSITORY, interval_s=600, stale_after_s=1800)
    out.update(body)
    if any(type(out[k]) is not int or out[k] != expected
           for k, expected in (("interval_s", 600), ("stale_after_s", 1800))):
        raise ValueError("Work interval/freshness must be 600/1800 seconds")
    return out


def config_key(config):
    return hashlib.sha256(json.dumps(validate_config(config), sort_keys=True).encode()).hexdigest()


def _text(value, limit=240):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing text")
    return " ".join(value.split())[:limit].rstrip()


def _number(value):
    if type(value) is not int or value <= 0:
        raise ValueError("invalid item number")
    return value


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("invalid commit")
    return value


def _stamp(value, now):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError("invalid observation")
    stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not stamp.tzinfo or stamp.timestamp() > now + 5:
        raise ValueError("invalid observation")
    return stamp.timestamp()


def _url(value, suffix):
    expected = f"https://github.com/{REPOSITORY}/{suffix}"
    if value != expected:
        raise ValueError("invalid source URL")
    return expected


def project_pr(raw, now):
    number = _number(raw["number"])
    if type(raw.get("draft")) is not bool:
        raise ValueError("invalid draft state")
    return {"id": f"gh:{REPOSITORY}:pr:{number}", "number": number,
            "title": _text(raw["title"]), "author": _text(raw["user"]["login"], 80),
            "draft": raw["draft"], "base": _text(raw["base"]["ref"], 240),
            "updated_at": _stamp(raw["updated_at"], now),
            "url": _url(raw["html_url"], f"pull/{number}")}


def project_run(raw, branch, now):
    ident = _number(raw["id"])
    status, conclusion = raw.get("status"), raw.get("conclusion")
    if raw.get("head_branch") != branch or status not in STATUSES:
        raise ValueError("invalid build branch/status")
    if status == "completed" and conclusion not in CONCLUSIONS:
        raise ValueError("invalid build conclusion")
    if status != "completed" and conclusion is not None:
        raise ValueError("invalid unfinished build")
    return {"id": f"gh:{REPOSITORY}:run:{ident}", "run_id": ident,
            "name": _text(raw["name"], 160), "branch": branch,
            "head_sha": _sha(raw["head_sha"]), "status": status, "conclusion": conclusion,
            "updated_at": _stamp(raw["updated_at"], now),
            "url": _url(raw["html_url"], f"actions/runs/{ident}")}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, "redirect refused", headers, fp)


def http_get(path, deadline):
    """One bounded unauthenticated GET to a generated API-relative path."""
    if not isinstance(path, str) or not path.startswith(f"/repos/{REPOSITORY}") or "://" in path:
        raise ValueError("invalid Work API path")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError()
    request = urllib.request.Request(API + path, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "alicenet-x4-work/1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=min(5, remaining)) as response:
        length = response.headers.get("Content-Length")
        if length is not None and int(length) > BODY_LIMIT:
            raise ValueError("Work response too large")
        chunks, total = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError()
            # read1 performs a single underlying read, so slow-drip responses
            # return control to the overall deadline check.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(min(5, remaining))
            chunk = response.read1(min(65536, BODY_LIMIT + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > BODY_LIMIT:
                raise ValueError("Work response too large")
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


def _error(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return "rate-limited" if exc.code in (403, 429) else "unavailable"
    if isinstance(exc, (KeyError, TypeError, ValueError)):
        return "invalid response"
    if isinstance(exc, TimeoutError):
        return "timed out"
    return "unreachable"


def _failed(old, error):
    if isinstance(old, dict) and old.get("available"):
        return dict(copy.deepcopy(old), refresh_failed=True, error=error)
    return {"available": False, "collected_at": None, "refresh_failed": True,
            "error": error, "data": None}


def collect(config, *, now, previous=None, http_get=None, monotonic=None):
    config = validate_config(config)
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError("invalid collection time")
    clock = monotonic or time.monotonic
    deadline = clock() + 15
    reader = http_get or globals()["http_get"]
    key = config_key(config)
    old = previous if isinstance(previous, dict) and previous.get("config_key") == key else {}
    result = {"schema": 1, "config_key": key, "stale_after_s": 1800,
              "repository": {"full_name": REPOSITORY, "default_branch": None, "head_sha": None}}
    def fetch(path):
        if clock() >= deadline:
            raise TimeoutError()
        return reader(path, deadline)
    branch = None
    try:
        repo = fetch(f"/repos/{REPOSITORY}")
        if not isinstance(repo, dict):
            raise ValueError("invalid repository metadata")
        if repo["full_name"] != REPOSITORY or repo.get("private") is not False:
            # A changed visibility/identity never retains public cache.
            old = {}
            raise ValueError("repository is not public")
        branch = _text(repo["default_branch"], 240)
        # Record a newly observed default branch before its fallible head read.
        result["repository"]["default_branch"] = branch
        head = fetch(f"/repos/{REPOSITORY}/branches/" + quote(branch, safe=""))
        if not isinstance(head, dict) or head.get("name") != branch:
            raise ValueError("branch changed")
        result["repository"].update(default_branch=branch, head_sha=_sha(head["commit"]["sha"]))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        if isinstance(exc, urllib.error.HTTPError) and exc.code in (301, 302, 303, 307, 308, 401, 404, 410):
            old = {}  # repository disappeared, became private, or moved
        prior_branch = old.get("repository", {}).get("default_branch")
        if branch is None and old.get("repository"):
            result["repository"] = copy.deepcopy(old["repository"])
        elif branch == prior_branch:
            result["repository"]["head_sha"] = old.get("repository", {}).get("head_sha")
        for section in ("prs", "build"):
            prior = old.get(section)
            if section == "build" and branch is not None and prior_branch != branch:
                prior = None
            result[section] = _failed(prior, _error(exc))
        return result
    for section in ("prs", "build"):
        try:
            if section == "prs":
                items, truncated = [], False
                for page in (1, 2):
                    raw = fetch(f"/repos/{REPOSITORY}/pulls?state=open&sort=updated&direction=desc&per_page=50&page={page}")
                    if type(raw) is not list or len(raw) > 50:
                        raise ValueError("invalid pull list")
                    items.extend(project_pr(p, now) for p in raw)
                    if len(raw) < 50:
                        break
                    truncated = page == 2  # conservatively label a full capped page
                if len({p["id"] for p in items}) != len(items):
                    raise ValueError("duplicate pulls")
                data = {"items": items, "truncated": truncated}
            else:
                query = urlencode({"branch": branch, "per_page": 1})
                raw = fetch(f"/repos/{REPOSITORY}/actions/runs?{query}")["workflow_runs"]
                if type(raw) is not list or len(raw) > 1:
                    raise ValueError("invalid build list")
                data = {"run": project_run(raw[0], branch, now) if raw else None}
            result[section] = {"available": True, "collected_at": now, "refresh_failed": False,
                               "error": None, "data": data}
        except (OSError, ValueError, TypeError, KeyError) as exc:
            prior = old.get(section)
            if section == "build" and old.get("repository", {}).get("default_branch") != branch:
                prior = None
            result[section] = _failed(prior, _error(exc))
    return result


def write_snapshot(path, snapshot):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".x4-work-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(snapshot, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read(path):
    with open(path, "rb") as stream:
        raw = stream.read(BODY_LIMIT + 1)
    if len(raw) > BODY_LIMIT:
        raise ValueError("snapshot too large")
    return json.loads(raw)


def validate_snapshot(snapshot, config):
    """Fail closed on malformed/foreign cached data before displaying it."""
    if not isinstance(snapshot, dict) or snapshot.get("schema") != 1 or snapshot.get("config_key") != config_key(config):
        raise ValueError("invalid snapshot")
    repo = snapshot["repository"]
    if repo["full_name"] != REPOSITORY or snapshot["stale_after_s"] != 1800:
        raise ValueError("wrong snapshot source")
    branch = repo["default_branch"]
    if branch is not None:
        _text(branch, 240)
        if repo["head_sha"] is not None:
            _sha(repo["head_sha"])
    for name in ("prs", "build"):
        section = snapshot[name]
        if type(section["available"]) is not bool or type(section["refresh_failed"]) is not bool:
            raise ValueError("invalid section")
        if section["error"] not in (None, "rate-limited", "unavailable", "invalid response",
                                     "timed out", "unreachable"):
            raise ValueError("invalid section error")
        if not section["available"]:
            if section["data"] is not None:
                raise ValueError("unavailable section has data")
            if section["collected_at"] is not None:
                raise ValueError("unavailable section has observation")
            continue
        stamp = section["collected_at"]
        if type(stamp) not in (int, float) or not math.isfinite(stamp):
            raise ValueError("invalid collection time")
        if branch is None:
            raise ValueError("missing branch")
        data = section["data"]
        if name == "prs":
            if type(data["items"]) is not list or len(data["items"]) > 100 or type(data["truncated"]) is not bool:
                raise ValueError("invalid cached pulls")
            for p in data["items"]:
                raw = {"number": p["number"], "title": p["title"], "user": {"login": p["author"]},
                       "draft": p["draft"], "base": {"ref": p["base"]}, "html_url": p["url"],
                       "updated_at": dt.datetime.fromtimestamp(p["updated_at"], dt.timezone.utc).isoformat()}
                if project_pr(raw, stamp) != p:
                    raise ValueError("invalid cached pull")
            if len({p["id"] for p in data["items"]}) != len(data["items"]):
                raise ValueError("duplicate cached pulls")
        elif data["run"] is not None:
            r = data["run"]
            raw = {"id": r["run_id"], "name": r["name"], "head_branch": r["branch"],
                   "head_sha": r["head_sha"], "status": r["status"], "conclusion": r["conclusion"],
                   "updated_at": dt.datetime.fromtimestamp(r["updated_at"], dt.timezone.utc).isoformat(),
                   "html_url": r["url"]}
            if project_run(raw, branch, stamp) != r:
                raise ValueError("invalid cached build")
    return snapshot


class Work:
    def __init__(self, snapshot_path, config_path=None):
        self.path, self.config_path = Path(snapshot_path), Path(config_path) if config_path else None

    def snapshot(self):
        if self.config_path is None:
            return None
        try:
            return validate_snapshot(_read(self.path), validate_config(_read(self.config_path)))
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            return None

    def configured(self):
        try:
            validate_config(_read(self.config_path))
            return True
        except (OSError, ValueError, TypeError):
            return False


@contextmanager
def collection_lock(path):
    """Serialize timer/manual CLI writers for one snapshot, across processes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+b") as stream:
        locked = False
        try:
            if os.name == "nt":
                import msvcrt
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            pass
        try:
            yield locked
        finally:
            if locked:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path.home() / ".config/x4d/work.json"))
    parser.add_argument("--output", default=str(Path.home() / ".local/state/x4d/work.json"))
    args = parser.parse_args(argv)
    try:
        config = validate_config(_read(args.config))
        with collection_lock(args.output) as locked:
            if not locked:
                print("Work collection already running")
                return 1
            try:
                previous = validate_snapshot(_read(args.output), config)
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                previous = None
            snapshot = collect(config, now=time.time(), previous=previous)
            write_snapshot(args.output, snapshot)
            return int(any(snapshot[s]["refresh_failed"] for s in ("prs", "build")))
    except (OSError, ValueError, TypeError, KeyError):
        print("Work config or snapshot unavailable")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
