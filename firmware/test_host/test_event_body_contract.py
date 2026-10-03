"""Contract test: the firmware's event body must pass x4d's real validator.

Compiles emit_event_body.cpp (which uses the firmware's own json_out.h), then
runs every body it prints through gateway/x4_protocol.validate_events.
Run:  env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 test_event_body_contract.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "gateway"))
import x4_protocol  # noqa: E402


class EventBodyContract(unittest.TestCase):
    def test_firmware_bodies_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "emit")
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Werror", "-I" + os.path.join(HERE, "..", "src"),
                            os.path.join(HERE, "emit_event_body.cpp"), "-o", exe], check=True)
            lines = subprocess.run([exe], check=True, capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(len(lines), 2)
        first = x4_protocol.validate_events(json.loads(lines[0]))
        ev = first["events"][0]
        self.assertEqual((first["device"], first["boot"]), ("x4-01", 7))
        self.assertEqual((ev["seq"], ev["card"], ev["button"], ev["press"], ev["wake"]),
                         (1, "network-smoke", "confirm", "short", "button"))
        self.assertEqual(ev["etag"], '"0530d69b7be984b1"')
        second = x4_protocol.validate_events(json.loads(lines[1]))["events"][0]
        self.assertIsNone(second["card"])
        self.assertIsNone(second["etag"])


if __name__ == "__main__":
    unittest.main()
