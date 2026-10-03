"""Tests for validate_sd_image: a port of CrossInk's FirmwareFlasher::validateOpenImageFile."""
import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

import validate_sd_image as v

DRILL = Path(__file__).resolve().parent.parent / "artifacts" / "drill"


def build_image(segments, chip_id=5, tag=b"", hash_appended=True):
    """Assemble a minimal ESP app image the way esptool lays it out."""
    hdr = bytearray(24)
    hdr[0] = 0xE9
    hdr[1] = len(segments)
    struct.pack_into("<H", hdr, 12, chip_id)
    hdr[23] = 1 if hash_appended else 0
    body = bytes(hdr)
    xor = 0xEF
    for i, data in enumerate(segments):
        if i == 0:
            data = data + tag
        body += struct.pack("<II", 0x3C000000 + i * 0x10000, len(data)) + data
        for b in data:
            xor ^= b
    pad_end = (len(body) + 16) & ~15
    body += b"\0" * (pad_end - len(body) - 1) + bytes([xor])
    if hash_appended:
        body += hashlib.sha256(body).digest()
    return body


class ValidateTest(unittest.TestCase):
    def check(self, blob, **kw):
        with tempfile.NamedTemporaryFile(suffix=".bin") as f:
            f.write(blob)
            f.flush()
            return v.validate(f.name, **kw)

    def big(self):
        return [bytes(range(256)) * 300]  # 76.8 KB > 64 KB minimum

    def test_synthetic_ok(self):
        self.assertEqual(self.check(build_image(self.big(), tag=b"CROSSPOINT-BOARD-V1:x4;")).result, "OK")

    def test_untagged_ok(self):
        self.assertEqual(self.check(build_image(self.big())).result, "OK")

    def test_too_small(self):
        self.assertEqual(self.check(build_image([b"x" * 100])).result, "TOO_SMALL")

    def test_too_large(self):
        self.assertEqual(self.check(build_image(self.big()), partition_size=70000).result, "TOO_LARGE")

    def test_bad_magic(self):
        blob = bytearray(build_image(self.big()))
        blob[0] = 0xE8
        self.assertEqual(self.check(bytes(blob)).result, "BAD_MAGIC")

    def test_wrong_chip(self):
        self.assertEqual(self.check(build_image(self.big(), chip_id=9)).result, "BAD_CHIP")

    def test_wrong_board(self):
        r = self.check(build_image(self.big(), tag=b"CROSSPOINT-BOARD-V1:x3;"))
        self.assertEqual((r.result, r.board), ("WRONG_BOARD", "x3"))

    def test_truncated(self):
        self.assertEqual(self.check(build_image(self.big())[:-1024]).result, "BAD_SEGMENTS")

    def test_bad_checksum(self):
        blob = bytearray(build_image(self.big(), hash_appended=False))
        blob[100] ^= 1
        self.assertEqual(self.check(bytes(blob)).result, "BAD_CHECKSUM")

    def test_bad_sha(self):
        blob = bytearray(build_image(self.big()))
        blob[-1] ^= 1
        self.assertEqual(self.check(bytes(blob)).result, "BAD_SHA")

    def test_trailing_garbage(self):
        self.assertEqual(self.check(build_image(self.big()) + b"\0" * 16).result, "BAD_SIZE")

    @unittest.skipUnless((DRILL / "escape-hatch-x4.bin").exists(), "drill artifacts absent")
    def test_drill_artifacts_match_device_outcome(self):
        # Ground truth from the 2026-10-03 drill: CrossInk flashed one, refused the other.
        self.assertEqual(v.validate(DRILL / "escape-hatch-x4.bin").result, "OK")
        self.assertNotEqual(v.validate(DRILL / "zz-corrupt-should-reject.bin").result, "OK")


if __name__ == "__main__":
    unittest.main()
