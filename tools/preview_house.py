"""Generate synthetic House previews and mocked receipts, never call native HA."""
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock

import x4d
from x4_house import collect, validate_config
from x4_house_actions import Controls
from test_x4_dashboard import fixture, NOW, TZ
from test_x4_house import config, state


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cfg = validate_config(config())
        cfg['rooms'][0]['entities'][0]['label'] = 'FIXTURE light'
        ha = Mock()
        ha.source.return_value = 'fixture'
        ha.fetch.side_effect = lambda e: state(e, 'off')
        ha.set_light.return_value = True
        snap = fixture()
        snap['house'] = dict(collect(cfg, ha.fetch, NOW), source_key='fixture')
        path = root / 'snapshot.json'
        path.write_text(json.dumps(snap))
        app = x4d.App({'db': str(root / 'db'), 'glance_snapshot': str(path)})
        app.menu.house_controls = Controls(app.store, lambda: cfg, ha)
        seq = 0
        def frame():
            return app.menu.frame('x4-01', NOW, TZ, 120)
        def press(button):
            nonlocal seq
            shown = frame()
            seq += 1
            app.menu.handle('x4-01', {'seq': seq, 'card': shown['card'], 'etag': shown['etag'],
                                    'button': button, 'press': 'short'}, NOW, TZ)
        def shot(name):
            shown = frame()
            shown['image'].save(out / (name + '.png'))
        press('back')
        for _ in range(3): press('down')
        press('confirm'); press('confirm'); shot('01-room')
        press('confirm'); shot('02-preview')
        ha.fetch.side_effect = [state('light.lr', 'off'), state('light.lr', 'on')]
        press('confirm'); shot('03-verified')
        press('back')
        ha.fetch.side_effect = lambda e: state(e, 'off')
        press('confirm')
        ha.set_light.side_effect = TimeoutError
        press('confirm'); shot('04-uncertain')
        print(json.dumps({'synthetic': True, 'physical_calls': 0, 'previews': sorted(p.name for p in out.glob('*.png'))}))


if __name__ == '__main__':
    main()
