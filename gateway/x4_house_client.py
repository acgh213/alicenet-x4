"""Uncredentialed gateway boundary: fixed helper argv, closed JSON request, no retry."""
import json
import os
from pathlib import Path
import subprocess


class Client:
    def __init__(self, config):
        keys = {'python', 'hermes_root', 'hermes_home', 'policy', 'journal'}
        if (type(config) is not dict or set(config) != keys
                or any(type(v) is not str or not Path(v).is_absolute() for v in config.values())):
            raise ValueError('House control config requires explicit absolute helper paths')
        self.argv = [config['python'], str(Path(__file__).with_name('x4_house_helper.py'))]
        for key in ('policy', 'journal', 'hermes_root', 'hermes_home'):
            self.argv += ['--' + key.replace('_', '-'), config[key]]

    def _call(self, req):
        # Do not pass gateway tokens or inherited source credentials into the helper.
        env = {k: os.environ[k] for k in ('HOME', 'PATH', 'LANG', 'TMPDIR') if k in os.environ}
        try:
            result = subprocess.run(self.argv, input=json.dumps(req), text=True, capture_output=True,
                                    timeout=45, env=env)
            if result.returncode != 0:
                raise ValueError('helper failed')
            return json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            if req['op'] == 'confirm':
                return {'id': req['id'], 'outcome': 'uncertain'}
            raise ValueError('House helper unavailable') from None

    def preview(self, device, room, item, now):
        return self._call({'op': 'preview', 'device': device, 'room': room, 'item': item})

    def confirm(self, device, ident, now=None):
        return self._call({'op': 'confirm', 'device': device, 'id': ident})

    def cancel(self, device, ident, now=None):
        return self._call({'op': 'cancel', 'device': device, 'id': ident})

    def receipt(self, device, ident):
        return self._call({'op': 'receipt', 'device': device, 'id': ident})
