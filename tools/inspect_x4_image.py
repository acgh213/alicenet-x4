#!/usr/bin/env python3
"""Read-only inspector for an Xteink X3/X4 (ESP32-C3) full 16 MB flash dump.

Reports: partition table, which OTA slot otadata selects, and for each app
slot the ESP-IDF app descriptor (project, version, IDF, build date,
secure_version), the CrossPoint-family board tag, and the image's SHA-256
trailer. Never writes to the input; works on a backup file, not the device.

Usage:  inspect_x4_image.py BACKUP.bin [--json]
"""
import binascii
import hashlib
import json
import struct
import sys

PT_OFFSET = 0x8000
PT_ENTRY = struct.Struct("<2sBBII16sI")  # magic, type, subtype, offset, size, label, flags
PT_MAGIC = b"\xaa\x50"
APP_DESC_MAGIC = 0xABCD5432
IMAGE_MAGIC = 0xE9
OTA_STATES = {0: "NEW", 1: "PENDING_VERIFY", 2: "VALID", 3: "INVALID", 4: "ABORTED", 0xFFFFFFFF: "UNDEFINED"}
BOARD_TAG = b"CROSSPOINT-BOARD-V1:"


def cstr(raw):
    return bytes(raw).split(b"\0", 1)[0].decode("utf-8", "replace")


def read_partitions(img):
    parts = []
    for off in range(PT_OFFSET, PT_OFFSET + 0xC00, PT_ENTRY.size):
        magic, ptype, sub, poff, size, label, flags = PT_ENTRY.unpack_from(img, off)
        if magic != PT_MAGIC:
            break  # 0xEBEB is the MD5 entry; 0xFFFF is the end
        parts.append({"label": cstr(label), "type": ptype, "subtype": sub, "offset": poff, "size": size})
    return parts


def read_otadata(img, part, n_ota):
    """Mirror the bootloader's choice: highest valid seq wins; slot = (seq-1) % n_ota."""
    entries, best = [], None
    for i in range(2):
        seq, label, state, crc = struct.unpack_from("<I20sII", img, part["offset"] + i * 0x1000)
        ok = seq != 0xFFFFFFFF and crc == (binascii.crc32(struct.pack("<I", seq), 0xFFFFFFFF) & 0xFFFFFFFF)
        usable = ok and state not in (3, 4)
        entries.append({"sector": i, "seq": seq, "state": OTA_STATES.get(state, hex(state)), "crc_ok": ok})
        if usable and (best is None or seq > best):
            best = seq
    selected = None if best is None or n_ota == 0 else (best - 1) % n_ota
    return entries, selected


def describe_app(img, part):
    base = part["offset"]
    info = {"slot": part["label"], "offset": hex(base)}
    if img[base] != IMAGE_MAGIC:
        info["status"] = "empty" if img[base] == 0xFF else "no image (magic 0x%02x)" % img[base]
        return info
    n_seg = img[base + 1]
    chip_id = struct.unpack_from("<H", img, base + 12)[0]
    hash_appended = img[base + 23] == 1
    # Walk segments to find the image end (checksum pad + optional SHA-256).
    pos = base + 24
    for _ in range(n_seg):
        _, seg_len = struct.unpack_from("<II", img, pos)
        pos += 8 + seg_len
    pad_end = base + ((pos - base + 16) & ~15)
    end = pad_end + (32 if hash_appended else 0)
    info.update(status="app", chip_id=chip_id, segments=n_seg, image_size=end - base)
    desc_off = base + 24 + 8  # app descriptor opens the first segment
    magic, secure_ver = struct.unpack_from("<II", img, desc_off)
    if magic == APP_DESC_MAGIC:
        d = desc_off + 16
        info.update(version=cstr(img[d:d + 32]), project=cstr(img[d + 32:d + 64]),
                    build=cstr(img[d + 64:d + 80]) + " " + cstr(img[d + 80:d + 96]),
                    idf=cstr(img[d + 96:d + 128]), secure_version=secure_ver)
    body = bytes(img[base:end])
    t = body.find(BOARD_TAG)
    if t >= 0:
        info["board_tag"] = body[t:body.find(b";", t) + 1].decode("ascii", "replace")
    # The descriptor 'version' is often a git hash; CrossInk also embeds a
    # human release string.
    for marker in (b"CrossInk version: ", b"CrossPoint version: "):
        m = body.find(marker)
        if m >= 0:
            info["release"] = cstr(body[m:m + 64]).split("\n", 1)[0]
    if hash_appended:
        stored = body[-32:]
        calc = hashlib.sha256(body[:-32]).digest()
        info["sha256_trailer"] = stored.hex()
        info["sha256_valid"] = stored == calc
    info["sha256_whole_image"] = hashlib.sha256(body).hexdigest()
    return info


def inspect(img):
    parts = read_partitions(img)
    apps = [p for p in parts if p["type"] == 0 and 0x10 <= p["subtype"] < 0x20]
    otad = next((p for p in parts if p["type"] == 1 and p["subtype"] == 0), None)
    report = {"flash_size": len(img), "partitions": [
        dict(p, offset=hex(p["offset"]), size=hex(p["size"])) for p in parts]}
    if otad:
        entries, sel = read_otadata(img, otad, len(apps))
        report["otadata"] = entries
        report["boot_slot"] = apps[sel]["label"] if sel is not None else "none valid -> bootloader picks first app (ota_0)"
    report["apps"] = [describe_app(img, p) for p in apps]
    return report


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[1], "rb") as f:
        img = memoryview(f.read())
    if len(img) == 0x2000:  # a bare otadata read (esptool read-flash 0xe000 0x2000)
        entries, sel = read_otadata(img, {"offset": 0}, 2)
        for e in entries:
            print("otadata[%d]: seq=0x%08x state=%s crc_ok=%s" % (e["sector"], e["seq"], e["state"], e["crc_ok"]))
        print("boot slot (assuming 2 OTA apps):", "ota_%d" % sel if sel is not None else "none valid -> ota_0")
        return 0
    if len(img) != 0x1000000:
        print("warning: image is %d bytes, expected 16 MiB" % len(img), file=sys.stderr)
    r = inspect(img)
    if "--json" in argv:
        print(json.dumps(r, indent=2))
        return 0
    print("partitions:")
    for p in r["partitions"]:
        print("  %-9s type=%d sub=0x%02x off=%-9s size=%s" % (p["label"], p["type"], p["subtype"], p["offset"], p["size"]))
    for e in r.get("otadata", []):
        print("otadata[%d]: seq=0x%08x state=%s crc_ok=%s" % (e["sector"], e["seq"], e["state"], e["crc_ok"]))
    print("boot slot:", r.get("boot_slot"))
    for a in r["apps"]:
        print("%s @ %s:" % (a["slot"], a["offset"]))
        for k, v in a.items():
            if k not in ("slot", "offset"):
                print("    %-18s %s" % (k, v))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
