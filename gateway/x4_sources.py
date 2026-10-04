#!/usr/bin/env python3
"""Glance snapshots using the EXACT configured clock feed sources and HA client.

Keeps weather metrics and the whole consented calendar window, not the Pi Zero's
flattened 220-character cards. No agent-inserted slides enter this data path.
"""
import argparse
import copy
import datetime as dt
import json
import hashlib
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
import x4_house
from x4_calendar import project_events


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


def source_key(config, source, key):
    ha=config['ha']
    identity=[source['id'],ha.get('origin') or os.environ.get(ha.get('url_env',''),''),
              ha.get(key+'_entity'),source.get('lookahead_s')]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _calendar(config, source, http_get, now):
    end = now + source.get('lookahead_s', source['stale_after_s'])
    window = {'start':feeds._utc_iso(now), 'end':feeds._utc_iso(end)}
    url = '/api/calendars/' + quote(config['ha']['calendar_entity'], safe='.') + '?' + urlencode(window)
    raw = feeds._ha_get(config,url,http_get)
    events = raw.get('events') if isinstance(raw,dict) else raw
    if not isinstance(events,list):
        raise feeds.FeedError('calendar unavailable')
    approved = project_events(events,source['fields'])
    return {'available':True, 'observed_at':now, 'checked_at':now,
            'stale_after_s':source['stale_after_s'], 'window_start':window['start'],
            'window_end':window['end'], 'events':approved, 'approved_fields':source['fields'],
            'hidden_event_count': max(0, len(events) - len(approved))}


def _house(config, house, http_get, now, previous):
    """Allowlisted HA entities through the same HA client. Old data stays old and marked."""
    identity = hashlib.sha256(json.dumps(house, sort_keys=True).encode()).hexdigest()
    try:
        fetch = lambda entity: feeds._ha_get(config, '/api/states/' + quote(entity, safe='.'), http_get)
        out = x4_house.collect(house, fetch, now)
        out['config_key'] = identity
        from x4_house_actions import fingerprint
        ha = config['ha']
        out['source_key'] = fingerprint((ha.get('origin') or os.environ.get(ha.get('url_env', ''), '')).rstrip('/'))
        return out
    except Exception:
        old = (previous or {}).get('house')
        if isinstance(old, dict) and old.get('available') and old.get('config_key') == identity:
            return dict(copy.deepcopy(old), refresh_failed=True)
        return {'available': False, 'refresh_failed': True}


def collect(config, *, now=None, previous=None, http_get=None, house=None):
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
        identity=source_key(config,source,key)
        try:
            result[key] = (_weather if key == 'weather' else _calendar)(config,source,http_get,now)
            result[key]['source_key']=identity
        except Exception:
            # No error bodies/paths/URLs/tokens. Old data stays OLD and marked failed.
            old = (previous or {}).get(key)
            if isinstance(old,dict) and old.get('available') and old.get('source_key')==identity:
                result[key] = copy.deepcopy(old)
                if key=='calendar':
                    result[key]['events']=project_events(old.get('events',[]),source['fields'],
                        retained=True,previous_fields=old.get('approved_fields',[]))
                    result[key]['hidden_event_count']=old.get('hidden_event_count',0) + len(old.get('events',[])) - len(result[key]['events'])
                    result[key]['approved_fields']=source['fields']
            result[key]['refresh_failed'] = True
    if house is not None:
        result['house'] = _house(config, x4_house.validate_config(house), http_get, now, previous)
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
    p.add_argument('--house',default='/home/cassie/.config/x4d/house.json',
                   help='Allowlisted HA entities for the House page; skipped when the file is absent.')
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
    house = json.loads(Path(args.house).read_text()) if Path(args.house).is_file() else None
    result = collect(feeds.load_config(args.config),previous=previous,house=house)
    write_snapshot(args.output,result)
    print(json.dumps({k:{'available':v.get('available',False), 'refresh_failed':v.get('refresh_failed',False),
                         'event_count':len(v.get('events',[]))} for k,v in result.items()
                      if k in ('weather','calendar','house')}))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
