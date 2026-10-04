#!/usr/bin/env python3
"""Fixed House helper: policy + journal are host-owned; stdin never carries services."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time

from x4_house_actions import Controls, fingerprint
from x4_store import Store


class NativeHA:
    """Only this credentialed adapter enters Hermes's existing HA integration."""
    def __init__(self, credentials, get_state, call_light):
        self.credentials, self.get_state, self.call_light = credentials, get_state, call_light

    def source(self):
        origin, token = self.credentials()
        if not origin or not token:
            raise ValueError('HA credentials unavailable')
        return fingerprint(origin.rstrip('/'))

    def fetch(self, entity):
        self.source()
        if not re.fullmatch(r'light\.[a-z0-9_]{1,80}', entity):
            raise ValueError('Only one light is permitted')
        return self.get_state(entity)

    def set_light(self, entity, service):
        self.source()
        if not re.fullmatch(r'light\.[a-z0-9_]{1,80}', entity) or service not in ('turn_on', 'turn_off'):
            raise ValueError('Only explicit single-light on/off is permitted')
        result = self.call_light('light', service, entity)
        return isinstance(result, dict) and result.get('success') is True


def dispatch(store, policy, ha, req, now):
    fields = {'preview': {'op', 'device', 'room', 'item'},
              'confirm': {'op', 'device', 'id'}, 'cancel': {'op', 'device', 'id'},
              'receipt': {'op', 'device', 'id'}}
    if type(req) is not dict or req.get('op') not in fields or set(req) != fields[req['op']]:
        raise ValueError('Invalid House operation')
    if not isinstance(req['device'], str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', req['device']):
        raise ValueError('Invalid device')
    if req['op'] != 'preview' and (not isinstance(req['id'], str) or not re.fullmatch(r'[a-z0-9]{1,48}', req['id'])):
        raise ValueError('Invalid action id')
    def load():
        with open(policy, encoding='utf-8') as source:
            return json.load(source)
    control = Controls(store, load, ha)
    if req['op'] == 'preview':
        if type(req['item']) is not dict or type(req['room']) is not str:
            raise ValueError('Invalid preview')
        return control.preview(req['device'], req['room'], req['item'], now)
    if req['op'] == 'receipt':
        return control.receipt(req['device'], req['id'])
    return getattr(control, req['op'])(req['device'], req['id'], now)


def native(root, home):
    # No credentials in x4d: only this process loads the explicitly selected private profile.
    sys.path.insert(0, str(root))
    from dotenv import dotenv_values
    from agent.secret_scope import set_secret_scope
    from tools import homeassistant_tool as ha
    values = dotenv_values(Path(home) / '.env')
    set_secret_scope({k: values.get(k) or '' for k in ('HASS_URL', 'HASS_TOKEN')})
    return NativeHA(ha._get_config, lambda e: ha._run_async(ha._async_get_state(e)),
                    lambda d, s, e: ha._run_async(ha._async_call_service(d, s, e)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('policy', 'journal', 'hermes-root', 'hermes-home'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        request = json.loads(sys.stdin.buffer.read(16385))
        if len(json.dumps(request)) > 16384:
            raise ValueError('request too large')
        # All helper processes share this lock, including cancel/receipt and restart.
        fd = os.open(args.journal + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = dispatch(Store(args.journal), args.policy, native(args.hermes_root, args.hermes_home),
                              request, time.time())
        print(json.dumps(result))
        return 0
    except Exception:
        print('{"error":"House helper unavailable"}')  # never stderr secrets or tracebacks
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
