# alicenet-x4 firmware — 0.3.1-ambient

## Build and install safely

```sh
PATH="$HOME/.platformio/penv/bin:$PATH" pio run -e x4
# From the repository root:
python3 tools/validate_sd_image.py firmware/.pio/build/x4/firmware.bin --board x4
```

The SDK is pinned to the Escape Hatch revision that ran on this unit (`e41f683`).
**Never upload over serial.** The custom upload command still refuses, because
serial upload would overwrite `ota_0`/CrossInk. Copy the validated app `.bin` to
the SD card root and use CrossInk **Settings -> SD Firmware Update**, which writes
the other slot. Partitions, board tag and stock SDK Back+Up recovery are unchanged.

## Runtime contract

- Reads `/.crosspoint/wifi.json` without writing it, including CrossInk's MAC-bound
  credential format. `WiFi.persistent(false)` and `disconnect(true, false)` preserve
  Wi-Fi credentials. Only boot/sequence counters and the `had-frame` marker live
  in the `alicenet` Preferences namespace; there is no NVS erase/clear operation.
- Wake names are `timer`, `button`, or `boot`. Session GETs use `session`; events
  use `button`. The gateway owns `X-Session` (home screens should supply `120`).
  Each press restarts the full assigned inactivity window, and periodic responses
  with unchanged session duration do not extend it. Remote duration is capped at
  600 seconds. Held buttons prevent ambient sleep until released.
- Confirm sends one classification per hold: short on release below 700 ms,
  long at/after 700 ms, never both. Other navigation buttons send short events.
  A dedicated consumer drains SDK `popPress()` and samples button levels every
  10 ms; SDK `beginAsync()` remains the only caller of `update()`. It starts
  before any splash refresh, and continues through Wi-Fi, HTTP and display work.
  Sampling/debounce introduces timing quantization; physical boundary tests remain.
- The initial Power hold is ignored until released. A new 1.5-second Power hold
  requests manual off. Manual off draws the off screen, drops GPIO13 LOW, and
  arms only Power wake (no timer). Ambient/session-complete/offline sleep does
  **not redraw** the panel, holds GPIO13 HIGH, and arms the gateway's
  `X-Next-Poll` timer (clamped to 300–21600 seconds) plus Power wake. SDK rail
  parking and sleep-abort recovery are retained. On wake, SDK `holdPowerRails()`
  releases the pad holds before asserting the latch; global deep-sleep hold is
  disabled afterward.
- During a session, ordinary GETs run about every five seconds, with
  `If-None-Match` after a successful frame in this wake. No long-poll is used.
  Connection is capped at 800 ms; one 3000 ms deadline covers connect, nonblocking
  request writes and response reads. Power-off cancels write/read waits. Gateway
  configuration must contain a **literal IP address**; DNS is rejected because
  the Arduino resolver has no bounded deadline. Display refresh and initial
  Wi-Fi join remain blocking in the main task; input classification is separate.
- A complete, exact-size 800×480 P4 PBM is staged before replacing the framebuffer.
  The first successful frame in each wake uses FULL refresh; later ones use HALF.
  A splash is shown only while there has never been a successful ambient frame.
  No framebuffer is retained across wake, so the first GET is unconditional.
  `304` is accepted only for a frame authenticated in this wake. Offline/failed
  fetches leave the old physical panel alone, log that it may be stale, and do
  not draw receipts or submit actions against an unknown current-wake card.
- Classified events use a bounded 32-entry queue. Overflow is logged. Each
  distinct dequeued navigation event reserves and persists a unique sequence
  **before** any network attempt (including offline/card-rejected events).
  A failed or lost-ACK POST burns that sequence; the next press gets a new one,
  never an alias of the event the server may already have committed. Persistence
  failure or counter exhaustion vetoes sending. A failed POST is not retried
  automatically: events can be lost, and an ambiguous timeout can mean the
  action was committed despite an error receipt. This MVP guarantees distinct
  identities, not reliable delivery; it has no persistent event outbox.

`src/x4_secrets.h` is local deployment configuration and is not committed.

## Host verification

From `firmware/` (put test executables in the configured scratch directory):

```sh
for test in ambient bounded_http http_line json_out wifi_password event_sequence; do
  g++ -std=c++17 -Wall -Wextra -Werror -Isrc "test_host/test_${test}.cpp" \
    -o "$TMPDIR/x4-test-$test"
  (cd test_host && "$TMPDIR/x4-test-$test") || exit 1
done
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -m unittest discover -s test_host -p 'test_*.py'
```

The pure helper tests cover Confirm threshold/release/duplicate suppression,
wake Power suppression, inactivity extension/rollover, sleep policy, panel
validity, bounded HTTP reads/writes (stall, EOF, overflow and partial write),
synthetic CrossInk validated credentials (roundtrip, corruption, wrong MAC and
bounds), and durable event allocation (lost ACK then a new press, persistence
failure and exhaustion).
Wiring guards are source checks, not substitutes for device tests.

## Pending physical gates — not run for this release

1. Install using SD updater; verify Back+Up still returns to CrossInk.
2. On **battery without USB**, retain the card at session expiry, measure sleep
   current, and observe an actual timer wake with GPIO13 held HIGH.
3. Verify Power wake does not immediately turn off; a fresh hold turns off on
   battery and wakes correctly on USB. Check latch/reset holds across cycles.
4. Check short/long Confirm around 700 ms and during refresh/HTTP delays: one
   event classification per hold, correct navigation and complete 120-second
   activity extension. Confirm polling does not keep an idle device awake forever.
5. Interrupt Wi-Fi/PBM downloads: preserve the panel, no corrupted partial
   redraws, honest stale-panel logs; recover with a full first refresh per wake.

Host tests and an accepted SD image do **not** establish battery timer wake,
panel fidelity, input timing, heap availability or power consumption on hardware.
