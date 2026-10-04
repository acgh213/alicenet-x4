"""Exercise the synthetic-only Life artifact producer as a real CLI."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class PreviewLife(unittest.TestCase):
    def test_cli_writes_distinct_source_states_and_readable_end(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONPATH=str(root / 'gateway'))
            env.pop('PYTHONHOME', None)
            result = subprocess.run([sys.executable, str(root / 'tools/preview_life.py'), tmp],
                                    env=env, text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            out = Path(tmp)
            self.assertEqual(len(list(out.glob('*.png'))), 13)
            self.assertEqual(len(list(out.glob('*.pbm'))), 13)
            text = (out / 'transcriptions.txt').read_text()
            for expected in ('END sentinel', 'Rest is valid', 'NEXT ·', 'NOW ·',
                             'Saved window expired', 'No events in fetched window', 'Calendar unavailable',
                             'Refresh failed · STALE', 'End unknown', 'hidden by consent', 'EST', 'All day'):
                self.assertIn(expected, text)
            for path in out.glob('*.pbm'):
                raw = path.read_bytes()
                self.assertTrue(raw.startswith(b'P4\n800 480\n'))
                self.assertEqual(len(raw), 48011)


if __name__ == '__main__':
    unittest.main()
