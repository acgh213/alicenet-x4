"""Synthetic consented Life behavior, using the real menu and records store."""
import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from x4_glance import Glance
from x4_menu import Menu
from x4_records import Records, validate_record
from x4_store import Store
from test_x4_dashboard import NOW, TZ, fixture
import test_x4_dashboard as dash


def life_fixture():
    snapshot = fixture()
    snapshot['calendar'].update(source_key='fixture-calendar', approved_fields=['summary', 'start'])
    snapshot['calendar']['events'].insert(0, {'summary': 'FIXTURE current appointment',
        'start': '2026-10-03T10:00:00-04:00', 'end': '2026-10-03T10:45:00-04:00'})
    for event in snapshot['calendar']['events']:
        if 'summary' in event:
            event['_title_field'] = 'summary'
    return snapshot


class LifeMenu(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'glance.json'
        self.snapshot = life_fixture()
        self.write()
        self.store = Store(str(Path(self.tmp.name) / 'db'))
        self.records = Records(self.store)
        self.menu = Menu(Glance(self.store, self.path), self.records)

    def write(self):
        self.path.write_text(json.dumps(self.snapshot))

    def frame(self, now=NOW):
        return self.menu.frame('x4-01', now, TZ, 120)

    def texts(self, now=NOW):
        return '\n'.join(row['text'] for row in self.frame(now)['image'].info['layout'])

    def press(self, button, press='short', frame=None, now=NOW):
        frame = self.frame(now) if frame is None else frame
        return self.menu.handle('x4-01', {'button': button, 'press': press,
            'card': frame['card'], 'etag': frame['etag']}, now, TZ)

    def open_life(self):
        self.press('back')
        for _ in range(4):
            self.press('down')
        self.press('confirm')

    def put(self, ident='life.rest.reading', **kw):
        body = dict(id=ident, kind='report', agent='pyrrha', title='FIXTURE Rest',
                    summary='Reading is valid planned care.', notify='none')
        body.update(kw)
        stamp = NOW - 300 + getattr(self, 'put_count', 0)
        self.put_count = getattr(self, 'put_count', 0) + 1
        return self.records.put(validate_record(body), stamp)

    def test_life_now_distinguishes_current_and_next_without_forwarding(self):
        self.open_life()
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        text = self.texts()
        for expected in ('NOW', 'NEXT', 'current appointment', 'Long walk', 'Calendar', 'fresh'):
            self.assertIn(expected, text)
        self.assertNotIn('Reading hour', text)  # next, not every future appointment
        self.assertIsNone(self.press('confirm')['label'])
        self.assertIsNone(self.press('confirm', 'long')['label'])
        self.assertTrue(self.press('confirm', 'long')['refresh'])
        self.press('back')
        self.assertEqual(self.frame()['card'], 'menu')

    def test_day_pages_and_back_preserve_ambient_navigation(self):
        self.open_life()
        self.press('right')
        self.assertIn('Life · Day', self.texts())
        self.assertIn('Reading hour', self.texts())
        self.assertNotIn('Studio day', self.texts())
        self.press('right')
        self.assertIn('Life · Notes', self.texts())
        self.press('left')
        self.assertIn('Life · Day', self.texts())
        self.menu.wake('x4-01', 'timer')
        self.assertEqual(self.frame()['card'], 'glance.home')

    def test_unknown_and_start_only_do_not_claim_current_or_next(self):
        self.snapshot['calendar']['events'] = [
            {'summary': 'Summary only'},
            {'start': '2026-10-03T09:00:00-04:00'},
            {'summary': 'Naive time', 'start': '2026-10-03T11:00:00'},
            {'summary': 'Invalid interval', 'start': '2026-10-03T10:00:00-04:00',
             'end': '2026-10-03T09:00:00-04:00'}]
        self.write()
        self.open_life()
        text = self.texts()
        for expected in ('Time unknown', 'End unknown', 'Untitled event', 'Timing uncertain'):
            self.assertIn(expected, text)
        self.assertNotIn('NEXT ·', text)
        self.assertNotIn('NOW ·', text)
        self.assertNotIn('free', text.lower())

    def test_partial_consent_reprojects_even_failed_cached_refresh(self):
        self.snapshot['calendar'].update(approved_fields=['start'], refresh_failed=True)
        self.snapshot['calendar']['events'][0].update(description='PRIVATE description', location='PRIVATE place')
        self.write()
        self.open_life()
        text = self.texts()
        self.assertIn('Untitled event', text)
        self.assertIn('Refresh failed', text)
        self.assertNotIn('current appointment', text)
        self.assertNotIn('PRIVATE', text)
        self.snapshot['calendar']['approved_fields'] = ['summary']
        self.write()
        text = self.texts()
        self.assertIn('Time unknown', text)
        self.assertNotIn('10:00', text)
        self.assertNotIn('NOW ·', text)

    def test_unknown_provenance_unavailable_empty_stale_and_expired_are_distinct(self):
        self.open_life()
        self.snapshot['calendar'].pop('source_key')
        self.write()
        self.assertIn('Calendar unavailable', self.texts())
        self.assertNotIn('current appointment', self.texts())
        self.snapshot = life_fixture()
        self.snapshot['calendar']['events'] = []
        self.write()
        self.assertIn('No events in fetched window', self.texts())
        self.snapshot['calendar']['observed_at'] = NOW - 7200
        self.snapshot['calendar']['refresh_failed'] = True
        self.write()
        self.assertIn('STALE', self.texts())
        self.assertIn('Refresh failed', self.texts())
        self.assertIn('saved window', self.texts())
        self.snapshot['calendar']['window_end'] = '2026-10-03T10:00:00-04:00'
        self.snapshot['calendar']['events'] = life_fixture()['calendar']['events']
        self.write()
        self.assertIn('Saved window expired', self.texts())
        self.assertNotIn('current appointment', self.texts())

    def test_all_day_exclusive_end_expired_and_dst_offset_order(self):
        now = dt.datetime(2026, 11, 1, 1, 45, tzinfo=TZ, fold=0).timestamp()
        self.snapshot['calendar'].update(observed_at=now, window_end='2026-11-03T00:00:00-05:00', events=[
            {'summary': 'All day care', 'start': '2026-11-01', 'end': '2026-11-02'},
            {'summary': 'Expired', 'start': '2026-10-31', 'end': '2026-11-01'},
            {'summary': 'First hour', 'start': '2026-11-01T01:00:00-04:00', 'end': '2026-11-01T01:50:00-04:00'},
            {'summary': 'Second hour', 'start': '2026-11-01T01:15:00-05:00', 'end': '2026-11-01T01:50:00-05:00'}])
        for event in self.snapshot['calendar']['events']:
            event['_title_field'] = 'summary'
        self.write()
        self.open_life()
        text = self.texts(now)
        for expected in ('All day', 'First hour', 'Second hour', 'EDT', 'EST'):
            self.assertIn(expected, text)
        self.assertNotIn('Expired', text)
        self.assertIn('NEXT ·', text)

    def test_only_explicit_opt_in_report_ids_appear_and_visiting_does_not_ack(self):
        self.put()
        self.put('life.play.game', title='FIXTURE Play')
        self.put('life.reminder.errand', title='FIXTURE Reminder')
        self.put('pyrrha.private', title='DO NOT DISPLAY')
        self.put('life.rest.expired', title='EXPIRED', expires_at='2026-10-03T09:00:00-04:00')
        self.open_life()
        self.press('right'); self.press('right')
        text = self.texts()
        for expected in ('Rest', 'Play', 'Reminder', 'No automatic actions'):
            self.assertIn(expected, text)
        self.assertNotIn('DO NOT DISPLAY', text)
        self.assertNotIn('EXPIRED', text)
        self.press('confirm')
        self.assertIn('Reading is valid planned care.', self.texts())
        for kind in ('short', 'long'):
            self.assertIsNone(self.press('confirm', kind)['label'])
        self.assertEqual(self.records.get('life.rest.reading')['status'], 'unread')
        self.press('back')
        self.assertIn('Life · Notes', self.texts())

    def test_note_open_uses_shown_id_after_reorder_and_never_cursor_replacement(self):
        self.put()
        self.put('life.play.game', title='Other')
        self.open_life(); self.press('right'); self.press('right')
        shown = self.frame()
        self.records.ack('life.rest.reading', 1, NOW)
        self.press('confirm', frame=shown)
        self.assertEqual(self.menu.state('x4-01')['life_record'], 'life.rest.reading')
        self.press('back')
        self.press('up')
        shown = self.frame()
        ident = self.menu._shown('x4-01', {'card': shown['card'], 'etag': shown['etag']})['life_record']
        self.records.remove(ident)
        self.press('confirm', frame=shown)
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        self.assertIn('changed', self.texts())

    def test_unknown_cross_device_and_delayed_frames_never_open_or_brief(self):
        self.put()
        self.open_life(); self.press('right'); self.press('right')
        shown = self.frame()
        self.press('confirm', frame=dict(shown, etag='"unknown"'))
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        other = self.menu.frame('other-device', NOW, TZ, 120)
        self.press('confirm', frame=other)
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        self.menu.go_home('x4-01')
        result = self.press('confirm', frame=shown)
        self.assertIsNone(result['label'])
        self.assertEqual(self.frame()['card'], 'glance.home')

    def test_revised_or_expired_note_returns_list_not_rebound_content(self):
        self.put()
        self.open_life(); self.press('right'); self.press('right')
        shown = self.frame()
        self.put(summary='Changed')
        self.press('confirm', frame=shown)
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        self.assertIn('changed', self.texts())
        self.press('confirm')
        self.put(summary='Changed', expires_at='2026-10-03T10:31:00-04:00')
        self.assertEqual(self.frame(NOW + 120)['card'].split('.')[0], 'life')
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')

    def test_same_pixels_new_id_have_distinct_frame_identity(self):
        item = self.put(title='x' * 80, summary='a' * 600)
        self.open_life(); self.press('right'); self.press('right')
        first = self.frame()
        self.records.remove(item['id'])
        self.put_count = 0
        self.put('life.rest.replacement', title='x' * 80, summary='a' * 600)
        second = self.frame()
        self.assertEqual(first['pbm'], second['pbm'])
        self.assertNotEqual((first['card'], first['etag']), (second['card'], second['etag']))
        self.press('confirm', frame=first)
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')

    def test_malformed_calendar_metadata_fails_closed_without_crashing(self):
        self.open_life()
        for key, value in [('stale_after_s', 'bad'), ('observed_at', True),
                           ('events', {}), ('window_end', 123), ('approved_fields', ['start', {}])]:
            with self.subTest(key=key):
                self.snapshot = life_fixture()
                self.snapshot['calendar'][key] = value
                self.write()
                self.assertIn('Calendar unavailable', self.texts())

    def test_mixed_title_provenance_is_not_revealed_by_summary_consent(self):
        self.snapshot['calendar']['events'] = [{'summary': 'REVOKED HEALTH', '_title_field': 'health',
            'start': '2026-10-03T11:00:00-04:00'}]
        self.write()
        self.open_life()
        self.assertNotIn('REVOKED HEALTH', self.texts())
        self.assertIn('Untitled event', self.texts())

    def test_missing_title_provenance_fails_closed(self):
        self.snapshot['calendar'].update(approved_fields=['summary'], refresh_failed=True,
            events=[{'summary': 'UNPROVEN TITLE'}])
        self.write(); self.open_life()
        self.assertNotIn('UNPROVEN TITLE', self.texts())
        self.assertIn('hidden by consent', self.texts())

    def test_projection_hidden_is_not_fetched_empty(self):
        self.snapshot['calendar'].update(approved_fields=['summary'], events=[{'start': '2026-10-03'}])
        self.write(); self.open_life()
        self.assertIn('hidden by consent', self.texts())
        self.assertNotIn('No events', self.texts())

    def test_collector_hidden_count_does_not_become_empty(self):
        self.snapshot['calendar'].update(events=[], hidden_event_count=1, refresh_failed=True)
        self.write(); self.open_life()
        self.assertIn('hidden by consent', self.texts())
        self.assertNotIn('No events', self.texts())

    def test_date_and_numeric_overflow_fail_closed(self):
        self.open_life()
        for changes in ({'observed_at': -(10 ** 400)}, {'stale_after_s': 10 ** 400},
                        {'window_end': '9999-12-31T23:59:59-23:59'},
                        {'events': [{'start': '9999-12-31T23:59:59-23:59'}]}):
            self.snapshot = life_fixture()
            self.snapshot['calendar'].update(changes)
            self.write()
            self.assertTrue(self.frame()['pbm'].startswith(b'P4'))

    def test_deleted_recreated_same_id_cannot_rebind_displayed_note(self):
        self.put(); self.open_life(); self.press('right'); self.press('right')
        shown = self.frame()
        self.records.remove('life.rest.reading')
        self.put(summary='REPLACEMENT')
        self.press('confirm', frame=shown)
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        self.press('confirm')
        self.records.remove('life.rest.reading')
        self.put(summary='SECOND REPLACEMENT')
        self.frame()
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')

    def test_notes_down_uses_displayed_successor_after_live_reorder(self):
        self.put(); self.put('life.play.second', title='Second'); self.put('life.play.third', title='Third')
        self.open_life(); self.press('right'); self.press('right')
        shown = self.frame()
        self.records.ack('life.rest.reading', 1, NOW)
        self.press('down', frame=shown)
        context = self.menu._shown('x4-01', self.frame())
        self.assertEqual(context['life_record'], 'life.play.second')

    def test_all_displayed_note_identities_bound_even_with_identical_pixels(self):
        self.put(); self.put('life.play.second', title='Second')
        self.open_life(); self.press('right'); self.press('right')
        first = self.frame()
        self.records.remove('life.play.second')
        self.put_count = 1
        self.put('life.play.third', title='Second')
        second = self.frame()
        self.assertEqual(first['pbm'], second['pbm'])
        self.assertNotEqual(first['card'], second['card'])

    def test_null_card_is_safe_on_home(self):
        result = self.press('back', frame={'card': None, 'etag': None})
        self.assertTrue(result['moved'])

    def test_life_destination_confirm_binds_shown_selection(self):
        self.press('back')
        for _ in range(4): self.press('down')
        life = self.frame()
        self.press('down')  # host selection moved but delayed Life confirm arrives
        self.press('confirm', frame=life)
        self.assertEqual(self.menu.state('x4-01')['view'], 'menu')
        report = self.frame()
        self.press('up')
        self.press('confirm', frame=report)
        self.assertEqual(self.menu.state('x4-01')['view'], 'menu')

    def test_persisted_soon_life_migrates_to_read_only_now(self):
        self.menu._save('x4-01', dict(self.menu.state('x4-01'), view='soon.life'))
        self.frame()
        self.assertEqual(self.menu.state('x4-01')['view'], 'life')
        self.assertIn('Life · Now', self.texts())

    def test_expired_window_with_hidden_events_does_not_draw_uncertain_rows_over_message(self):
        self.snapshot['calendar'].update(events=[], hidden_event_count=1,
            window_end='2026-10-03T10:00:00-04:00')
        self.write(); self.open_life()
        image = self.frame()['image']
        dash.Dashboard.assert_layout(self, image)
        self.assertNotIn('hidden by consent', self.texts())
        self.assertIn('Saved window expired', self.texts())

    def test_notes_navigation_after_list_shrink_uses_clamped_shown_cursor(self):
        self.put(); self.put('life.play.second'); self.put('life.play.third')
        self.open_life(); self.press('right'); self.press('right'); self.press('down'); self.press('down')
        self.records.remove('life.play.third'); self.records.remove('life.play.second')
        self.press('down')
        self.assertEqual(self.menu._shown('x4-01', self.frame())['life_record'], 'life.rest.reading')

    def test_many_events_and_long_notes_paginate_and_layout_fits(self):
        self.snapshot['calendar']['events'] = [dict(summary='Long appointment ' * 12,
            start=f'2026-10-03T{11 + i:02}:00:00-04:00', end=f'2026-10-03T{12 + i:02}:00:00-04:00') for i in range(9)]
        self.write()
        self.put(title='Long rest title ' * 5, summary='Rest is valid. ' * 40,
            sections=[{'heading': 'END', 'body': 'END sentinel'}])
        self.open_life(); self.press('right')
        first = self.frame()
        self.press('down')
        self.assertNotEqual(first['etag'], self.frame()['etag'])
        dash.Dashboard.assert_layout(self, self.frame()['image'])
        self.press('right'); self.press('confirm')
        for _ in range(12):
            dash.Dashboard.assert_layout(self, self.frame()['image'])
            self.press('down')
        self.assertIn('END sentinel', self.texts())


if __name__ == '__main__':
    unittest.main()
