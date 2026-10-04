"""Narrow House authority and durable receipts, run in the credentialed helper only."""
import hashlib
import json
import secrets

from x4_house import controllable, validate_config, normalize

TTL = 120


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def permitted(config, room, item):
    entries = [(r['name'], e) for r in config['rooms'] for e in r['entities'] if e['entity'] == item.get('entity')]
    return (len(entries) == 1 and entries[0][0] == room and entries[0][1]['label'] == item.get('label')
            and entries[0][1]['protected'] is False and controllable(item)
            and isinstance(item.get('entity'), str) and item['entity'].startswith('light.')
            and item.get('state') in ('on', 'off') and item.get('state_key'))


class Controls:
    def __init__(self, store, load_config, ha):
        self.store, self.load_config, self.ha = store, load_config, ha
        with store.lock, store._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS house_actions (id TEXT PRIMARY KEY, device TEXT, body TEXT, receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS house_audit (action TEXT, at REAL, outcome TEXT, body TEXT)')

    def preview(self, device, room, item, now):
        config = validate_config(self.load_config())
        source_key = self.ha.source()
        if not permitted(config, room, item) or item.get('ha_source_key') != source_key:
            raise ValueError('This device cannot be controlled.')
        action = {'id': secrets.token_hex(12), 'device': device, 'room': room, 'entity': item['entity'],
                  'label': item['label'], 'observed': {'state': item['state'], 'state_key': item['state_key']},
                  'requested': 'off' if item['state'] == 'on' else 'on', 'source': 'Home Assistant',
                  'source_key': source_key, 'config_key': fingerprint(config),
                  'created_at': now, 'expires_at': now + TTL}
        action['revision'] = fingerprint(action)
        receipt = {'id': action['id'], 'outcome': 'preview', 'at': now, 'action': action}
        with self.store.lock, self.store._db() as db:
            db.execute('INSERT INTO house_actions VALUES (?,?,?,?)',
                       (action['id'], device, json.dumps(action), json.dumps(receipt)))
            self._audit(db, receipt)
        return action

    @staticmethod
    def _audit(db, receipt):
        db.execute('INSERT INTO house_audit VALUES (?,?,?,?)',
                   (receipt['id'], receipt['at'], receipt['outcome'], json.dumps(receipt)))

    def receipt(self, device, ident):
        with self.store._db() as db:
            row = db.execute('SELECT receipt FROM house_actions WHERE id=? AND device=?', (ident, device)).fetchone()
        return json.loads(row['receipt']) if row else None

    def _finish(self, action, outcome, now):
        receipt = {'id': action['id'], 'outcome': outcome, 'at': now, 'action': action}
        with self.store.lock, self.store._db() as db:
            db.execute('UPDATE house_actions SET receipt=? WHERE id=?', (json.dumps(receipt), action['id']))
            self._audit(db, receipt)
        return receipt

    def cancel(self, device, ident, now):
        with self.store.lock:
            receipt = self.receipt(device, ident)
            if receipt and receipt['outcome'] == 'preview':
                return self._finish(receipt['action'], 'cancelled', now)
            return receipt

    def confirm(self, device, ident, now):
        # The helper also holds a process-shared flock for its entire invocation.
        with self.store.lock:
            receipt = self.receipt(device, ident)
            if receipt is None:
                return {'id': ident, 'outcome': 'rejected', 'at': now}
            if receipt['outcome'] != 'preview':
                return receipt  # includes crash/network-uncertain; NEVER retry
            action = receipt['action']
            try:
                config = validate_config(self.load_config())
                if not action['created_at'] <= now < action['expires_at']:
                    raise ValueError('expired')
                item = dict(normalize(self.ha.fetch(action['entity']), action['label']),
                            protected=False)
                raw_key = item.get('state_key')
                if (fingerprint(config) != action['config_key'] or self.ha.source() != action['source_key']
                        or not permitted(config, action['room'], item)
                        or item['entity'] != action['entity'] or item['state'] != action['observed']['state']
                        or raw_key != action['observed']['state_key']):
                    raise ValueError('changed')
                # Independent current policy check AFTER the network read.
                if fingerprint(validate_config(self.load_config())) != action['config_key']:
                    raise ValueError('revoked')
            except Exception:
                return self._finish(action, 'rejected', now)
            # Commit uncertainty BEFORE sending: crash cannot make this action executable again.
            self._finish(action, 'uncertain', now)
            try:
                accepted = self.ha.set_light(action['entity'], 'turn_' + action['requested'])
                raw = self.ha.fetch(action['entity'])
                if accepted is True and raw.get('entity_id') == action['entity'] and raw.get('state') == action['requested']:
                    return self._finish(action, 'verified', now)
            except Exception:
                pass  # no error bodies, credentials or fabricated success
            return self.receipt(device, ident)
