#!/usr/bin/env python3
"""Glance snapshots using the EXACT configured clock feed sources and HA client.

Keeps weather metrics and the whole consented calendar window, not the Pi Zero's
flattened 220-character cards. No agent-inserted slides enter this data path.
"""
import argparse
import copy
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import quote, urlencode

CLOCK_ROOT = Path('/home/cassie/services/clock-control')
sys.path.insert(0, str(CLOCK_ROOT))
import clock_feeds as feeds


def epoch(value):
    if not isinstance(value, str):
        return None
    try:
        v = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return v.timestamp() if v.tzinfo else None
    except ValueError:
        return None


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def text(value, cap=160):
    return ' '.join(value.split())[:cap] if isinstance(value, str) else ''


def _weather(config, source, http_get, now):
    entity = config['ha'].get('weather_entity')
    if not entity:
        raise feeds.FeedError('weather entity is not configured')
    raw = feeds._ha_get(config, '/api/states/' + quote(entity, safe='.'), http_get)
    feeds._weather_body(raw)  # use the established available-state validation
    observed = epoch(raw.get('last_updated'))
    if observed is None:
        observed = now  # observation made by this successful bounded read
    if observed > now + 5:
        raise feeds.FeedError('future weather observation')
    a = raw.get('attributes') or {}
    out = {'available':True, 'observed_at':observed, 'checked_at':now,
           'stale_after_s':source['stale_after_s'], 'condition':text(raw['state'],40)}
    for k in ('temperature','humidity','wind_speed','pressure'):
        out[k] = number(a.get(k))
    for k in ('temperature_unit','wind_speed_unit','pressure_unit','attribution'):
        out[k] = text(a.get(k),80)
    return out


def _calendar(config, source, http_get, now):
    end = now + source.get('lookahead_s', source['stale_after_s'])
    window = {'start':feeds._utc_iso(now), 'end':feeds._utc_iso(end)}
    url = '/api/calendars/' + quote(config['ha']['calendar_entity'], safe='.') + '?' + urlencode(window)
    raw = feeds._ha_get(config,url,http_get)
    events = raw.get('events') if isinstance(raw,dict) else raw
    if not isinstance(events,list):
        raise feeds.FeedError('calendar unavailable')
    approved = []
    for e in events[:256]:
        if not isinstance(e,dict):
            continue
        row = {}
        if 'summary' in source['fields']:
            row['summary'] = text(e.get('summary'))
        elif 'text' in source['fields']:
            row['summary'] = text(e.get('text'))
        if 'start' in source['fields'] or 'at' in source['fields']:
            for k in ('start','end'):
                value = e.get(k)
                if isinstance(value,dict):
                    value = value.get('dateTime',value.get('date'))
                if isinstance(value,str) and len(value) <= 40:
                    row[k] = value
        if row.get('summary') and row.get('start'):
            approved.append(row)
    return {'available':True, 'observed_at':now, 'checked_at':now,
            'stale_after_s':source['stale_after_s'], 'window_start':window['start'],
            'window_end':window['end'], 'events':approved}


def collect(config, *, now=None, previous=None, http_get=None):
    config = feeds.validate_config(config)  # keeps existing consent/privacy gates
    now = time.time() if now is None else now
    if number(now) is None:
        raise ValueError('invalid collection time')
    http_get = http_get or feeds._ha_http_get
    result = {'observed_at':now, 'weather':{'available':False}, 'calendar':{'available':False}, 'extras':[]}
    for source in config['sources']:
        if not source['enabled']:
            continue
        key = 'weather' if source['kind'] == 'weather' else 'calendar' if source.get('calendar') else None
        if key is None:
            continue  # explicit glance sources only; never copy carousel notes
        try:
            result[key] = (_weather if key == 'weather' else _calendar)(config,source,http_get,now)
        except Exception:
            # No error bodies/paths/URLs/tokens. Old data stays OLD and marked failed.
            old = (previous or {}).get(key)
            if isinstance(old,dict) and old.get('available'):
                result[key] = copy.deepcopy(old)
            result[key]['refresh_failed'] = True
    return result


def write_snapshot(path, snapshot):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.x4-snapshot-',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as out:
            json.dump(snapshot,out,ensure_ascii=False)
            out.write('\n')
            out.flush(); os.fsync(out.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name): os.unlink(name)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='/home/cassie/.config/clock-noticeboard/feed-config.json')
    p.add_argument('--output',default='/home/cassie/.local/state/x4d/glance.json')
    args = p.parse_args(argv)
    # Same launcher credentials as clock_feed_runner; never copy to Pi or config.
    from dotenv import dotenv_values
    values = dotenv_values(Path.home()/'.hermes/.env')
    for key in ('HASS_URL','HASS_TOKEN'):
        if not os.environ.get(key) and values.get(key): os.environ[key] = values[key]
    previous = {}
    try:
        previous = json.loads(Path(args.output).read_text())
    except (OSError,ValueError): pass
    result = collect(feeds.load_config(args.config),previous=previous)
    write_snapshot(args.output,result)
    print(json.dumps({k:{'available':v.get('available',False), 'refresh_failed':v.get('refresh_failed',False),
                         'event_count':len(v.get('events',[]))} for k,v in result.items() if k in ('weather','calendar')}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
