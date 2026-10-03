# Recovery drill runbook (step 2 gate)

This drill uses **Escape Hatch** (upstream `crosspoint-reader/escape-hatch` at `122aaa5`,
freeink-sdk `e41f683`, built unmodified) instead of our own code. A known-good firmware
proves the path in and out before any alicenet-x4 build depends on it. It also gives three
hardware readings with zero custom code: **Hardware Detect** (panel controller), **Button Test**
(ADC ladder) and **Battery Info**.

Files (in `artifacts/drill/`, gitignored):

- `escape-hatch-x4.bin`: 500,304 B,
  sha256 `d9ac08f802c3a6edad6627e1d9d47b0250ec20edf844c4639e7c46bc047b4ce4`. It embeds
  `CROSSPOINT-BOARD-V1:x4;`; esptool checksum and hash are valid.
- `zz-corrupt-should-reject.bin`: the same file with the last 1,024 B cut off. esptool fails
  it ("End of file reading segment").

## Where things land (from code, not assumption)

- CrossInk runs from ota_0. Its SD flasher writes `esp_ota_get_next_update_partition()` =
  **ota_1** (`crossink/src/network/FirmwareFlasher.cpp:328`). It validates *before* the first
  erase (`:340-345`), so a rejected file doesn't touch flash.
- ota_1 most likely holds an old CrossInk 1.3.4-tiny. It gets overwritten, which doesn't
  matter.
- CrossInk in ota_0 is never written to.

## Steps

0. Put both files in the SD root. Either Pyrrha uploads them through CrossInk's File Transfer
   (`POST /upload?path=/`), or Cassie copies them over.
1. **Reject test.** CrossInk → Settings → SD Firmware Update → pick
   `zz-corrupt-should-reject.bin`. Expect an error (BAD_SIZE/BAD_SEGMENTS/BAD_SHA). The device
   stays in CrossInk, and books and settings are unchanged.
2. **Flash.** Same menu → `escape-hatch-x4.bin` → confirm. Expect a progress bar, a reboot,
   and the Escape Hatch menu.
   - The fallback entry is the recovery chord: from sleep, hold **Up** and press **Power**
     (`crossink/src/main.cpp:1272-1276`).
3. **Readings** (photos are fine):
   - Hardware Detect: panel controller line
   - Button Test: each button's name + ADC value
   - Battery Info
4. **Return, route A.** Hold **Back + Up** while pressing reset (or while waking with Power).
   SDK `checkBootCombo()` repoints otadata to ota_0 → CrossInk (`RecoveryBoot.cpp:182-185`).
   Expect CrossInk with books intact.
5. **Re-enter and return, route B.** Without reflashing, go from CrossInk back to Escape Hatch.
   This route needs a reflash: CrossInk has no "boot other slot", so repeat step 2. Then use
   Escape Hatch's **Boot Other Slot** menu entry to get to CrossInk.
6. Optional: a USB read-only check of otadata (checklist §B) after each step.

Pass = 1 rejected with no change, 2 boots Escape Hatch, 4 and 5 both land in CrossInk
intact. Record the result in `drill-log.md`.

## If something goes sideways

- Escape Hatch itself is an SD flasher. From its menu, **Flash Firmware** →
  `crossink-firmware-x3-x4-v1.5.1.bin` puts CrossInk into the other slot.
- USB is fully open (no secure boot, download mode enabled). The web flasher or esptool
  always works.
- The June full backup exists (checklist §A), but it is a *June* image.
