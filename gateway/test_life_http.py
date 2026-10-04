"""Life over real device/agent HTTP: local reading, never sending or dismissing."""
import json
from pathlib import Path
from unittest.mock import patch

import x4d
from test_x4d import Server
from test_menu_life import life_fixture


class LifeHTTP(Server):
    def setUp(self):
        super().setUp()
        self.path = Path(self.dir.name) / 'snapshot.json'
        self.path.write_text(json.dumps(life_fixture()))
        self.app = x4d.App(dict(self.app.cfg, glance_snapshot=str(self.path), tz='America/New_York'))
        self.httpd.RequestHandlerClass.app = self.app
        self.seq = 0

    def frame(self, **headers):
        status, out, body = self.call('GET', '/x4/v1/frame', headers={'X-Wake': 'session', **headers})
        self.assertIn(status, (200, 304))
        if status == 200:
            self.assertTrue(body.startswith(b'P4\n800 480\n'))
            self.assertEqual(len(body), 48011)
        return out

    def press(self, button, kind='short', shown=None, seq=None):
        shown = self.frame() if shown is None else shown
        self.seq += 1
        event = {'seq': self.seq if seq is None else seq, 'card': shown['X-Card'],
                 'etag': shown['ETag'], 'button': button, 'press': kind, 'wake': 'button'}
        status, out, _ = self.call('POST', '/x4/v1/events',
            {'device': 'x4-01', 'boot': 1, 'events': [event]})
        self.assertEqual(status, 200)
        return out

    def open_life(self):
        self.press('back')
        for _ in range(4):
            self.press('down')
        self.press('confirm')

    def publish(self, ident='life.play.synthetic'):
        status, out = self.agent({'action': 'record_put', 'record': {
            'id': ident, 'kind': 'report', 'agent': 'pyrrha', 'title': 'FIXTURE Play',
            'summary': 'Rest and play are valid planned care.', 'notify': 'none'}})
        self.assertEqual(status, 200, out)

    def test_local_notes_flow_never_dismisses_or_queues_muse(self):
        self.publish()
        self.open_life()
        self.press('right'); self.press('right'); self.press('confirm')
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'life_note')
        self.press('confirm')
        with patch.object(self.app, 'refresh_sources', return_value=True) as refresh:
            self.press('confirm', 'long')
            refresh.assert_called_once()
        self.press('back'); self.press('back')
        self.assertEqual(self.frame()['X-Card'], 'menu')
        status, out = self.agent({'action': 'record_get', 'id': 'life.play.synthetic'})
        self.assertEqual(out['result']['status'], 'unread')
        self.assertEqual(self.app.store.pending_forwards(), [])
        self.assertEqual({e['forward'] for e in self.app.store.events(30)}, {'local'})

    def test_delayed_life_confirm_after_timer_wake_stays_local(self):
        self.open_life()
        shown = self.frame()
        self.assertEqual(self.frame(**{'X-Wake': 'timer'})['X-Card'], 'glance.home')
        with patch.object(self.app, 'forward_once') as forward:
            self.press('confirm', shown=shown)
        self.assertEqual(self.app.store.pending_forwards(), [])
        self.assertIsNone(self.app.store.events(1)[0]['label'])

    def test_foreign_glance_and_slide_labels_cannot_queue_muse_while_in_life(self):
        self.open_life()
        super().publish('fixture.slide', actions={'confirm': 'ack'})
        for card in ('glance.home', 'fixture.slide'):
            with patch.object(self.app, 'forward_once'):
                self.press('confirm', shown={'X-Card': card, 'ETag': '"unknown"'})
            self.assertEqual(self.app.store.pending_forwards(), [])
            self.assertIsNone(self.app.store.events(1)[0]['label'])

    def test_batch_entering_life_does_not_preassign_foreign_glance_brief(self):
        self.press('back')
        for _ in range(4):
            self.press('down')
        shown = self.frame()
        self.seq += 1
        events = [{'seq': self.seq, 'card': shown['X-Card'], 'etag': shown['ETag'],
                   'button': 'confirm', 'press': 'short', 'wake': 'button'},
                  {'seq': self.seq + 1, 'card': 'glance.home', 'etag': '"unknown"',
                   'button': 'confirm', 'press': 'short', 'wake': 'button'}]
        status, _, _ = self.call('POST', '/x4/v1/events', {'device': 'x4-01', 'boot': 1, 'events': events})
        self.assertEqual(status, 200)
        self.assertEqual(self.app.store.pending_forwards(), [])

    def test_identical_pixels_replacement_304_keeps_distinct_card_context(self):
        self.publish()
        self.open_life(); self.press('right'); self.press('right')
        first = self.frame()
        self.agent({'action': 'record_remove', 'id': 'life.play.synthetic'})
        self.publish('life.play.replaced')
        # Publication timestamps can vary; freeze the replacement to the exact saved timestamp.
        with self.app.store.lock, self.app.store._db() as db:
            db.execute('UPDATE records SET updated_at=created_at')
        second = self.frame(**{'If-None-Match': first['ETag']})
        self.assertNotEqual(first['X-Card'], second['X-Card'])
        self.press('confirm', shown=first)
        self.assertEqual(self.app.menu.state('x4-01')['view'], 'life')
        self.assertEqual(self.app.store.pending_forwards(), [])


if __name__ == '__main__':
    import unittest
    unittest.main()
