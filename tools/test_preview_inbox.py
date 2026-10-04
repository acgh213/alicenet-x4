"""Synthetic preview artifact generation must be runnable and complete."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class InboxPreview(unittest.TestCase):
    def test_preview_generates_images_and_readable_transcriptions(self):
        root = Path(__file__).resolve().parents[1]
        script = root / "tools" / "preview_inbox.py"
        self.assertTrue(script.exists(), "Inbox previews are not implemented")
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONPATH=str(root / "gateway"))
            result = subprocess.run([sys.executable, str(script), tmp], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            text = (Path(tmp) / "transcriptions.txt").read_text()
            for needle in ("Inbox not configured", "Synthetic lab", "END sentinel", "STALE", "error",
                           "12:00", "Inbox Unavailable"):
                self.assertIn(needle, text)
            self.assertGreaterEqual(len(list(Path(tmp).glob("*.png"))), 7)
            self.assertNotIn("fixture-chat", text)


if __name__ == "__main__":
    unittest.main()
