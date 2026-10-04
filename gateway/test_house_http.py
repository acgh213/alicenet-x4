"""Real device HTTP with in-process narrow helper and synthetic HA, never live mutations."""
import json
from pathlib import Path
from unittest.mock import Mock, patch

import x4d
import x4_house_helper
from x4_house import collect, validate_config
from x4_store import Store
from test_x4_house import config, state
from test_x4_dashboard import fixture
from test_x4d import Server


class HouseHTTP(Server):
    def setUp(self):
        super().setUp()
        root = Path(self.dir.name)
        cfg = validate_config(config())
        self.policy = root / 'house.json'
        self.policy.write_text(json.dumps(cfg))
        self.ha = Mock()
        self.ha.source.return_value = 'fixture'
        self.ha.fetch.side_effect = lambda e: state(e, 'off')
        self.ha.set_light.return_value = True
        import time
        snap = fixture()
        snap['house'] = dict(collect(cfg, self.ha.fetch, time.time()), source_key='fixture')
        path = root / 'snapshot.json'
        path.write_text(json.dumps(snap))
        journal = root / 'house.db'
        self.journal = Store(str(journal))
        self.app = x4d.App(dict(self.app.cfg, glance_snapshot=str(path), house_controls={
            'python': '/fixture/python', 'hermes_root': '/fixture/hermes', 'hermes_home': '/fixture/profile',
            'policy': str(self.policy), 'journal': str(journal)}))
        self.httpd.RequestHandlerClass.app = self.app
        self.boundary = patch('x4_house_client.Client._call', side_effect=self.dispatch)
        self.boundary.start()
        self.addCleanup(self.boundary.stop)
        self.seq = 0
        import threading
        self.executed = threading.Event()

    def dispatch(self, req):
        import time
        result = x4_house_helper.dispatch(self.journal, self.policy, self.ha, req, time.time())
        if req['op'] == 'confirm': self.executed.set()
        return result

    def await_receipt(self):
        import time
        self.assertTrue(self.executed.wait(2))
        deadline = time.monotonic() + 2
        while self.app.menu.state('x4-01')['house_receipt']['outcome'] == 'uncertain':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)

    def frame(self):
        status, headers, _ = self.call('GET', '/x4/v1/frame', headers={'X-Wake': 'session'})
        self.assertEqual(status, 200)
        return headers

    def batch(self, button, shown=None):
        shown = shown or self.frame()
        self.seq += 1
        return {'device': 'x4-01', 'boot': 1, 'events': [{'seq': self.seq, 'card': shown['X-Card'],
                'etag': shown['ETag'], 'button': button, 'press': 'short', 'wake': 'button'}]}

    def press(self, button):
        self.assertEqual(self.call('POST', '/x4/v1/events', self.batch(button))[0], 200)

    def preview(self):
        self.press('back')
        for _ in range(3): self.press('down')
        self.press('confirm')
        self.press('confirm')
        self.press('confirm')
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'house_preview')
        return self.frame()

    def test_slow_helper_does_not_exceed_firmware_http_ack_deadline(self):
        import threading
        frame = self.preview()
        entered, release = threading.Event(), threading.Event()
        def slow(entity, service):
            entered.set()
            release.wait(5)
            return True
        self.ha.set_light.side_effect = slow
        try:
            import time
            started = time.monotonic()
            self.assertEqual(self.call('POST', '/x4/v1/events', self.batch('confirm', frame))[0], 200)
            self.assertLess(time.monotonic() - started, 1)
            self.assertTrue(entered.wait(1))
            self.assertFalse(release.is_set())
            self.assertEqual(self.app.menu.state('x4-01')['house_receipt']['outcome'], 'uncertain')
        finally:
            release.set()

    def test_durable_duplicate_event_and_preview_replay_only_one_service(self):
        frame = self.preview()
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        batch = self.batch('confirm', frame)
        for _ in range(2): self.assertEqual(self.call('POST', '/x4/v1/events', batch)[0], 200)
        self.await_receipt()
        self.assertEqual(self.app.menu.state('x4-01')['house_receipt']['outcome'], 'verified')
        restarted = x4d.App(self.app.cfg)
        self.app = restarted
        self.httpd.RequestHandlerClass.app = restarted
        self.assertEqual(self.call('POST', '/x4/v1/events', batch)[0], 200)
        self.assertEqual(self.call('POST', '/x4/v1/events', self.batch('confirm', frame))[0], 200)
        self.ha.set_light.assert_called_once_with('light.lr', 'turn_on')
        self.assertEqual({e['forward'] for e in self.app.store.events(50)}, {'local'})
        self.assertEqual(self.app.menu.state('x4-01')['house_receipt']['outcome'], 'verified')

    def test_fresh_helper_protection_revocation_over_http(self):
        frame = self.preview()
        body = json.loads(self.policy.read_text())
        body['rooms'][0]['entities'][0]['protected'] = True
        self.policy.write_text(json.dumps(body))
        self.assertEqual(self.call('POST', '/x4/v1/events', self.batch('confirm', frame))[0], 200)
        self.await_receipt()
        self.ha.set_light.assert_not_called()
        self.assertEqual(self.app.menu.state('x4-01')['house_receipt']['outcome'], 'rejected')

    def test_delayed_house_frames_after_navigation_never_forward_or_refresh(self):
        shown = self.preview()
        for leave in ('timer', 'home'):
            if leave == 'timer':
                self.app.menu.wake('x4-01', 'timer')
            else:
                self.app.menu.go_home('x4-01')
            for card in (shown, {'X-Card': shown['X-Card'], 'ETag': '"evicted"'},
                         {'X-Card': 'house.room.foreign', 'ETag': '"foreign"'}):
                for kind in ('short', 'long'):
                    with self.subTest(leave=leave, card=card, kind=kind):
                        batch = self.batch('confirm', card)
                        batch['events'][0]['press'] = kind
                        with patch.object(self.app, 'forward_once') as forward, patch.object(self.app, 'refresh_sources') as refresh:
                            self.assertEqual(self.call('POST', '/x4/v1/events', batch)[0], 200)
                            forward.assert_not_called()
                            refresh.assert_not_called()
                        self.assertEqual(self.app.store.pending_forwards(), [])
                        self.assertIsNone(self.app.store.events(1)[0]['label'])
        self.ha.set_light.assert_not_called()

    def test_batch_leaving_house_cannot_reinterpret_old_confirm(self):
        shown = self.preview()
        # Receipt Back -> room Back -> House Back -> menu Back -> glance,
        # then delayed Confirm/hold from the old preview in the same request.
        self.app.menu._save('x4-01', dict(self.app.menu.state('x4-01'), view='house_receipt',
                            house_receipt={'outcome': 'uncertain'}))
        self.seq += 1
        events = []
        for offset, (button, kind) in enumerate([('back', 'short')] * 4 + [('confirm', 'short'), ('confirm', 'long')]):
            events.append({'seq': self.seq + offset, 'card': shown['X-Card'], 'etag': shown['ETag'],
                           'button': button, 'press': kind})
        with patch.object(self.app, 'forward_once') as forward, patch.object(self.app, 'refresh_sources') as refresh:
            self.assertEqual(self.call('POST', '/x4/v1/events', {'device': 'x4-01', 'boot': 1, 'events': events})[0], 200)
            forward.assert_not_called()
            refresh.assert_not_called()
        self.assertEqual(self.app.store.pending_forwards(), [])
        self.ha.set_light.assert_not_called()

    def test_missing_frame_acknowledged_without_service(self):
        self.preview()
        self.assertEqual(self.call('POST', '/x4/v1/events', self.batch('confirm',
                        {'X-Card': 'house.preview.invalid', 'ETag': '"absent"'}))[0], 200)
        self.ha.set_light.assert_not_called()
