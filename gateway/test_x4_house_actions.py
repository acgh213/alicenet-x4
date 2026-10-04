"""Offline safety tests: the adapter is mocked; no physical HA calls."""
import copy
import importlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_x4_house import config, state, NOW
from x4_house import collect, validate_config
from x4_store import Store


class Actions(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('x4_house_actions'), 'House executor missing')
        self.mod = importlib.import_module('x4_house_actions')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(Path(self.tmp.name) / 'db'))
        self.cfg = validate_config(config())
        self.ha = Mock()
        self.ha.source.return_value = 'fixture-ha'
        self.ha.fetch.side_effect = lambda e: state(e, 'off')
        self.ha.set_light.return_value = True
        self.load = lambda: copy.deepcopy(self.cfg)
        self.control = self.mod.Controls(self.store, self.load, self.ha)
        self.item = dict(collect(self.cfg, self.ha.fetch, NOW)['rooms'][0]['items'][0], ha_source_key='fixture-ha')

    def preview(self):
        return self.control.preview('x4-01', 'Living Room', self.item, NOW)

    def test_turn_off_is_explicit_and_verified_not_toggle(self):
        raw = state('light.lr', 'on')
        from x4_house import normalize
        item = dict(normalize(raw, 'Light'), protected=False, ha_source_key='fixture-ha')
        action = self.control.preview('x4-01', 'Living Room', item, NOW)
        self.ha.fetch.side_effect = [raw, state('light.lr', 'off')]
        self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 1)['outcome'], 'verified')
        self.ha.set_light.assert_called_once_with('light.lr', 'turn_off')

    def test_preview_requires_same_observation_source_as_executor(self):
        for source in (None, 'another-ha'):
            with self.assertRaises(ValueError):
                self.control.preview('x4-01', 'Living Room', dict(self.item, ha_source_key=source), NOW)
        self.ha.set_light.assert_not_called()

    def test_displayed_action_only_and_receipt_survives_restart(self):
        action = self.preview()
        self.assertEqual((action['entity'], action['observed']['state'], action['requested'], action['source']),
                         ('light.lr', 'off', 'on', 'Home Assistant'))
        self.ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        receipt = self.control.confirm('x4-01', action['id'], NOW + 1)
        self.assertEqual(receipt['outcome'], 'verified')
        self.ha.set_light.assert_called_once_with('light.lr', 'turn_on')
        restarted = self.mod.Controls(Store(self.store.path), self.load, self.ha)
        self.assertEqual(restarted.receipt('x4-01', action['id']), receipt)
        self.assertEqual(restarted.confirm('x4-01', action['id'], NOW + 2), receipt)
        self.ha.set_light.assert_called_once()
        with self.store._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM house_audit').fetchone()[0], 3)

    def test_allowlist_entity_label_room_and_protection_changes_reject(self):
        for change in ('removed', 'entity', 'label', 'room', 'protected'):
            with self.subTest(change=change):
                original = copy.deepcopy(self.cfg)
                action = self.preview()
                entry = self.cfg['rooms'][0]['entities'][0]
                if change == 'removed': self.cfg['rooms'][0]['entities'].pop(0)
                elif change == 'room': self.cfg['rooms'][0]['name'] = 'Other'
                elif change == 'protected': entry['protected'] = True
                else: entry[change] = 'light.other' if change == 'entity' else 'Other'
                self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 1)['outcome'], 'rejected')
                self.cfg = original
        self.ha.set_light.assert_not_called()

    def test_changed_state_revision_source_wrong_entity_and_unavailable_reject(self):
        for raw in (state('light.lr', 'on'), state('light.lr', 'unavailable'), state('light.lr', 'unknown'),
                    state('light.other', 'off'), dict(state('light.lr', 'off'), last_changed='2026-10-04T00:00:00Z'),
                    state('light.lr', 'off', brightness=25)):
            action = self.preview()
            self.ha.fetch.side_effect = None
            self.ha.fetch.return_value = raw
            self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 1)['outcome'], 'rejected')
            self.ha.fetch.side_effect = lambda e: state(e, 'off')
        action = self.preview()
        self.ha.source.return_value = 'another-ha'
        self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 1)['outcome'], 'rejected')
        self.ha.set_light.assert_not_called()

    def test_expired_missing_wrong_device_and_cancelled_never_execute(self):
        action = self.preview()
        self.assertEqual(self.control.confirm('other', action['id'], NOW)['outcome'], 'rejected')
        self.assertEqual(self.control.confirm('x4-01', 'missing', NOW)['outcome'], 'rejected')
        self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 121)['outcome'], 'rejected')
        action = self.preview()
        self.control.cancel('x4-01', action['id'], NOW + 1)
        self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 2)['outcome'], 'cancelled')
        self.ha.set_light.assert_not_called()

    def test_network_failure_stays_uncertain_no_retry_even_after_restart(self):
        action = self.preview()
        self.ha.set_light.side_effect = TimeoutError('SECRET must not leak')
        receipt = self.control.confirm('x4-01', action['id'], NOW + 1)
        self.assertEqual(receipt['outcome'], 'uncertain')
        self.assertNotIn('SECRET', json.dumps(receipt))
        restarted = self.mod.Controls(Store(self.store.path), self.load, self.ha)
        self.assertEqual(restarted.confirm('x4-01', action['id'], NOW + 2), receipt)
        self.ha.set_light.assert_called_once()

    def test_crash_after_claim_is_uncertain_and_never_retried(self):
        action = self.preview()
        self.ha.set_light.side_effect = KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt): self.control.confirm('x4-01', action['id'], NOW + 1)
        self.assertEqual(self.control.receipt('x4-01', action['id'])['outcome'], 'uncertain')
        self.control.confirm('x4-01', action['id'], NOW + 2)
        self.ha.set_light.assert_called_once()

    def test_successful_service_without_matching_readback_is_not_verified(self):
        action = self.preview()
        receipt = self.control.confirm('x4-01', action['id'], NOW + 1)
        self.assertEqual(receipt['outcome'], 'uncertain')
        self.ha.set_light.assert_called_once()

    def test_washer_switches_protected_invalid_states_and_spoofed_domains_refused(self):
        for item in (dict(self.item, entity='switch.washer'), dict(self.item, protected=True),
                     dict(self.item, available=False), dict(self.item, state='broken'),
                     dict(self.item, entity='light.other'), dict(self.item, domain='switch')):
            with self.assertRaises(ValueError): self.control.preview('x4-01', 'Living Room', item, NOW)
        self.ha.set_light.assert_not_called()

    def test_policy_rechecked_after_fresh_state_read(self):
        action = self.preview()
        def revoke(_):
            self.cfg['rooms'][0]['entities'][0]['protected'] = True
            return state('light.lr', 'off')
        self.ha.fetch.side_effect = revoke
        self.assertEqual(self.control.confirm('x4-01', action['id'], NOW + 1)['outcome'], 'rejected')
        self.ha.set_light.assert_not_called()
