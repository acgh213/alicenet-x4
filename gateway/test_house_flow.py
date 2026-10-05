"""Frame-bound light control flow, synthetic states and mocked HA only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import x4d
from x4_house import collect, validate_config
from test_x4_house import config, state
import test_x4_dashboard as dash
from test_x4_dashboard import fixture, NOW, TZ


class Flow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'snapshot.json'
        self.cfg = validate_config(config())
        self.ha = Mock()
        self.ha.source.return_value = 'fixture'
        self.ha.fetch.side_effect = lambda e: state(e, 'off')
        self.ha.set_light.return_value = True
        self.write()
        self.app = x4d.App({'db': str(Path(self.tmp.name) / 'db'), 'glance_snapshot': str(self.path)})
        import x4_house_actions
        self.control = x4_house_actions.Controls(self.app.store, lambda: self.cfg, self.ha, clock=lambda: NOW)
        self.app.menu.house_controls = self.control
        self.seq = 0

    def write(self, body=None):
        snap = fixture()
        snap['house'] = dict(body or collect(self.cfg, self.ha.fetch, NOW), source_key='fixture')
        self.path.write_text(json.dumps(snap))

    def frame(self, now=NOW):
        return self.app.menu.frame('x4-01', now, TZ, 120)

    def press(self, button, kind='short', frame=None, now=NOW):
        frame = frame or self.frame(now)
        self.seq += 1
        return self.app.menu.handle('x4-01', {'seq': self.seq, 'card': frame['card'], 'etag': frame['etag'],
                                             'button': button, 'press': kind}, now, TZ)

    def open_room(self):
        self.press('back')
        for _ in range(3): self.press('down')
        self.press('confirm')
        self.press('confirm')

    def preview(self):
        self.open_room()
        self.press('confirm')
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'house_preview')
        return self.frame()

    def texts(self, frame=None):
        return '\n'.join(i['text'] for i in (frame or self.frame())['image'].info['layout'])

    def _preview_during_navigation(self, navigate):
        self.open_room()
        frame = self.frame()
        original = self.control.preview
        actions = []
        def delayed(*args):
            action = original(*args)
            actions.append(action)
            navigate(frame)
            return action
        self.control.preview = delayed
        self.press('confirm', frame=frame)
        self.ha.set_light.assert_not_called()
        return actions[0]

    def test_delayed_preview_does_not_reopen_after_back(self):
        action = self._preview_during_navigation(lambda frame: self.press('back', frame=frame))
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'house')
        self.assertEqual(self.control.receipt('x4-01', action['id'])['outcome'], 'cancelled')

    def test_delayed_preview_does_not_reopen_after_timer_wake(self):
        action = self._preview_during_navigation(lambda frame: self.app.menu.wake('x4-01', 'timer'))
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'glance')
        self.assertEqual(self.control.receipt('x4-01', action['id'])['outcome'], 'cancelled')

    def test_delayed_preview_does_not_reopen_after_home(self):
        action = self._preview_during_navigation(lambda frame: self.app.menu.go_home('x4-01'))
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'glance')
        self.assertEqual(self.control.receipt('x4-01', action['id'])['outcome'], 'cancelled')

    def test_delayed_preview_does_not_restore_after_return_to_same_room(self):
        def leave_and_return(frame):
            self.press('back', frame=frame)
            self.press('confirm')
        action = self._preview_during_navigation(leave_and_return)
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'room')
        self.assertEqual(self.control.receipt('x4-01', action['id'])['outcome'], 'cancelled')

    def test_preview_displays_target_state_request_source_revision_then_verified_receipt(self):
        frame = self.preview()
        for text in ('light.lr', 'Observed: off', 'Requested: on', 'Home Assistant', 'Revision:', 'Back: cancel'):
            self.assertIn(text, self.texts(frame))
        dash.Dashboard.assert_layout(self, frame['image'])
        self.ha.set_light.assert_not_called()
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        self.press('confirm', frame=frame)
        self.assertIn('Verified', self.texts())
        self.ha.set_light.assert_called_once_with('light.lr', 'turn_on')
        dash.Dashboard.assert_layout(self, self.frame()['image'])

    def test_back_cancels_old_frame_and_later_preview_does_not_reuse_it(self):
        old = self.preview()
        self.press('back')
        self.press('confirm')
        new = self.frame()
        self.assertNotEqual(old['card'], new['card'])
        self.press('confirm', frame=old)
        self.ha.set_light.assert_not_called()
        self.assertIn('Review', self.texts())

    def test_missing_frame_wrong_card_and_long_confirm_never_execute(self):
        frame = self.preview()
        for shown, kind in [(dict(frame, etag='"missing"'), 'short'), (dict(frame, card='house'), 'short'),
                            (frame, 'long')]:
            self.press('confirm', kind, frame=shown)
        self.ha.set_light.assert_not_called()

    def test_expired_preview_refuses_even_when_pixels_unchanged(self):
        frame = self.preview()
        self.press('confirm', frame=frame, now=NOW + 121)
        self.assertIn('Rejected', self.texts())
        self.ha.set_light.assert_not_called()

    def test_room_selection_binds_displayed_entity_not_reordered_list(self):
        self.open_room()
        old = self.frame()
        body = collect(self.cfg, self.ha.fetch, NOW)
        body['rooms'][0]['items'].reverse()
        self.write(body)
        self.press('confirm', frame=old)
        self.assertIn('light.lr', self.texts())
        self.ha.set_light.assert_not_called()

    def test_room_state_changes_since_render_cannot_create_new_action(self):
        self.open_room()
        old = self.frame()
        body = collect(self.cfg, self.ha.fetch, NOW)
        body['rooms'][0]['items'][0]['state'] = 'on'
        self.write(body)
        self.press('confirm', frame=old)
        self.assertNotEqual(self.app.menu.state('x4-01')['view'], 'house_preview')
        self.ha.set_light.assert_not_called()

    def test_nonlights_protected_unavailable_and_stale_are_read_only(self):
        self.open_room()
        body = collect(self.cfg, self.ha.fetch, NOW)
        for change in ({'protected': True}, {'available': False}, {'entity': 'switch.washer'}, {'state': 'bad'}):
            changed = copy.deepcopy(body)
            changed['rooms'][0]['items'][0].update(change)
            self.write(changed)
            self.press('confirm')
            self.assertEqual(self.app.menu.state('x4-01')['view'], 'room')
        self.write(dict(body, observed_at=NOW - 1801))
        self.press('confirm')
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'room')
        self.ha.set_light.assert_not_called()

    def test_uncertain_receipt_persists_in_gateway_after_restart_and_not_forwarded(self):
        frame = self.preview()
        self.ha.set_light.side_effect = TimeoutError
        result = self.press('confirm', frame=frame)
        self.assertIsNone(result['label'])
        restarted = x4d.App(self.app.cfg)
        restarted.menu.house_controls = self.control
        text = '\n'.join(i['text'] for i in restarted.menu.frame('x4-01', NOW, TZ, 120)['image'].info['layout'])
        self.assertIn('Uncertain', text)
        self.assertIn('No retry', text)
        self.ha.set_light.assert_called_once()

    def test_source_switch_cannot_rebind_displayed_observation(self):
        self.open_room()
        old = self.frame()
        snap = json.loads(self.path.read_text())
        snap['house']['source_key'] = 'different-ha'
        self.path.write_text(json.dumps(snap))
        self.ha.source.return_value = 'different-ha'
        self.press('confirm', frame=old)
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'room')
        self.ha.set_light.assert_not_called()

    def test_same_pixels_different_revision_get_distinct_frame_identity(self):
        self.open_room()
        old = self.frame()
        self.ha.fetch.side_effect = lambda e: dict(state(e, 'off'), last_updated='2026-10-04T00:00:00Z')
        self.write()
        new = self.frame()
        self.assertNotEqual((old['card'], old['etag']), (new['card'], new['etag']))
        self.press('confirm', frame=new)
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'house_preview')
        self.ha.set_light.assert_not_called()

    def test_identical_labels_reordered_entities_get_distinct_frame_identity(self):
        entries = self.cfg['rooms'][0]['entities']
        entries[1] = dict(entries[0], entity='light.other')
        self.write()
        self.open_room()
        old = self.frame()
        body = collect(self.cfg, self.ha.fetch, NOW)
        body['rooms'][0]['items'].reverse()
        self.write(body)
        new = self.frame()
        self.assertNotEqual((old['card'], old['etag']), (new['card'], new['etag']))
        self.press('confirm', frame=new)
        self.assertIn('light.other', self.texts())

    def test_verified_helper_receipt_recovered_after_lost_gateway_response(self):
        frame = self.preview()
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        confirm = self.control.confirm
        def crash(*args):
            confirm(*args)
            raise KeyboardInterrupt
        from unittest.mock import patch
        with patch.object(self.control, 'confirm', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt): self.press('confirm', frame=frame)
        self.assertEqual(self.app.menu.state('x4-01')['house_receipt']['outcome'], 'uncertain')
        restarted = x4d.App(self.app.cfg)
        restarted.menu.house_controls = self.control
        recovered = restarted.menu.frame('x4-01', NOW, TZ, 120)
        self.assertIn('Verified', '\n'.join(i['text'] for i in recovered['image'].info['layout']))
        self.ha.set_light.assert_called_once()

    def test_async_completion_does_not_read_then_replace_navigation_state(self):
        frame = self.preview()
        self.seq += 1
        ev = {'seq': self.seq, 'card': frame['card'], 'etag': frame['etag'], 'button': 'confirm', 'press': 'short'}
        result = self.app.menu.handle('x4-01', ev, NOW, TZ, defer_house=True)
        self.press('back')
        self.press('confirm')
        newer = self.app.menu.state('x4-01')['house_action']['id']
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        from unittest.mock import patch
        with patch.object(self.app.menu, 'state', side_effect=AssertionError('worker must update conditionally in SQL')):
            result['house_execute']()
        self.assertEqual(self.app.menu.state('x4-01')['house_action']['id'], newer)
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'house_preview')

    def test_concurrent_confirm_claims_preview_once(self):
        import concurrent.futures
        import threading
        from unittest.mock import patch
        frame = self.preview()
        menu = self.app.menu
        read = menu.state
        barrier = threading.Barrier(2)
        def simultaneous(device):
            result = read(device)
            barrier.wait(timeout=2)
            return result
        ev = {'card': frame['card'], 'etag': frame['etag'], 'button': 'confirm', 'press': 'short'}
        with patch.object(menu, 'state', side_effect=simultaneous):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(menu.handle, 'x4-01', dict(ev, seq=n), NOW, TZ, True) for n in (1, 2)]
                results = [f.result() for f in futures]
        executors = [r['house_execute'] for r in results if r.get('house_execute')]
        self.assertEqual(len(executors), 1)
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        executors[0]()
        self.assertEqual(read('x4-01')['house_receipt']['outcome'], 'verified')
        self.ha.set_light.assert_called_once()

    def test_late_uncertain_or_conflicting_terminal_cannot_regress_verified(self):
        frame = self.preview()
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        self.press('confirm', frame=frame)
        menu = self.app.menu
        saved = menu.state('x4-01')
        action = saved['house_action']
        for outcome in ('uncertain', 'rejected', 'cancelled'):
            menu._update_house_receipt('x4-01', action, {'id': action['id'], 'outcome': outcome, 'at': NOW + 5})
            self.assertEqual(menu.state('x4-01')['house_receipt'], saved['house_receipt'])

    def test_default_app_has_no_executor(self):
        default = x4d.App(dict(self.app.cfg, db=str(Path(self.tmp.name) / 'default-db')))
        self.assertIsNone(default.menu.house_controls)
