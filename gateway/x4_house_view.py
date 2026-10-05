"""House light preview/receipt views; all authority remains in the narrow helper."""
from PIL import ImageDraw

from x4_dashboard import _text
from x4_house import controllable
from x4_house_actions import fingerprint


def fresh(house, now):
    return (house and house.get('available') is True and not house.get('refresh_failed')
            and 0 <= now - house.get('observed_at', 0) <= house.get('stale_after_s', 1800))


def eligible(item):
    return (controllable(item) and item.get('state') in ('on', 'off')
            and item.get('entity', '').startswith('light.') and bool(item.get('state_key')))


class HouseViews:
    def _on_room(self, device, state, button, press, shown, now, result):
        house = self._house()
        room = next((r for r in (house or {}).get('rooms', []) if r['name'] == state['room']), None)
        if button == 'confirm' and press == 'long':
            return dict(result, refresh=True)
        if button == 'back':
            state.update(view='house', room=None, cursor=0)
        elif button in ('up', 'down') and room:
            state['cursor'] = self._move(state, button, len(room['items']))
        elif button == 'confirm' and press == 'short' and self.house_controls and room:
            displayed = (shown or {}).get('house_item')
            current = next((i for i in room['items'] if i['entity'] == (displayed or {}).get('entity')), None)
            if (not shown or shown.get('view') != 'room' or shown.get('room') != state['room']
                    or not fresh(house, now) or shown.get('house_source_key') != house.get('source_key')
                    or not current or not eligible(current)
                    or fingerprint(current) != fingerprint(displayed)):
                state['notice'] = 'Review current state; no action prepared.'
            else:
                import json
                revision = state.get('_navigation_revision', 0)
                action = None
                try:
                    action = self.house_controls.preview(device, state['room'],
                                                         dict(displayed, ha_source_key=shown.get('house_source_key')), now)
                    state.update(view='house_preview', house_action=action)
                except ValueError:
                    state['notice'] = 'Controls unavailable or policy changed. No action.'
                # The helper can take seconds. Install its result only if no
                # navigation occurred, including leaving and returning to this room.
                state['_navigation_revision'] = revision + 1
                with self.store.lock, self.store._db() as db:
                    installed = db.execute(
                        "UPDATE menu_state SET body=? WHERE device=? "
                        "AND json_extract(body, '$.view')='room' "
                        "AND COALESCE(json_extract(body, '$._navigation_revision'), 0)=?",
                        (json.dumps(state), device, revision)).rowcount
                if not installed and action:
                    try:
                        self.house_controls.cancel(device, action['id'], now)
                    except ValueError:
                        pass  # obsolete frames cannot confirm this discarded action
                return dict(result, moved=bool(installed))
        else:
            return result
        self._save(device, state)
        return dict(result, moved=True)

    def _update_house_receipt(self, device, action, receipt):
        # Update just the receipt, conditionally in one SQLite transaction. An old
        # worker/reconciliation must never restore an obsolete view or preview.
        import json
        with self.store.lock, self.store._db() as db:
            db.execute("UPDATE menu_state SET body=json_set(body, '$.house_receipt', json(?)) "
                       "WHERE device=? AND json_extract(body, '$.view')='house_receipt' "
                       "AND json_extract(body, '$.house_action.id')=? "
                       "AND json_extract(body, '$.house_receipt.outcome')='uncertain'",
                       (json.dumps(dict(receipt, action=action)), device, action['id']))

    def _on_house_preview(self, device, state, button, press, shown, now, result):
        action = state.get('house_action')
        if button == 'back':
            if action and self.house_controls:
                try:
                    self.house_controls.cancel(device, action['id'], now)
                except ValueError:
                    pass  # leaving this view permanently invalidates its frame
            state.update(view='room', house_action=None)
        elif button == 'confirm' and press == 'short':
            if (not action or not self.house_controls or not shown or shown.get('view') != 'house_preview'
                    or shown.get('house_action') != action):
                state['notice'] = 'Review this preview. Nothing executed.'
            else:
                # Persist uncertainty in gateway BEFORE entering the helper. Lost response/crash
                # cannot leave a runnable preview, even if helper accepted the service already.
                receipt = {'id': action['id'], 'outcome': 'uncertain', 'action': action, 'at': now}
                import json
                with self.store.lock, self.store._db() as db:
                    claimed = db.execute(
                        "UPDATE menu_state SET body=json_set(body, '$.view', 'house_receipt', "
                        "'$.house_receipt', json(?)) WHERE device=? "
                        "AND json_extract(body, '$.view')='house_preview' "
                        "AND json_extract(body, '$.house_action.id')=?",
                        (json.dumps(receipt), device, action['id'])).rowcount
                if not claimed:
                    return result  # another Confirm/navigation won; do not schedule a helper
                def execute():
                    receipt = {'id': action['id'], 'outcome': 'uncertain', 'action': action, 'at': now}
                    try:
                        receipt = self.house_controls.confirm(device, action['id'], now)
                    except Exception:
                        pass
                    self._update_house_receipt(device, action, receipt)
                if result.get('defer_house'):
                    return dict(result, moved=True, house_execute=execute)
                execute()
                return dict(result, moved=True)
        else:
            return result  # hold NEVER approves, refreshes, or forwards on a preview
        self._save(device, state)
        return dict(result, moved=True)

    def _reconcile_house_receipt(self, device, state):
        if (state['view'] != 'house_receipt' or not self.house_controls
                or state.get('house_receipt', {}).get('outcome') != 'uncertain'):
            return state
        action = state.get('house_action')
        try:
            receipt = self.house_controls.receipt(device, action['id'])
            if (receipt and receipt.get('action') == action
                    and receipt.get('outcome') in ('verified', 'rejected', 'cancelled')):
                self._update_house_receipt(device, action, receipt)
                state = self.state(device)
        except Exception:
            pass  # read-only failure retains uncertainty; NEVER resubmit confirmation
        return state

    def _on_house_receipt(self, device, state, button, press, shown, now, result):
        if button != 'back':
            return result  # no retry button, including long Confirm
        state.update(view='room', house_action=None)
        self._save(device, state)
        return dict(result, moved=True)

    def _render_house_action(self, state, now, tz):
        action = state['house_action']
        preview = state['view'] == 'house_preview'
        outcome = state.get('house_receipt', {}).get('outcome', 'uncertain')
        title = 'Confirm light change' if preview else {
            'verified': 'Verified', 'rejected': 'Rejected · nothing sent',
            'cancelled': 'Cancelled', 'uncertain': 'Uncertain · check light'}[outcome]
        image = self._canvas(title, 'House › ' + action['room'], tz, now)
        lines = [action['label'], action['entity'],
                 'Observed: ' + action['observed']['state'] + '  →  Requested: ' + action['requested'],
                 'Source: Home Assistant · explicit turn_' + action['requested'],
                 'Revision: ' + action['revision'][:16],
                 'Reversible: return here for a new opposite-state preview.' if preview else
                 'Fresh readback matched requested state.' if outcome == 'verified' else
                 'No retry. Result unknown; inspect independently.' if outcome == 'uncertain' else
                 'Policy, observation or preview changed; review again.']
        for index, text in enumerate(lines):
            y = 128 + 42 * index
            _text(image, text, (24, y, 776, y + 30), 17 if index > 0 else 22, index == 0)
        self._footer(image, 'Confirm: execute displayed change · Back: cancel' if preview else 'Back: room · no retry',
                     state.get('notice') or ('Preview expires after 120 seconds.' if preview else None))
        return image, ('house.preview.' if preview else 'house.receipt.') + action['id'], {'house_action': action}

    def _render_control_room(self, state, now, tz, house):
        from x4_menu import house_freshness
        room = next(r for r in house['rooms'] if r['name'] == state['room'])
        image = self._canvas(room['name'], 'House › ' + room['name'], tz, now)
        cursor = min(state['cursor'], len(room['items']) - 1)
        pitch = min(50, 286 // max(1, len(room['items'])))
        for index, item in enumerate(room['items']):
            y = 124 + index * pitch
            mark = index == cursor
            if mark:
                ImageDraw.Draw(image).rectangle((24, y, 776, y + pitch - 2), outline=0, width=2)
            _text(image, ('▶ ' if mark else '') + item['label'], (30, y + 4, 300, y + 30), 19, True)
            value = (' · '.join(p for p in (item['state'], item.get('detail'),
                     'protected' if item.get('protected') else '') if p) if item.get('available')
                     else '— ' + item['state'].upper())
            _text(image, value, (310, y + 4, 770, y + 30), 17)
        selected = room['items'][cursor]
        hint = ('▲ ▼ device · Confirm: preview light change · hold: refresh · Back' if eligible(selected) and fresh(house, now)
                else '▲ ▼ device · read-only · hold: refresh · Back: rooms')
        self._footer(image, hint, state.get('notice') or house_freshness(house, now))
        extra = {'house_item': selected, 'room': room['name'], 'house_source_key': house.get('source_key')}
        identity = fingerprint(extra)
        return image, 'house.room.' + identity[:24], dict(extra, frame_identity=identity)
