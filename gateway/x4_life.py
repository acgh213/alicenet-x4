"""Read-only Life: the existing consented calendar and explicitly published reports.

No fetches, travel estimates, routines, acknowledgments or Muse actions. Times are
classified at draw time, not a ticking clock. Snapshot metadata is the collector's
consent boundary; unknown provenance fails closed. All-day end dates are exclusive.
"""
import datetime as dt
import hashlib
import json
import math

from x4_dashboard import _age, _clock, _date, _state, _text

PARTS = ('now', 'day', 'notes')
PAGE_SIZE = 4
PREFIXES = {'life.reminder.': 'Reminder', 'life.rest.': 'Rest', 'life.play.': 'Play'}


def notes(records, now):
    """Only an explicit Life report ID opts in; ordinary reports/decisions stay out."""
    return [item for item in records.list(now, kind='report')
            if any(item['id'].startswith(prefix) and len(item['id']) > len(prefix) for prefix in PREFIXES)]


def category(item):
    return next(label for prefix, label in PREFIXES.items() if item['id'].startswith(prefix))


def _safe_date(value, tz):
    try:
        return _date(value, tz)
    except (OverflowError, OSError):
        return None


def note_key(item):
    """Content/publication fingerprint survives resettable per-ID revision counters."""
    return hashlib.sha256(json.dumps([item['id'], item['revision'], item['updated_at'], item['record']],
                                    sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def calendar(snapshot, now, tz):
    """Project every draw through the snapshot's current approved fields."""
    source = snapshot.get('calendar')
    if (not isinstance(source, dict) or source.get('available') is not True
            or not isinstance(source.get('source_key'), str) or not source['source_key']
            or not isinstance(source.get('approved_fields'), list)
            or not all(isinstance(f, str) for f in source['approved_fields'])
            or not isinstance(source.get('events'), list)
            or type(source.get('observed_at')) not in (int, float)
            or not 0 <= source['observed_at'] <= now or not math.isfinite(source['observed_at'])
            or type(source.get('stale_after_s')) not in (int, float)
            or not 0 < source['stale_after_s'] <= 604800 or not math.isfinite(source['stale_after_s'])
            or not isinstance(source.get('window_end'), str)):
        return {'available': False}, [], 'Calendar unavailable'
    # Reuse the collector's retained-field projection, including title provenance.
    from x4_calendar import project_events
    raw = [event for event in source['events'][:256] if isinstance(event, dict)]
    # A title with no originating field is not evidence of summary consent.
    proven = [dict(event, _title_field=event.get('_title_field', 'unproven')) for event in raw]
    projected = project_events(proven, source['approved_fields'], retained=True,
                               previous_fields=source['approved_fields'])
    hidden = source.get('hidden_event_count', 0)
    if type(hidden) is not int or not 0 <= hidden <= 100000:
        return {'available': False}, [], 'Calendar unavailable'
    source = dict(source, events=projected, hidden_count=hidden + len(source['events']) - len(projected))
    window_end = _safe_date(source.get('window_end'), tz)
    if window_end is None:
        return source, [], 'Saved window unknown'
    if window_end.timestamp() <= now:
        return source, [], 'Saved window expired'
    rows = []
    today = dt.datetime.fromtimestamp(now, tz).date()
    for event in projected:
        start, end = _safe_date(event.get('start'), tz), _safe_date(event.get('end'), tz)
        invalid = bool(start and end and end.timestamp() <= start.timestamp())
        if end and not invalid and end.timestamp() <= now:
            continue
        all_day = start is not None and len(event.get('start', '')) == 10
        if start is None:
            kind, when = 'unknown', 'Time unknown'
        elif invalid:
            kind, when = 'unknown', 'Timing uncertain'
        elif all_day:
            kind = 'all_day'
            when = 'All day · ' + start.strftime('%a %b %d')
            if not end:
                when += ' · End unknown'
        elif start.timestamp() <= now:
            kind = 'current' if end else 'unknown'
            when = ('NOW · ' if end else 'End unknown · started ') + _when(start)
            if end:
                when += ' → ' + _when(end)
        else:
            kind, when = 'next', 'NEXT · ' + _when(start)
            if not end:
                when += ' · End unknown'
        day = (start is None or invalid or start.date() == today or
               (start.date() < today and (end is None or end.timestamp() > now)))
        rows.append({'title': event.get('summary') or 'Untitled event', 'when': when,
                     'kind': kind, 'day': day, 'start': start.timestamp() if start else None})
    rows.sort(key=lambda row: row['start'] if row['start'] is not None else float('inf'))
    return source, rows, None


def _when(stamp):
    return stamp.strftime('%a %b %d') + ' · ' + _clock(stamp) + ' ' + str(stamp.tzname())


def calendar_rows(snapshot, part, now, tz):
    source, rows, message = calendar(snapshot, now, tz)
    if part == 'day':
        rows = [row for row in rows if row['day']]
    else:
        # All overlapping current appointments; only the earliest future start(s).
        future = [row for row in rows if row['kind'] == 'next']
        first = future[0]['start'] if future else None
        rows = ([row for row in rows if row['kind'] == 'current'] +
                [row for row in future if row['start'] == first] +
                [row for row in rows if row['kind'] == 'all_day' and row['day']] +
                [row for row in rows if row['kind'] == 'unknown'])
    if source.get('hidden_count') and message is None:
        rows.append({'title': 'Some events hidden by consent or missing provenance',
                     'when': 'Timing unknown · not evidence of an empty calendar',
                     'kind': 'unknown', 'day': True, 'start': None})
    if not rows and message is None:
        message = ('No events in fetched window' if not source['events'] and _state(source, now) == 'fresh'
                   and not source.get('refresh_failed') else
                   'No events in saved window' if not source['events'] else
                   'No remaining timed events today' if part == 'day' else 'No now/next in saved window')
    return source, rows, message


def freshness(source, now):
    if not source.get('available'):
        return 'Calendar unavailable · consented snapshot only'
    state = _state(source, now)
    return ('Calendar · ' + ('Refresh failed · ' if source.get('refresh_failed') else '') +
            state + ' · observed ' + _age(source.get('observed_at'), now) + ' ago')


def _identity(extra):
    return 'life.' + hashlib.sha256(json.dumps(extra, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def render_life(menu, state, snapshot, items, now, tz):
    """One supplied observation covers state settling, rendering, and frame context."""
    part = state.get('life_part', 'now')
    item = next((i for i in items if i['id'] == state.get('life_record')), None)
    if state['view'] == 'life_note':
        return _render_note(menu, state, item, now, tz)
    image = menu._canvas('Life · ' + part.capitalize(), 'Destinations › Life · read-only', tz, now)
    stamp = dt.datetime.fromtimestamp(now, tz)
    _text(image, 'Drawn ' + _clock(stamp) + ' ' + str(stamp.tzname()) + ' · not live · ' +
          ('Manual opt-in notes' if part == 'notes' else 'Consented calendar snapshot'),
          (24, 124, 776, 146), 15)
    extra = {'life_part': part, 'page': state['page'], 'cursor': state['cursor']}
    if part == 'notes':
        cursor = min(state['cursor'], max(0, len(items) - 1))
        start = (cursor // PAGE_SIZE) * PAGE_SIZE
        shown = items[start:start + PAGE_SIZE]
        for index, note in enumerate(shown):
            y = 158 + index * 58
            mark = start + index == cursor
            _text(image, ('▶ ' if mark else '') + note['record']['title'], (24, y, 505, y + 26), 20, mark)
            _text(image, category(note) + ' · ' + note['agent'].capitalize(), (520, y, 776, y + 23), 16, mark)
            _text(image, 'Published ' + _age(note['updated_at'], now) + ' ago · rev ' + str(note['revision']),
                  (24, y + 29, 776, y + 49), 14)
        extra['cursor'] = cursor
        extra['life_order'] = [{'id': i['id'], 'key': note_key(i)} for i in items]
        extra['life_visible'] = extra['life_order'][start:start + PAGE_SIZE]
        if items:
            extra.update(life_record=items[cursor]['id'], life_revision=items[cursor]['revision'],
                         life_key=note_key(items[cursor]))
            _text(image, f'{start + 1}–{min(start + PAGE_SIZE, len(items))} of {len(items)}', (590, 395, 776, 413), 13)
        else:
            _text(image, 'No opt-in notes published', (24, 175, 776, 212), 24, True)
            _text(image, 'Rest and play are valid planned care.', (24, 235, 776, 260), 19)
        menu._footer(image, '◀ ▶ views · ▲ ▼ choose · Confirm: read only · hold: refresh · Back',
                     state['notice'] or 'No automatic actions · nothing dismissed by visiting')
    else:
        source, rows, message = calendar_rows(snapshot, part, now, tz)
        page = min(state['page'], max(0, (len(rows) - 1) // PAGE_SIZE))
        shown = rows[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        if message:
            _text(image, message, (24, 175, 776, 212), 24, True)
            _text(image, 'Unknown does not mean free. No departure estimates.', (24, 238, 776, 264), 18)
        for index, row in enumerate(shown):
            y = 157 + index * 58
            _text(image, row['when'], (24, y, 776, y + 21), 14, True)
            _text(image, row['title'], (24, y + 24, 776, y + 51), 20)
        if rows:
            _text(image, f'{page + 1}/{(len(rows) + PAGE_SIZE - 1) // PAGE_SIZE} · fetched window', (500, 395, 776, 413), 13)
        extra.update(page=page, source_key=source.get('source_key'), approved_fields=source.get('approved_fields'),
                     rows=shown, observed_at=source.get('observed_at'))
        menu._footer(image, '◀ ▶ Now / Day / Notes · ▲ ▼ page · hold: refresh · Back: destinations',
                     state['notice'] or freshness(source, now))
    return image, _identity(extra), extra


def _render_note(menu, state, item, now, tz):
    from x4_menu import paginate, BODY, LINE
    record = item['record']
    pages = paginate(record)
    page_no = min(state['page'], len(pages) - 1)
    page = pages[page_no]
    image = menu._canvas(record['title'], 'Life › ' + category(item) + ' · ' + item['agent'].capitalize() +
                         ' · rev ' + str(item['revision']), tz, now)
    _text(image, page['heading'], (24, 124, 590, 148), 18, True)
    _text(image, f'{page_no + 1}/{len(pages)}', (640, 124, 776, 146), 15, True)
    for index, line in enumerate(page['lines']):
        if line:
            y = BODY[1] + LINE * (index + 1)
            _text(image, line, (24, y, 776, y + LINE - 2), 19)
    exp = record.get('expires_at')
    expiry = _safe_date(exp, tz) if exp else None
    meta = 'Published ' + _age(item['updated_at'], now) + ' ago · '
    meta += 'Expires ' + _when(expiry) if expiry else 'No expiry set'
    menu._footer(image, '▲ ▼ page · read-only: Confirm does not dismiss · hold: refresh · Back: notes', meta)
    extra = {'life_part': 'notes', 'life_record': item['id'], 'life_revision': item['revision'],
             'life_key': note_key(item), 'page': page_no}
    return image, _identity(extra), extra


class LifeMenuMixin:
    def _life_settle(self, device, state, items):
        if state['view'] == 'life_note':
            item = next((i for i in items if i['id'] == state.get('life_record')), None)
            if item is None or note_key(item) != state.get('life_key'):
                state = dict(state, view='life', life_part='notes', life_record=None, page=0,
                             notice='That note changed or expired; choose again.')
                self._save(device, state)
        return state

    def _life_handle(self, device, state, button, press, shown, now, tz, result):
        if not shown or shown.get('view') != state['view'] or shown.get('life_part') != state.get('life_part', 'now'):
            return result
        if state['view'] == 'life_note' and (shown.get('life_record'), shown.get('life_key')) != (
                state.get('life_record'), state.get('life_key')):
            return result
        if button == 'confirm' and press == 'long':
            return dict(result, refresh=True)
        if press != 'short':
            return result
        items = notes(self.records, now)
        if button == 'back':
            if state['view'] == 'life_note':
                state.update(view='life', life_record=None, page=0)
            else:
                state.update(view='menu', cursor=0)
        elif state['view'] == 'life' and button in ('left', 'right'):
            index = PARTS.index(shown['life_part'])
            state.update(life_part=PARTS[(index + (1 if button == 'right' else -1)) % len(PARTS)], page=0, cursor=0)
        elif button in ('up', 'down'):
            step = 1 if button == 'down' else -1
            if state['view'] == 'life_note':
                from x4_menu import paginate
                # Binding includes full content, since deletion can reset a revision.
                item = next((i for i in items if i['id'] == state.get('life_record') and
                             note_key(i) == state.get('life_key')), None)
                if item is None:
                    return result
                total = len(paginate(item['record']))
                state['page'] = max(0, min(total - 1, shown['page'] + step))
            elif shown['life_part'] == 'notes':
                order = shown.get('life_order', [])
                if not order:
                    return result
                wanted = order[(shown['cursor'] + step) % len(order)]
                cursor = next((n for n, i in enumerate(items) if i['id'] == wanted['id'] and
                               note_key(i) == wanted['key']), None)
                if cursor is None:
                    state['notice'] = 'That note changed or expired; choose again.'
                else:
                    state['cursor'] = cursor
            else:
                _, rows, _ = calendar_rows(self.glance.snapshot(), shown['life_part'], now, tz)
                state['page'] = max(0, min(max(0, (len(rows) - 1) // PAGE_SIZE), shown['page'] + step))
        elif button == 'confirm' and state['view'] == 'life' and shown['life_part'] == 'notes':
            item = next((i for i in items if i['id'] == shown.get('life_record') and
                         note_key(i) == shown.get('life_key')), None)
            if item is None:
                state['notice'] = 'That note changed or expired; choose again.'
            else:
                state.update(view='life_note', life_record=item['id'], life_revision=item['revision'],
                             life_key=note_key(item), page=0)
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)
