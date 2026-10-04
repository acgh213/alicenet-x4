"""Small test-only stand-in for the private clock-control feed module.

Production x4_sources.py prepends /home/cassie/services/clock-control and therefore
uses the exact deployed clock_feeds implementation. Public CI cannot clone Cassie's
private clock-control repository, so these tests exercise the X4 adapter contract
without copying private source or credentials.
"""
import copy
import datetime as dt
import json
import os
from urllib.parse import urljoin


class FeedError(Exception):
    pass


def validate_config(config):
    result = copy.deepcopy(config)
    if not isinstance(result.get("ha"), dict) or not result["ha"].get("calendar_entity"):
        raise ValueError("test config needs HA calendar entity")
    sources = []
    for source in result.get("sources", []):
        source = dict(source)
        source.setdefault("enabled", True)
        if source.get("calendar") and source["enabled"] and source.get("display_consent") is not True:
            raise ValueError("calendar display consent is required")
        source.setdefault("fields", ["text", "status", "at"])
        sources.append(source)
    result["sources"] = sources
    return result


def _ha_get(config, path, http_get):
    token = os.environ.get(config["ha"].get("token_env", ""), "")
    if not token:
        raise FeedError("Home Assistant token is unavailable")
    origin = config["ha"].get("origin", "")
    url = urljoin(origin.rstrip("/") + "/", path.lstrip("/"))
    return http_get(url, {"Authorization": "Bearer " + token}, config.get("timeout_s", 5), 256 * 1024)


def _weather_body(raw):
    if not isinstance(raw, dict) or raw.get("state") in (None, "unknown", "unavailable"):
        raise FeedError("weather state unavailable")
    return raw


def _utc_iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _ha_http_get(*_args):
    raise FeedError("Home Assistant source unavailable")


def load_config(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)
