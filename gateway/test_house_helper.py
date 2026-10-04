"""No-network narrow helper and subprocess boundary contract."""
import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_x4_house import config, state, NOW
from x4_house import collect, validate_config
from x4_store import Store


class Helper(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('x4_house_helper'), 'Narrow helper missing')
        self.mod = importlib.import_module('x4_house_helper')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = validate_config(config())
        self.path = Path(self.tmp.name) / 'house.json'
        self.path.write_text(json.dumps(self.cfg))
        self.ha = Mock()
        self.ha.source.return_value = 'fixture-ha'
        self.ha.fetch.side_effect = lambda e: state(e, 'off')
        self.ha.set_light.return_value = True
        self.item = dict(collect(self.cfg, self.ha.fetch, NOW)['rooms'][0]['items'][0], ha_source_key='fixture-ha')
        self.store = Store(str(Path(self.tmp.name) / 'journal'))

    def dispatch(self, req):
        return self.mod.dispatch(self.store, self.path, self.ha, req, NOW)

    def test_closed_schema_not_generic_services_or_shell(self):
        for req in ({'op': 'toggle'}, {'op': 'confirm', 'device': 'x4-01', 'id': 'x', 'data': {}},
                    {'op': 'preview', 'device': 'x4-01', 'room': 'Living Room', 'item': self.item, 'cmd': ['sh']},
                    {'op': 'confirm', 'device': 'x4-01', 'id': ['x']}):
            with self.assertRaises(ValueError): self.dispatch(req)
        self.ha.set_light.assert_not_called()

    def test_helper_independently_rereads_policy_not_gateway_copy(self):
        action = self.dispatch({'op': 'preview', 'device': 'x4-01', 'room': 'Living Room', 'item': self.item})
        self.path.unlink()
        receipt = self.dispatch({'op': 'confirm', 'device': 'x4-01', 'id': action['id']})
        self.assertEqual(receipt['outcome'], 'rejected')
        self.ha.set_light.assert_not_called()

    def test_native_adapter_only_explicit_single_light_services(self):
        get = Mock(return_value=state('light.lr', 'off'))
        service = Mock(return_value={'success': True})
        native = self.mod.NativeHA(lambda: ('http://fixture', 'not-real'), get, service)
        for entity, service_name in [('switch.washer', 'turn_off'), ('light.lr', 'toggle'),
                                     ('light.lr,light.other', 'turn_on'), ('all', 'turn_on')]:
            with self.assertRaises(ValueError): native.set_light(entity, service_name)
        native.set_light('light.lr', 'turn_on')
        service.assert_called_once_with('light', 'turn_on', 'light.lr')
        self.assertEqual(native.fetch('light.lr')['state'], 'off')
        self.assertNotIn('http', native.source())
        self.assertNotIn('not-real', native.source())

    def test_native_refuses_credentialless_read_or_write(self):
        get, service = Mock(), Mock()
        native = self.mod.NativeHA(lambda: ('', ''), get, service)
        with self.assertRaises(ValueError): native.fetch('light.lr')
        with self.assertRaises(ValueError): native.set_light('light.lr', 'turn_off')
        service.assert_not_called()
        get.assert_not_called()

    def test_fixed_client_argv_scrubs_secrets_and_handles_timeout_honestly(self):
        self.assertIsNotNone(importlib.util.find_spec('x4_house_client'))
        client_mod = importlib.import_module('x4_house_client')
        client = client_mod.Client({'python': '/fixture/python', 'hermes_root': '/fixture/hermes',
                                   'hermes_home': '/fixture/profile', 'policy': str(self.path),
                                   'journal': str(Path(self.tmp.name) / 'journal')})
        with patch('x4_house_client.subprocess.run') as run, patch.dict('os.environ', {'HASS_TOKEN': 'SECRET'}):
            run.return_value = Mock(returncode=0, stdout='{"outcome":"verified"}')
            self.assertEqual(client.confirm('x4-01', 'abc')['outcome'], 'verified')
            args, kw = run.call_args
            self.assertIn('x4_house_helper.py', args[0][1])
            self.assertNotIn('HASS_TOKEN', kw['env'])
            self.assertNotIn('SECRET', str(kw))
            self.assertFalse(kw.get('shell', False))
            import subprocess
            run.side_effect = subprocess.TimeoutExpired('helper', 45)
            self.assertEqual(client.confirm('x4-01', 'abc')['outcome'], 'uncertain')
            self.assertEqual(run.call_count, 2)  # one per explicit call; never an internal retry

    def test_real_helper_process_serializes_replay_and_receipt_without_network(self):
        import concurrent.futures
        import sys
        import time
        root = Path(self.tmp.name) / 'fake-hermes'
        (root / 'agent').mkdir(parents=True)
        (root / 'tools').mkdir()
        (root / 'agent' / '__init__.py').write_text('')
        (root / 'tools' / '__init__.py').write_text('')
        (root / 'agent' / 'secret_scope.py').write_text('def set_secret_scope(values): pass\n')
        (root / 'dotenv.py').write_text('def dotenv_values(path): return {}\n')
        calls = Path(self.tmp.name) / 'calls'
        native_source = (
            'import time\n'
            'from pathlib import Path\n'
            f'calls = Path({str(calls)!r})\n'
            f'raw = {state("light.lr", "off")!r}\n'
            'def _get_config(): return ("fixture-ha", "fake-test-token")\n'
            'def _run_async(value): return value\n'
            'def _async_get_state(entity):\n'
            '    return dict(raw, state="on" if calls.exists() else "off")\n'
            'def _async_call_service(domain, service, entity):\n'
            '    with calls.open("a") as out: out.write(domain+"."+service+" "+entity+"\\n")\n'
            '    time.sleep(0.5)\n'
            '    return {"success":True}\n'
        )
        (root / 'tools' / 'homeassistant_tool.py').write_text(native_source)
        client_mod = importlib.import_module('x4_house_client')
        client = client_mod.Client({'python': sys.executable, 'hermes_root': str(root),
                                   'hermes_home': str(root), 'policy': str(self.path),
                                   'journal': str(Path(self.tmp.name) / 'process.db')})
        from x4_house_actions import fingerprint
        action = client.preview('x4-01', 'Living Room', dict(self.item, ha_source_key=fingerprint('fixture-ha')), NOW)
        import fcntl
        with open(str(Path(self.tmp.name) / 'process.db') + '.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started = time.monotonic()
            with self.assertRaises(ValueError):
                client.preview('x4-01', 'Living Room', dict(self.item, ha_source_key=fingerprint('fixture-ha')), NOW)
            self.assertLess(time.monotonic() - started, 1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(client.confirm, 'x4-01', action['id']) for _ in range(2)]
            receipts = [f.result() for f in futures]
        self.assertIn('verified', [r['outcome'] for r in receipts])
        self.assertTrue(all(r['outcome'] in ('verified', 'uncertain') for r in receipts))
        self.assertEqual(calls.read_text(), 'light.turn_on light.lr\n')
        receipt = client.receipt('x4-01', action['id'])
        self.assertEqual(receipt['outcome'], 'verified')
        self.assertEqual(client.confirm('x4-01', action['id']), receipt)
        self.assertEqual(calls.read_text(), 'light.turn_on light.lr\n')

    def test_no_generic_command_or_unknown_config_keys(self):
        self.assertIsNotNone(importlib.util.find_spec('x4_house_client'))
        client_mod = importlib.import_module('x4_house_client')
        for cfg in ({'cmd': ['sh']}, {'python': 'python3'}, {'python': '/python', 'extra': 'x'}):
            with self.assertRaises(ValueError): client_mod.Client(cfg)
