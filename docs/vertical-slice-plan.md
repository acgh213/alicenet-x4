# Vertical slice plan

Target chain:
**clockctl → x4d → Wi-Fi → X4 renders one 800×480 card → Confirm → event to x4d → Muse →
ambient deep sleep → timer/power wake → recovers cleanly (304, no refresh).**

Deliberately boring: no UI framework, no fonts on the device, no animation, no cloud, no
self-OTA, no rebuild of the Pi stack.

## Step 1: x4d + fake device (host only, buildable now)

`~/projects/alicenet-x4/gateway/` (Python stdlib `http.server` + Pillow, under 300 lines per
script):

- `x4d.py`: implements the three endpoints in `protocol.md`. It keeps the deck and the device
  state (last_seen, battery, seq) in SQLite. The token is read from
  `~/.config/alicenet-x4/devices.json` (sha256 only).
- `x4_render.py`: card → 800×480 1-bit PBM. The layout is
  - header band: title and avatar scaled 32→96 px
  - body: up to 12 lines, complete-fit-or-reject, like `clock_render.py`
  - footer: owner@time, plus an action hints row ("◀ prev  ● ack  next ▶")
  - a white status corner reserved for the device

  It shares `clock_protocol.validate` for the card fields.
- `x4sim.py`: a fake device. It runs the wake sequence (GET with ETag → write PNG → optional
  POST event) and is the integration test for everything except radio and panel.
- Events are forwarded through `musegadget send-user-msg`, behind `--dry-run` until step 6.
- Tests:
  - ETag/304
  - 401
  - PBM exactly 48,000 raster bytes
  - duplicate event dedupe
  - poll clamp
  - render-fit rejection
- **Gate:** `x4sim.py` round-trips a card and a Confirm event against a local x4d.

## Step 2: firmware skeleton, "hello" (no network)

`~/projects/alicenet-x4/firmware/`, laid out like Escape Hatch, with freeink-sdk as a submodule
**pinned** to a specific commit.

- `setup()`: `freeink::recovery::checkBootCombo()` (stock SDK; Back+Up → ota_0 = CrossInk) →
  `holdPowerRails()` →
  display init (controller probe) → draw the status screen into the framebuffer with the SDK's
  built-in font → HALF refresh.
- Power long → pocket sleep. This copies CrossInk's latch-LOW path verbatim.
- Embed `CROSSPOINT-BOARD-V1:x4;`, and disable `pio upload`.
- **Log the probed panel controller and the raw ADC ladder values to serial.**
- **Gate:** build size < 1 MB; checklist §E recovery drill passes on hardware.

## Step 3: provision, join, fetch, blit

- SD `/alicenet/device.json` → NVS, then zero and delete the file.
- Wi-Fi STA with cached BSSID/channel; optionally a static IP.
- `GET /frame` with If-None-Match; stream the PBM into the framebuffer, inverting bits; verify
  the length, otherwise keep the old frame.
- Draw the status corner over it: battery %, a Wi-Fi glyph, and ✓/✗.
- **Gate:** a slide published with `clockctl --target x4` appears after a Power wake. Record
  join time and fetch time on serial.

## Step 4: buttons → events, session window

- Map buttons as in `architecture.md`, with `seq` in NVS and a queue of ≤16.
- A 30 s session after any button press (or for as long as `X-Session` asks), using the long-poll
  GET.
- Left/Right render the new card via `X-Frame-Changed` with a FAST refresh, with a HALF refresh
  every 5th.
- **Gate:** Confirm shows up in x4d's events table exactly once; Right advances the card in under
  3 s.

## Step 5: ambient sleep and wake

- `sleepAmbient(next_poll_s)`: display `deepSleep()` → latch held HIGH (`gpio_hold_en`) → timer
  wake + power-button GPIO wake → `esp_deep_sleep_start()`.
- Wake-cause routing: timer → silent fetch (render only on 200); power → fetch, then session.
- The 90 s awake watchdog; the low-battery drop to pocket mode; backoff on failures.
- **Gate:** overnight on battery with a 15 min poll: no missed wakes (x4d last_seen log), no
  hangs, the screen intact at the end, and every 304 wake left the panel untouched.

## Step 6: wire into Alicenet for real

- Install x4d as a user systemd unit on Alicenet, LAN-only.
- Add `clockctl --target x4` and extend MUSE-USAGE.md with the "delivered at next wake" semantics.
- Turn off musegadget `--dry-run`.
- **Gate (the slice):** Alice publishes a briefing → Cassie presses Power → the card renders →
  Confirm → Alice receives the ack → the device sleeps → timer wake → no refresh → Back+Up →
  CrossInk.

## Explicitly later

- RED-RAM seeding for FAST first-frame-after-wake
- SD frame cache for offline browsing of the deck
- HTTPS/TLS
- self-OTA (only if the device becomes Alicenet-only, with Escape Hatch in ota_0)
- grayscale
- double press
- multiple devices

## Measurements that need the physical X4

Already known from Cassie (2026-10-03):
- Ghosting is noticeable in normal CrossInk use. This confirms the design choice of HALF on
  every card change and big, high-contrast layouts. Consider a FULL refresh every N cards or
  once per hour of ambient time.
- CrossInk's Wi-Fi join takes ~6–7 s. That's a scan-and-join; caching BSSID/channel (and a
  static IP) should beat it, but it sets the pessimistic energy figure: ~7 s × ~100 mA ≈
  0.2 mAh per wake.
- Press-and-hold Power wakes the device. The reset button is below the USB port,
  bottom-left.

Still needed:
1. **Panel controller**: SSD1677 / UC8179 / UC8279_X4, from the step 2 serial log.
2. **Deep-sleep floor current with the latch held, on battery.** It decides the poll interval.
   Crude method without opening the case: a build that wakes every 10 min, logs battery mV to
   NVS, runs 48 h, then compare against the drop with the latch released. Better method: an
   inline meter on the battery lead.
3. Wi-Fi join time from deep sleep (cached BSSID vs scan), and awake energy per wake.
4. HALF/FULL refresh duration and visible ghosting after N FAST updates at 800×480.
5. Whether a power-button press wakes the device from latch-held deep sleep on battery
   (the rail is already up, so it should act as a plain GPIO3 wake). This is unproven.
6. This unit's ADC ladder readings vs the SDK's averages.
7. Whether this hardware revision self-latches (BoardConfig mentions a field revision that
   doesn't).
8. Where the reset button is, and how it behaves on USB vs battery.
9. RSSI where the device will actually live.
