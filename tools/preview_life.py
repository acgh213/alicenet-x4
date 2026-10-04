"""Synthetic Life previews and read transcripts. Never reads live configuration."""
import copy
import json
import sys
import tempfile
from pathlib import Path

from x4_glance import Glance
from x4_menu import Menu
from x4_records import Records, validate_record
from x4_store import Store
from test_menu_life import life_fixture
from test_x4_dashboard import NOW, TZ


def preview(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    transcripts = []
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'snapshot.json'
        snapshot = life_fixture()
        path.write_text(json.dumps(snapshot))
        store = Store(str(Path(tmp) / 'db'))
        records = Records(store)
        for index, (kind, title, body) in enumerate([
            ('rest', 'FIXTURE · Reading time', 'Rest is valid planned care. No work required first.'),
            ('play', 'FIXTURE · Game with friends', 'Play is part of life, not a reward.'),
            ('reminder', 'FIXTURE · Library return', 'Manual opt-in reminder. No action will be sent.')]):
            records.put(validate_record(dict(id='life.' + kind + '.fixture', kind='report', agent='pyrrha',
                title=title, summary=body, sections=[dict(heading='END', body='END sentinel · synthetic only')],
                expires_at='2026-10-04T23:00:00-04:00', notify='none')), NOW - 300 + index)
        menu = Menu(Glance(store, path), records)
        def frame():
            return menu.frame('x4-01', NOW, TZ, 120)
        def press(button):
            shown = frame()
            menu.handle('x4-01', dict(button=button, press='short', card=shown['card'], etag=shown['etag']), NOW, TZ)
        def shot(name):
            shown = frame()
            shown['image'].save(out / (name + '.png'))
            (out / (name + '.pbm')).write_bytes(shown['pbm'])
            transcripts.append(name + '\n' + '\n'.join(row['text'] for row in shown['image'].info['layout']))
        press('back')
        for _ in range(4): press('down')
        press('confirm'); shot('01-now')
        press('right'); shot('02-day')
        press('right'); shot('03-notes')
        press('confirm'); shot('04-rest')
        press('down'); shot('05-rest-end')
        press('back'); press('left'); press('left')
        snapshot['calendar'].update(approved_fields=['summary'], refresh_failed=True, observed_at=NOW - 7200)
        path.write_text(json.dumps(snapshot)); shot('06-summary-only-stale')
        snapshot['calendar'].update(approved_fields=['start'])
        path.write_text(json.dumps(snapshot)); shot('07-start-only')
        snapshot['calendar']['source_key'] = None
        path.write_text(json.dumps(snapshot)); shot('08-unavailable')
        snapshot = life_fixture(); snapshot['calendar']['events'] = []
        path.write_text(json.dumps(snapshot)); shot('09-empty')
        snapshot = life_fixture(); snapshot['calendar']['window_end'] = '2026-10-03T10:00:00-04:00'
        path.write_text(json.dumps(snapshot)); shot('10-expired-window')
        snapshot = life_fixture()
        snapshot['calendar']['events'] = [{'start': '2026-10-03T09:00:00-04:00'},
                                         {'summary': 'FIXTURE unknown', '_title_field': 'summary'}]
        path.write_text(json.dumps(snapshot)); shot('11-start-only-end-unknown')
        snapshot['calendar'].update(events=[], hidden_event_count=1, refresh_failed=True)
        path.write_text(json.dumps(snapshot)); shot('12-consent-hidden')
        import datetime as dt
        current = dt.datetime(2026, 11, 1, 1, 45, tzinfo=TZ, fold=0).timestamp()
        snapshot = life_fixture()
        snapshot['calendar'].update(observed_at=current, window_end='2026-11-03T00:00:00-05:00', events=[
            {'summary': 'FIXTURE all-day care', '_title_field': 'summary', 'start': '2026-11-01', 'end': '2026-11-02'},
            {'summary': 'FIXTURE first hour', '_title_field': 'summary', 'start': '2026-11-01T01:00:00-04:00', 'end': '2026-11-01T01:50:00-04:00'},
            {'summary': 'FIXTURE second hour', '_title_field': 'summary', 'start': '2026-11-01T01:15:00-05:00', 'end': '2026-11-01T01:50:00-05:00'}])
        path.write_text(json.dumps(snapshot))
        shown = menu.frame('x4-01', current, TZ, 120)
        name = '13-dst-all-day'
        shown['image'].save(out / (name + '.png'))
        (out / (name + '.pbm')).write_bytes(shown['pbm'])
        transcripts.append(name + '\n' + '\n'.join(row['text'] for row in shown['image'].info['layout']))
        assert records.get('life.rest.fixture')['status'] == 'unread'
        assert not store.pending_forwards()
    (out / 'transcriptions.txt').write_text('\n\n'.join(transcripts) + '\n')
    return len(transcripts)


if __name__ == '__main__':
    print('Synthetic previews:', preview(sys.argv[1]))
