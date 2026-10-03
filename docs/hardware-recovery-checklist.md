# Hardware and recovery checklist

Recovery is treated as already established: Cassie has a full backup, on Eido's PC. This
checklist covers identifying exactly what's on the device and the new risks the Alicenet
firmware introduces.

## A. What's installed now (known)

- [x] Firmware: **CrossInk 1.5.1**, a CrossPoint fork. https://github.com/uxjulia/CrossInk
  tag `v1.5.1` = `4f2da6b7`; release asset `crossink-firmware-x3-x4-v1.5.1.bin`
  (6,047,504 B, saved in `artifacts/`). It embeds `CROSSPOINT-BOARD-V1:x4;` and
  `secure_version 0`.
- [x] Partition layout: standard X4 16 MB (nvs / otadata@0xe000 / app0@0x10000 /
  app1@0x650000, 0x640000 each / spiffs / coredump). Identical across CrossPoint,
  CrossInk, Escape Hatch and the OpenX4 sample.
- [x] **How it was flashed:** the online web flasher (Cassie, 2026-10-03). Which slot it wrote
  is still unknown, so read otadata from the live device (step B).
- [ ] **Which OTA slot is active.** Determine it read-only (step B).
- [x] **Backup file** (found by Eido, 2026-10-03), on Eido's PC:
  `C:\Users\Cassie\Downloads\flash.bin`, 16,777,216 B, 2026-06-29 21:43,
  sha256 `a54453281353feb5eb1d4c9433e19efe604a746fb878a8dd5c4ee90386caf30b`.
  `flash2.bin` (23:38 the same night) is byte-identical, so it's a verified double read.
  Its otadata is seq 1 / state UNDEFINED and seq 2 / state VALID, which selects **ota_1** at
  backup time.
  ⚠ **The backup predates CrossInk 1.5.1** (released 2026-09-11). It captures whatever ran in
  June (probably stock or an early CrossPoint), *not* the current CrossInk install. Restoring it
  goes back to that state; to get back to CrossInk afterwards, re-run the web flasher.
- Physical: the reset button is just below the USB port, bottom-left with the screen facing you.
  Press-and-hold Power wakes it. In CrossInk, Wi-Fi joins in ~6–7 s.

## B. Read-only identification (USB, no writes)

Only read commands. The device stays exactly as it is. Port is `/dev/ttyACM0` on Linux or
`COMx` on Windows.

```sh
esptool --chip esp32c3 --port $PORT flash-id                   # 16 MB? manufacturer id
esptool --chip esp32c3 --port $PORT read-flash 0x8000 0xc00 pt.bin
esptool --chip esp32c3 --port $PORT read-flash 0xe000 0x2000 otadata.bin
python3 tools/inspect_x4_image.py otadata.bin                  # → "boot slot: ota_0|ota_1"
espefuse --chip esp32c3 --port $PORT summary > efuse.txt       # read-only; record, don't act
```

If the backup file is at hand, `python3 tools/inspect_x4_image.py backup.bin` reports all of the
above offline: partition table, active slot, and per-slot project/version/board tag/SHA validity.

Record: active slot = CrossInk's slot; the **other** slot is where alicenet-x4 will go.
(Tested: `tools/test_inspect_x4_image.py` passes against real CrossInk + Escape Hatch artifacts.)

## C. Full restore (last resort)

```sh
esptool --chip esp32c3 --port $PORT --baud 921600 write-flash 0x0 <backup-16MB.bin>
```

Writes all 16 MB (bootloader, partition table, otadata, both slots, NVS) back exactly as dumped.
Verify afterwards: `esptool … verify-flash 0x0 <backup-16MB.bin>`.

CrossInk-only reinstall (no backup needed) is CrossInk's own SD picker or the release asset at
the slot's offset.

## D. New recovery risks this project introduces (and mitigations)

1. **Latch-held deep sleep.** In ambient mode the battery stays connected while the ESP32
   sleeps. If our firmware sleeps *without* arming the power-button GPIO wake, the power button
   can't wake it (the battery is already connected, so bridging the rail does nothing), and
   neither can the timer if it isn't set.
   - Mitigation: one `sleepAmbient()` function arms **both** timer and power-button wakes, clamps
     the timer to ≤6 h, and has a host-side unit test for the clamp.
   - Escape: USB in + reset, or wait for the timer.
   - **Find out where the X4's reset button is before first flash.** CrossPoint issue #1263 users
     mention "press reset".
2. **Stuck awake with the latch held** drains the battery (the #1263 failure mode).
   - Mitigation: a 90 s hard awake watchdog (`esp_task_wdt` + a millis guard) that forces sleep.
     Below 3.45 V, the firmware drops to pocket mode (latch LOW, same as the reader).
3. **Our image in the other slot is only reachable through CrossInk's flasher.** CrossInk has no
   "boot other slot" option, so getting from CrossInk back to Alicenet means re-flashing
   `alicenet-x4.bin` from SD (validated, roughly 20–40 s).
   - Accepted for the slice. Revisit if switching back and forth becomes a daily habit.
4. **`pio run -t upload` writes `boot_app0.bin` and app at 0x10000.** That silently replaces
   ota_0, possibly CrossInk.
   - Mitigation: `upload` is disabled in our platformio.ini (custom `upload_command` that
     refuses). USB writes go through `tools/flash-slot.sh` with an explicit slot and a pre-read
     of otadata.
5. **CrossInk online OTA overwrites alicenet-x4** (it installs into the other slot).
   - Mitigation: documented. Don't press it while dual-booting.

## E. Recovery drill (must pass before any network code is trusted)

Using the "hello" build (vertical slice step 2):

1. Copy `alicenet-x4.bin` to SD. In CrossInk: sleep → hold **Up** + press **Power** → pick the
   .bin. Expect it to validate, flash, and boot to the alicenet-x4 status screen.
2. Hold **Back+Up** and press Power (wake). Expect CrossInk to boot with books intact.
3. Repeat step 1 with a **deliberately corrupted** .bin (truncate by 1 KB). Expect CrossInk to
   reject it (BAD_SIZE / BAD_SHA) and stay in CrossInk.
4. With USB attached, run the read-only step B again. Expect otadata to point at the expected
   slot each time.

Record pass/fail with dates in `docs/drill-log.md`.
