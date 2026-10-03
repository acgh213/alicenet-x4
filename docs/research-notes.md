# Research notes — Xteink X4 as an Alicenet endpoint

All paths are relative to `refs/` (read-only clones, see bottom for exact commits).
Everything here was read in source or measured locally unless marked **(unverified)**.

## 1. The finding that shapes everything: on battery, "sleep" is power-off

`freeink-sdk/libs/hardware/BoardConfig/include/BoardConfig.h:916-921` (X4 profile):

> GPIO13 gates the battery MOSFET. Known units self-latch through a pull once the
> power button bridges the rail … at least one hardware revision in the field does
> not self-latch … Driving it LOW is the battery power-off.

`crossink/lib/hal/HalPowerManager.cpp:103-113` — on the C3 (`!SOC_PM_SUPPORT_EXT1_WAKEUP`)
CrossInk drives every latch LOW and holds it before `esp_deep_sleep_start()` (`:146`).
Same code in `crosspoint-reader/lib/hal/HalPowerManager.cpp:78`.

Provenance: crossink commit `526c8a5e` ("use sleep routine from the original firmware",
#1298) — reverse-engineered from stock firmware V3.1.1:

> GPIO13 is connected to battery latch MOSFET … this means the MCU will be completely
> powered off during sleep, including RTC … On battery … the power button is hard-wired
> to briefly provide power to the MCU, waking it up regardless of the wakeup source.

Consequences:

- What every X4 firmware calls "deep sleep" is a **battery disconnect**. No RTC, no timer
  wake. Only the power button (which bridges the rail) brings it back.
- **A periodic-poll appliance must keep GPIO13 HIGH through ESP32 deep sleep**
  (`gpio_hold_en`) and use `esp_sleep_enable_timer_wakeup`. None of the reference
  firmwares do this. `grep esp_sleep_enable_timer_wakeup` across all refs: 0 hits.
- The deep-sleep floor current of the X4 board with the latch held is therefore
  **unmeasured by anyone in these repos**. It is the #1 hardware measurement.
- Before #1298, CrossPoint used plain deep sleep without dropping the latch. Issue #1263
  ("Hang on sleep with battery drain") describes devices that failed to sleep and drained.
  That is the failure mode we re-enter by holding the latch, so an awake-time watchdog
  is mandatory, not optional.
- `holdPowerRails()` (`BoardConfig.h:2101-2125`) asserts the latch HIGH at boot and
  explicitly undoes a prior `gpio_hold_en` LOW. Escape Hatch calls it at
  `escape-hatch/src/main.cpp:1036`.

## 2. Panel controller is not guaranteed to be SSD1677

`BoardConfig.h:129-142`: "Newer batches of several Xteink panels ship an UltraChip
controller in place of the original … UC8179 — X4 / X4 Pro (800x480), replaces the
SSD1677 … resolved at boot by the display-bus probe (0x70 VER readback)". X4 builds link
SSD1677, UC8179 and UC8279_X4 drivers (`:149-155`). Using FreeInkDisplay gets this for
free; the old community-sdk `EInkDisplay` is SSD1677-only.

## 3. Display API and refresh model (FreeInkDisplay)

`freeink-sdk/libs/display/FreeInkDisplay/include/FreeInkDisplay.h`:
- `:34` `enum RefreshMode { FULL_REFRESH, HALF_REFRESH, FAST_REFRESH };`
- `:99` `clearScreen(uint8_t color = 0xFF)` — **0xFF = white**, so framebuffer bit 1 = white.
- `:107` the first refresh after a mode change is promoted FAST→HALF.
- `:248-254` FAST against a stale baseline is downgraded to HALF unless the caller seeds
  RED RAM (`syncRedRamFromFrameBuffer`) and opts in with `setSingleBufferFastDiff`.
- `:294` `deepSleep()`; `src/driver/Ssd1677Driver.cpp:757-760`: "deep sleep mode 2
  … discards controller RAM".

So **after every wake the controller has forgotten the previous frame**. The first update
is HALF (or FULL) unless we store the last frame ourselves and re-seed RED RAM. That
optimisation is possible but deferred (see vertical slice, later work).

## 4. Buttons

`freeink-sdk/libs/hardware/InputManager/src/InputManager.cpp:28-41` — two ADC ladders:
GPIO1 = Back/Confirm/Left/Right (3512/2694/1493/5 avg), GPIO2 = Up/Down (2242/5).
Power is GPIO3, digital. `InputManager.h:29-67`: `isPressed`, `wasReleased`,
`getHeldTime()`, indices `BTN_BACK=0 … BTN_POWER=6`, plus a latched press-edge queue
(`:221-231`).

- Long press: supported directly (`getHeldTime`).
- Double press: software-timed, works, but costs ~300 ms latency on every single press.
- **Chords only work across ladders**: two buttons on the same ADC pin collapse into one
  reading (escape-hatch `README.md:176-180`). Back+Up and Confirm+Down are fine; Back+Right
  is not.
- **Only Power reliably wakes the device.** With the latch dropped it is the only thing that
  can, because it physically reconnects the battery. With the latch held (ambient mode),
  GPIO1/GPIO2 are in the C3's deep-sleep-wake range (GPIO0–5), and Right/Down pull their
  ladder to ~0 V, so a low-level wake on those two *might* work. The other four sit at
  intermediate voltages and won't register as a digital low. **Unverified**; not used in the
  slice.

## 5. Recovery machinery that already exists

- **Escape Hatch** (`escape-hatch/README.md:161-185`, `freeink-sdk/libs/hardware/RecoveryBoot`):
  `freeink::recovery::checkBootCombo()` as the first line of `setup()`. With Back+Up held,
  it repoints otadata at ota_0 and reboots. It's a no-op unless the combo is held and ota_0
  is valid. Limit: it can't help a firmware that dies before `setup()`.
- **RecoveryBoot FirmwareFlasher** (`RecoveryBoot/include/FirmwareFlasher.h:6-56`): streams
  an SD `.bin` into the next OTA slot. It validates magic, chip id, segments, XOR checksum,
  SHA-256 trailer and size first.
- **CrossInk's SD picker**: Up+Power held at wake → `recoveryFirmwareMode`
  (`crossink/src/main.cpp:1275`, `:1381`, `:1495`). The picker is a file browser filtered to
  `.bin` (`src/activities/settings/SdFirmwareUpdateActivity.cpp:27-29`). It writes to
  `esp_ota_get_next_update_partition` (`src/network/FirmwareFlasher.cpp:267,328`), i.e. the
  slot CrossInk is *not* running from.
- **Board tag**: `FirmwareFlasher.cpp:194` rejects images whose
  `CROSSPOINT-BOARD-V1:<name>;` tag mismatches (`FirmwareBoardTag.cpp:46`). Whether an
  *untagged* image is accepted looks like yes from the scanner logic, but that's
  **unverified on device**. Embed `CROSSPOINT-BOARD-V1:x4;` and skip the question.
- **CrossInk OTA** is pinned to `api.github.com/repos/uxjulia/CrossInk/releases/latest`
  (`src/network/OtaUpdater.cpp:31`). It can't deliver our image, and if run it overwrites
  whatever is in the other slot.
- CrossInk has no "boot the other slot" menu. `OtaBootSwitch` is only used by the flasher.

## 6. Partition layout (identical in CrossPoint, CrossInk, Escape Hatch, OpenX4 sample)

`crossink/partitions.csv`: nvs 0x9000 (0x5000), otadata 0xe000 (0x2000),
app0 0x10000 (0x640000), app1 0x650000 (0x640000), spiffs 0xc90000 (0x360000),
coredump 0xff0000. Each app slot is 6.25 MiB.

Prebuilt otadata blobs, decoded with `tools/inspect_x4_image.py` (they're correct):
- `sample-firmware/otadata_boot_app0.bin` → seq 5 / 4, both VALID → **ota_0**
- `sample-firmware/otadata_boot_app1.bin` → seq 6 / 4 → **ota_1**
- PlatformIO's `boot_app0.bin` → erased/UNDEFINED → ota_0. ⚠ `pio run -t upload` writes
  this blob *and* the app at 0x10000. It silently replaces whatever is in ota_0 and
  re-selects it.

## 7. Sizes (measured)

| image | flash | static RAM | notes |
|---|---|---|---|
| CrossInk 1.5.1 release | 6,047,504 B | DRAM seg 0x4E80 | **97% of a 6.25 MiB slot** |
| CrossPoint 1.6.5 x3-x4 | 5,589,776 B | — | release asset size |
| Escape Hatch (FreeInkUI, SD, display, input, battery) | 488,968 B | 19,500 B | built here |
| Escape Hatch + WiFi STA + HTTPClient + Preferences + 48 KB frame buffer | 1,276,531 B | 90,820 B | size probe, built here, never flashed |

The Wi-Fi stack costs ~790 KB of flash. The 48 KB static frame buffer accounts for most of
the RAM delta, and it's only needed for streaming into the panel; it can be dropped in favour
of the display's own buffer. CrossPoint's own docs budget the C3 heap at "~380KB total"
(`crosspoint-reader/docs/sd-plugins.md:271`).

## 8. CrossPoint extension surfaces (evaluated for Option A)

- SD plugins (`crosspoint-reader/docs/sd-plugins.md`): declarative `device.json` catalogs +
  browser `plugin.js`. No code runs on the device. It's built for book catalogs.
- Plugin events (`crosspoint-reader/docs/plugin-events.md:40-58,107-160`): a whitelist of
  `reader.exit`, `reader.session`, `sleep.enter`… each with an HTTP `request` or `download`
  handler. **`sleep.enter` + `download` → `/sleep.bmp` is a real zero-firmware path to put
  an Alicenet-rendered image on the sleep screen.** It refreshes only when the user puts the
  reader to sleep. There's no button→event, no wake-to-poll, and it requires 20% battery.
- This plugin surface is upstream CrossPoint (1.6.x). **CrossInk 1.5.1 has no
  `activities/plugins/` directory** (checked). Using it means switching to upstream
  CrossPoint.

## 9. OpenX4 community-sdk / sample-firmware

- community-sdk: 27 commits, last 2026-04-25. libs: EInkDisplay (SSD1677 only),
  BatteryMonitor, InputManager, SDCardManager. No power/sleep, no recovery, no UC8179.
- sample-firmware: 23 commits, last 2025-12-06. It's a demo that depends on community-sdk.
- freeink-sdk is the living descendant: 656 commits, last 2026-10-02. It covers the same libs
  plus PowerManager, RecoveryBoot, BoardConfig with multi-controller probing, FreeInkUI,
  XteinkDetect and SecureNet. CrossPoint and Escape Hatch both build on it.
- Verdict: **use freeink-sdk, not community-sdk**. The OpenX4 SDK is superseded rather than
  immature.

## 10. CrossPoint simulator

`crosspoint-simulator/CLAUDE.md` and `src/`: compiles a CrossPoint-HAL firmware natively
with SDL2, a curl-backed HTTP stub (`SimHttpFetch.h`), WebServer shims on :8080, scripted
input (`CROSSPOINT_SIM_INPUT_SCRIPT`), screenshots, and a sleep/relaunch loop. It requires
the firmware to expose CrossPoint's `Hal*` classes exactly ("HAL stub rule").
- Useful for options A/B with almost no work.
- For option C it only helps if we mirror CrossPoint's HAL surface. The vertical slice
  doesn't; it tests protocol and gateway with a fake device instead (see the plan).
- It can't test the things that matter most here: latch/sleep current, Wi-Fi association
  time, ghosting, and the controller variant.

## 11. Commits inspected

- crosspoint-reader `1f77b83` (2026-10-02, v1.6.5)
- crossink `v1.5.1` = `4f2da6b7` (2026-09-11; release commit `9656361d` is `v1.5.1~1`)
- freeink-sdk HEAD `6993701` (2026-10-02); CrossPoint pins `bbd528ce`
- escape-hatch `122aaa5` (2026-09-28)
- community-sdk `7d86603` (2026-04-25)
- sample-firmware `b7ff070` (2025-12-06)
- crosspoint-simulator `62ec1ec` (2026-10-02)
- yanganto/xteink-x4 hardware doc (pin map: SPI 8/10/21, DC4 RST5 BUSY6, SD CS12 MISO7,
  ADC GPIO1/GPIO2, Power GPIO3, battery ADC GPIO0, USB detect GPIO20; 650 mAh)
