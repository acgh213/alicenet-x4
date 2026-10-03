#!/usr/bin/env python3
"""Validate an ESP32 app image exactly as CrossInk 1.5.1's SD flasher will.

Port of refs/crossink/src/network/FirmwareFlasher.cpp validateOpenImageFile()
and FirmwareBoardTag.cpp Scanner. Run on every .bin before it goes to the SD
card; "OK" here means CrossInk will accept it, anything else names the same
Result code the device would show.

    validate_sd_image.py firmware.bin [--board x4] [--partition-size 0x640000]
"""
import argparse
import hashlib
import struct
import sys
from dataclasses import dataclass

MIN_FIRMWARE_SIZE = 64 * 1024
SLOT_SIZE = 0x640000
HEADER_SIZE = 24
SEG_HEADER_SIZE = 8
SHA_TRAILER = 32
CHECKSUM_SEED = 0xEF
ESP_IMAGE_MAGIC = 0xE9
ESP32C3_CHIP_ID = 5
MAGIC = b"CROSSPOINT-BOARD-V1:"
MAX_NAME = 31


@dataclass
class Report:
    result: str
    detail: str = ""
    board: str | None = None  # last tag name seen (None = untagged)


class TagScanner:
    """Byte-for-byte port of board_tag::Scanner (streaming, single-byte lookback)."""

    def __init__(self, board: str):
        self.board = board.encode()
        self.matched = 0
        self.capturing = False
        self.name = bytearray()
        self.mismatch = False
        self.found: str | None = None

    def feed(self, data: bytes) -> None:
        for c in data:
            if self.mismatch:
                return
            if self.capturing:
                if c == 0x3B:  # ';'
                    self.capturing = False
                    self.found = self.name.decode()
                    if bytes(self.name) != self.board:
                        self.mismatch = True
                elif len(self.name) < MAX_NAME and 0x20 < c < 0x7F:
                    self.name.append(c)
                else:
                    self.capturing = False
                continue
            if c == MAGIC[self.matched]:
                self.matched += 1
                if self.matched == len(MAGIC):
                    self.matched = 0
                    self.capturing = True
                    self.name = bytearray()
            else:
                self.matched = 1 if c == MAGIC[0] else 0


def validate(path, board: str = "x4", partition_size: int = SLOT_SIZE,
             chip_id: int = ESP32C3_CHIP_ID) -> Report:
    with open(path, "rb") as f:
        blob = f.read()
    size = len(blob)
    if size < MIN_FIRMWARE_SIZE:
        return Report("TOO_SMALL", f"{size} < {MIN_FIRMWARE_SIZE}")
    if partition_size and size > partition_size:
        return Report("TOO_LARGE", f"{size} > {partition_size}")
    if blob[0] != ESP_IMAGE_MAGIC:
        return Report("BAD_MAGIC", f"0x{blob[0]:02X}")
    image_chip = struct.unpack_from("<H", blob, 12)[0]
    if image_chip != chip_id:
        return Report("BAD_CHIP", f"image=0x{image_chip:04X} device=0x{chip_id:04X}")
    seg_count, hash_appended = blob[1], blob[23] != 0

    xor = CHECKSUM_SEED
    tags = TagScanner(board)
    pos = HEADER_SIZE
    for i in range(seg_count):
        if pos + SEG_HEADER_SIZE > size:
            return Report("BAD_SEGMENTS", f"seg {i} header overruns EOF at {pos}")
        data_len = struct.unpack_from("<I", blob, pos + 4)[0]
        pos += SEG_HEADER_SIZE
        if pos + data_len > size:
            return Report("BAD_SEGMENTS", f"seg {i} data overruns EOF ({pos} + {data_len} > {size})")
        data = blob[pos:pos + data_len]
        tags.feed(data)
        for b in data:
            xor ^= b
        pos += data_len

    if tags.mismatch:
        return Report("WRONG_BOARD", f"image={tags.found} device={board}", tags.found)
    pad_end = (pos + 16) & ~15
    expected = pad_end + (SHA_TRAILER if hash_appended else 0)
    if expected != size:
        return Report("BAD_SIZE", f"expected={expected} actual={size}", tags.found)
    if blob[pad_end - 1] != xor:
        return Report("BAD_CHECKSUM", f"computed=0x{xor:02X} stored=0x{blob[pad_end - 1]:02X}", tags.found)
    if hash_appended and hashlib.sha256(blob[:pad_end]).digest() != blob[pad_end:pad_end + SHA_TRAILER]:
        return Report("BAD_SHA", "", tags.found)
    return Report("OK", f"{size} bytes, {seg_count} segments", tags.found)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("images", nargs="+")
    ap.add_argument("--board", default="x4")
    ap.add_argument("--partition-size", type=lambda s: int(s, 0), default=SLOT_SIZE)
    a = ap.parse_args(argv)
    bad = 0
    for p in a.images:
        r = validate(p, a.board, a.partition_size)
        tag = f"tag={r.board}" if r.board else "untagged"
        print(f"{r.result:13} {tag:10} {p}  {r.detail}")
        bad += r.result != "OK"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
