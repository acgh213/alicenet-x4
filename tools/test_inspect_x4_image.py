#!/usr/bin/env python3
"""Builds a synthetic 16 MiB X4 flash image from real build artifacts and
checks inspect_x4_image against it. Paths are passed in, nothing is written to
any device.

Usage: test_inspect_x4_image.py BOOTLOADER PARTITIONS BOOT_APP0 APP0 APP1
"""
import binascii
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inspect_x4_image as ix  # noqa: E402


def otadata_entry(seq, state=2):
    crc = binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF) & 0xFFFFFFFF
    return struct.pack("<I20sII", seq, b"\xff" * 20, state, crc)


def build(paths, select_seq=None):
    img = bytearray(b"\xff" * 0x1000000)
    for off, p in zip((0x0, 0x8000, 0xE000, 0x10000, 0x650000), paths):
        data = open(p, "rb").read()
        img[off:off + len(data)] = data
    if select_seq is not None:
        img[0xE000:0x10000] = b"\xff" * 0x2000
        e = otadata_entry(select_seq)
        img[0xE000:0xE000 + len(e)] = e
    return memoryview(bytes(img))


def main(argv):
    paths = argv[1:6]
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    r = ix.inspect(build(paths, select_seq=2))  # seq 2 -> (2-1)%2 = ota_1
    labels = [p["label"] for p in r["partitions"]]
    check("partition labels", labels == ["nvs", "otadata", "app0", "app1", "spiffs", "coredump"])
    check("app1 offset", r["partitions"][3]["offset"] == "0x650000")
    check("otadata seq 2 selects app1", r["boot_slot"] == "app1")
    a0, a1 = r["apps"]
    check("app0 project", a0.get("project") == "escape-hatch" or a0.get("status") == "app")
    check("app1 is CrossInk 1.5.1", a1.get("project") == "CrossInk" and a1.get("release") == "CrossInk version: 1.5.1")
    check("app1 secure_version 0 (no anti-rollback floor)", a1.get("secure_version") == 0)
    check("app1 board tag x4", a1.get("board_tag") == "CROSSPOINT-BOARD-V1:x4;")
    check("app1 sha256 trailer valid", a1.get("sha256_valid") is True)
    check("app1 image size == file size", a1.get("image_size") == os.path.getsize(paths[4]))
    r3 = ix.inspect(build(paths, select_seq=3))
    check("otadata seq 3 selects app0", r3["boot_slot"] == "app0")
    print("app0:", {k: a0.get(k) for k in ("project", "version", "board_tag", "sha256_valid")})
    print("arduino boot_app0 otadata ->", ix.inspect(build(paths))["boot_slot"])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
