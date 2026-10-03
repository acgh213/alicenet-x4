# Architecture — Xteink X4 as an Alicenet endpoint

Evidence and citations are in [research-notes.md](research-notes.md).

## The two facts that decide this

1. **On battery, every existing X4 firmware's "sleep" is a power-off.** GPIO13 drives the
   battery MOSFET, and the sleep path pulls it LOW. The RTC is unpowered and the timer can't
   wake the device; only the power button, which physically bridges the rail, can. An ambient
   terminal that refreshes on its own therefore needs a *different sleep mode* from anything
   CrossPoint/CrossInk/stock do: hold the latch HIGH and take a real ESP32 timer deep sleep.
   Nobody in these repos has measured that floor current.
2. **The X4 can't usefully receive unsolicited LAN traffic on battery.** Staying associated
   (modem sleep) costs tens of mA, i.e. days of battery rather than weeks. So the device
   **pulls**: on a timer, on a power-button wake, and in a short interactive window after a
   press. "Push" becomes "the gateway answers the next pull, or answers a held long-poll
   immediately while a session is open."

Both point the same way: the hard part is power/wake policy plus a dumb render loop, which
is exactly the part the reader stack doesn't help with.

## Shape regardless of option: clockctl is the surface, the X4 is a dumb frame client

```
 Muse/Alice ──clockctl──▶ x4d (HTTP, on Alicenet) ──GET frame (PBM)──▶ X4
                 ▲                │  render 800x480 server-side (Pillow,
                 │                │  same complete-fit-or-reject rules)
 musegadget  ◀───┴── events ◀─────┴──POST events (JSON)──────────────── X4
 send-user-msg
```

- **All layout happens upstream.** x4d renders 800×480 1-bit frames with Pillow, just as
  `clock_render.py` does at 250×122. The device never parses cards and never handles fonts,
  wrapping or Unicode. That's what keeps the firmware small and lets the card vocabulary grow
  without reflashing.
- **clockctl grows a target, not a fork.** The X4 reuses the existing model of slides, stable
  IDs, owner/kind/expiry/stale, rotate/next/hold intents and avatars; at 800×480 the limits
  are simply larger. The intended direction: `clockctl --target x4 slide …` → x4d, with
  `clock_protocol.validate` reused and X4 limits/geometry in its capabilities. The Pi Zero
  stays exactly as it is. The deck lives in x4d's SQLite, because a device that's off most of
  the time can't own the carousel the way the clock Pi does.
- **Button events go back through the seam clockctl already uses.** `clock_power_relay.py`
  forwards power warnings with `musegadget send-user-msg`. x4d does the same for X4 events.
  Navigation (next/prev card) is resolved inside x4d without bothering Alice; only
  meaningful events (acknowledge, context actions) are forwarded.

## Option comparison

### 1. CrossPoint plugin/feature (upstream CrossPoint 1.6.x, SD `device.json` events)

- **Inherit:** everything in the reader. A working zero-firmware path: `sleep.enter` →
  `download` `{x4d}/sleep.bmp` → the CUSTOM sleep screen shows an Alicenet-rendered image.
- **Build:** a `device.json`, and a BMP endpoint on x4d. That's all.
- **Can't do:** wake on a timer, deliver button events, any interaction, or refresh the screen
  on its own. It updates only when *you* put the reader to sleep, and only at ≥20% battery
  with a 10-second join budget.
- **Also:** the plugin surface is upstream CrossPoint; CrossInk 1.5.1 doesn't have it. You'd
  give up CrossInk or juggle two reader firmwares.
- **Complexity:** trivial. **Memory/flash:** none of ours. **Battery:** identical to the reader.
- **Maintenance:** none, but we're bound by a whitelist designed for book services.
- **Recovery:** unchanged.
- **Suitability:** a "your next sleep screen is Alice's briefing" toy. It covers capabilities
  1–4 only, and 4 only incidentally. **Not the endpoint.**

### 2. CrossInk/CrossPoint fork with the reader retained, plus an Alicenet mode

- **Inherit:** Wi-Fi provisioning UI and credential store, HTTP downloader, HAL, display with
  controller probing, input with chords, battery, SD, web server, simulator compatibility,
  SD flasher, the reader.
- **Build:** an Alicenet activity; a second sleep path (latch held + timer wake) carved through
  a power manager written around power-off; wake-reason routing in an already complex
  `main.cpp` boot sequence (Quick Resume, Network resume, recovery mode, splash policy…); the
  protocol client; the status overlay.
- **Complexity:** medium-high. The code we'd add is small. The difficulty is that it has to
  thread through sleep/boot logic that ~1,500 commits have tuned for a different job, and
  the reader's assumption that "asleep = off" sits in many places.
- **Memory/flash:** CrossInk 1.5.1 is **6,047,504 B in a 6,553,600 B slot (97%)**; CrossPoint
  1.6.5 is 5.59 MB. There's room for a client, but not much. The heap is shared with a reader
  that already fights fragmentation (their own docs budget ~380 KB).
- **Battery:** sleep must change for *both* modes or become mode-dependent. Every regression
  there costs you reading battery as well.
- **Maintenance:** high. CrossInk ships frequently, and every rebase touches `main.cpp` and the
  power manager, which are exactly the files we'd modify. The simulator HAL stub rule means
  more upkeep.
- **Recovery:** fine (same partitions, same SD flasher). But reader and agent fail together:
  one bad build takes both down.
- **Suitability:** workable, but you'd be running an e-reader that also does Alicenet, which
  is the framing you asked me to challenge.

### 3. Purpose-built Alicenet firmware on freeink-sdk (Escape Hatch as template)

- **Inherit (SDK libs, already used by CrossPoint and Escape Hatch):**
  - BoardConfig with X4 pins/latch and SSD1677/UC8179/UC8279 probing
  - FreeInkDisplay (FULL/HALF/FAST, controller deep sleep)
  - InputManager (ADC ladders, held time, edge queue)
  - PowerManager, BatteryMonitor, SDCardManager
  - RecoveryBoot (boot-combo slot switch + validated SD flasher)
  - FreeInkUI if we ever want it
  - Escape Hatch's build/partition/size-patching setup
- **Build:** Wi-Fi join with cached BSSID/channel; an HTTP client for two endpoints; PBM blit;
  an event queue in NVS; a wake/sleep policy; a status strip; SD provisioning. Realistically
  **~800–1,200 lines** for the slice.
- **Complexity:** low-medium, and nearly all of it is *our* logic. The parts that would be hard
  from scratch (panel controller variants, ADC ladder thresholds, latch semantics, flashing)
  come from the SDK.
- **Memory/flash (measured):** Escape Hatch alone is 489 KB flash / 19.5 KB static RAM. With
  Wi-Fi STA, HTTPClient, Preferences and a 48 KB frame buffer forced in, it's
  **1.28 MB flash (19.5% of a slot) / 90.8 KB static RAM**. That leaves ~5 MB flash and
  >200 KB heap. No reader is competing for the heap.
- **Battery:** we own the sleep policy outright. There are two explicit modes: "pocket"
  (latch dropped, identical to the reader's off state) and "ambient" (latch held, timer wake).
- **Maintenance:** we pin a freeink-sdk commit and bump it deliberately; the SDK is actively
  maintained, with 656 commits as of 2026-10-02. No downstream-fork rebases.
- **Recovery:** the best of the three, see the recommendation. It **sits next to CrossInk
  rather than replacing it**.
- **Suitability:** a direct fit for capabilities 1–12, and it stays small.

The OpenX4 community-sdk / sample-firmware isn't a separate contender. It's the older ancestor
of freeink-sdk (SSD1677-only display, no power/recovery libs, last touched 2025-12 / 2026-04).
If your X4 has a UC8179 panel, community-sdk simply wouldn't drive it.

## Recommendation

**Turn the X4 into a dedicated Alicenet appliance (option 3), dual-booted with CrossInk.**

- **ota_0: CrossInk 1.5.1, untouched** (read from the device 2026-10-03: otadata selects
  ota_0). It's your reader *and* your SD flasher.
- **ota_1: alicenet-x4.** This overwrites whatever is left in ota_1, probably the old CrossInk
  1.3.4-tiny. Nothing is lost: that build is on GitHub and in the June backup.
- **Switching:** hold **Back+Up while waking** in alicenet-x4 to get back to CrossInk. Because
  CrossInk sits in ota_0, the SDK's stock `freeink::recovery::checkBootCombo()` does exactly this
  unmodified; no two-slot variant needed. From CrossInk, **Up+Power** opens its SD picker, which
  flashes `alicenet-x4.bin` into ota_1 and boots it.
- **Updating alicenet-x4:** combo into CrossInk → Up+Power → pick the new `.bin`. The flasher
  always targets the slot it isn't running from, which is ours. Before writing, it validates
  checksum, SHA-256, size and board tag. We embed `CROSSPOINT-BOARD-V1:x4;`.
- **USB is the backstop,** never the routine path. Always write explicit offsets; **never** use
  `pio run -t upload`, which rewrites otadata and ota_0.
- **The full backup is the last resort,** not part of each cycle.

Why not keep the reader inside the same image? Because then a bad Alicenet build costs you
the reader, and the reader's sleep design fights the agent's. Dual-boot gives you both
without either knowing about the other.

Two rules this creates:
- **Don't run CrossInk's online OTA while dual-booted.** It installs into the other slot, which
  would overwrite alicenet-x4. Harmless, but surprising.
- **alicenet-x4 won't self-OTA from x4d** in this phase, for the same reason in reverse.
  Updates go via SD. Self-OTA becomes reasonable only if you ever give the device to Alicenet
  full-time, with Escape Hatch in ota_0 as the recovery slot.

Option 1 still makes a nice **Phase 0** if you want an Alicenet image on the screen *today*
with zero flashing. But it needs upstream CrossPoint, not CrossInk, so I wouldn't bother
unless you want to compare the readers anyway.

## Display and UI implications

- **Every wake starts with a HALF or FULL refresh** (~1–2 s flash), because the controller's
  deep sleep discards its RAM. Design for that: one deliberate update per wake, not
  animations. A later optimisation is possible (store the last frame on SD, re-seed RED RAM,
  then FAST-diff), but not in the slice.
- **Within an awake session, use FAST for small changes** (cursor, "sent ✓", page dots). Force
  HALF every N FAST updates, and on every card change. x4d hints via `X-Refresh`.
- **Visual language suited to ghosting:** big type, thick rules, whitespace, no greys, no
  dithering in the slice. Keep a stable chrome position (header band, footer band, status
  corner) so successive cards overwrite in the same places and ghosts land on ghosts.
- **The status corner is device-owned** (top-right, 160×28), as on the clock Pi. x4d leaves it
  white and the device draws battery and sync state. No timestamps: after a power-off the
  device doesn't know the time, so it reports "sync ok" / "sync ✗ ×3" instead of lying about
  age.

## Push vs poll, concretely

| state | radio | wake source | cost driver |
|---|---|---|---|
| **pocket** (latch LOW) | off | power button only | ~0, equals the reader's "off" |
| **ambient** (latch HIGH, timer deep sleep) | off | timer (x4d's `next_poll_s`, clamped 5 min–6 h) or power | board floor (unmeasured) + Wi-Fi join per wake |
| **session** (awake ≤60 s after a press) | on | buttons, long-poll | ~80–120 mA while awake |

Rough budget (**estimates until measured**):
- A wake with a cached BSSID/channel and static IP is ~2–4 s of radio, about **0.1–0.25 mAh**.
- A 15-minute poll is 96 wakes/day, ≈ 10–24 mAh/day.
- If the latched floor is ~50 µA, add ~1.2 mAh/day: **~4–8 weeks on 650 mAh**. If the board
  floor is ~1 mA (LDO + dividers), add 24 mAh/day: **~2–3 weeks**.

The floor is the number we need first.

Unsolicited push stays off the table. Light sleep with Wi-Fi kept alive is the only way, it's
fiddly on the C3, and it still costs mA-class average current.

## Buttons

| button | in a session | notes |
|---|---|---|
| Power short | wake / refresh now | the only button that wakes the device |
| Power long (≥2 s) | sleep now | device-local |
| Left / Right | previous / next card | resolved in x4d, not forwarded to Alice |
| Confirm | acknowledge / primary action | forwarded |
| Confirm long | secondary action | forwarded |
| Back | dismiss / home card | forwarded only if the card asks |
| Up / Down | context choice (A/B), scroll | card-defined |
| **Back+Up held during wake** | **boot the other slot (CrossInk)** | cross-ladder chord, device-local |

Long press: yes, via `getHeldTime`. Double press: possible, but it adds ~300 ms to every single
press, so not in the slice. Chords only across ladders (Back/Confirm/Left/Right on GPIO1,
Up/Down on GPIO2).
