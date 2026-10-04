# x4 protocol v1

Boring on purpose: HTTP/1.1 over the LAN, one long-lived bearer token, a bit-packed PBM frame
in one direction and JSON events in the other. The device only ever *initiates*
connections, so it never needs a listener. Inspect it with `curl` and look at it with any
image viewer.

## Roles

- **x4d** runs on Alicenet. It owns the deck (SQLite), renders frames, accepts events and
  forwards meaningful ones to Muse with `musegadget send-user-msg`. It listens on the LAN
  only, port 8787.
- **device**: alicenet-x4 firmware. It is stateless except for: Wi-Fi credentials, the token,
  the gateway URL, the last frame ETag, and an outbound event queue (all in NVS).

## Auth

- Each device has one 32-byte random token, provisioned through the SD card (below) and stored
  in NVS. Header: `Authorization: Bearer <token>`. x4d keeps `sha256(token)` per device id.
- That's it. No TLS on the home LAN for v1; the token mainly stops other LAN devices from
  posting events or scraping cards. Re-provision to rotate it. x4d binds to the LAN interface
  only and refuses requests from outside 192.168.18.0/24.
- What it doesn't protect against: someone sniffing your Wi-Fi. If that ever matters, put
  x4d behind TLS with a pinned cert. ESP32-C3 HTTPS works but costs ~40 KB heap and ~1 s per
  handshake per wake, so it's deferred.

## Endpoints

### `GET /x4/v1/frame`

Request headers:
- `Authorization: Bearer …`
- `X-Device: x4-01`
- `X-Wake: timer|button|boot|session`
- `X-Battery: 3.92V,78`: raw voltage and the SDK's percent estimate
- `X-Rssi: -61`
- `X-Fw: alicenet-x4/0.1.0`
- `If-None-Match: "<etag>"`

Responses:
- `304 Not Modified`: keep the current screen. **No refresh at all, which is the common case.**
- `200 OK`, `Content-Type: image/x-portable-bitmap`: a **P4 PBM, 800×480 exactly**, 48,000-byte
  raster after the header. PBM bit 1 = black; the device inverts into FreeInkDisplay's
  1 = white buffer. Headers:
  - `ETag: "<sha256 of the PBM, 16 hex>"`: content-addressed, so the staleness marker or a
    re-render changes it but an identical republish doesn't
  - `X-Refresh: full|half|fast`: a hint; the device may upgrade it (first frame after wake is
    always ≥ half)
  - `X-Card: weather.today`: the current card id, echoed back with events. Life uses
    `life.<semantic fingerprint>` so identical pixels cannot rebind a different
    note ID/content/revision or calendar context. A 304 still supplies the current
    card; the displayed PBM's ETag remains content-addressed.
  - `X-Card-Actions: confirm,confirm_long,down`: buttons this card assigned (subset of
    `confirm, confirm_long, back, up, down`; Left/Right always page the deck)
  - `X-Next-Poll: 900`: seconds; the device clamps it to [300, 21600]
  - `X-Session: 30`: stay awake N seconds after rendering (0 = sleep immediately). x4d sends
    `session_s` for cards that assigned any action, 0 otherwise.
- `204 No Content`: nothing to show yet (fresh device). Keep the status screen.
- `401`: bad token. The device shows a local "not provisioned" screen and drops to pocket mode.
- Any other error or timeout: keep the screen, mark "sync ✗", back off (×2 up to 6 h).

The device streams the 48,000 bytes straight into the display buffer and verifies the
length. A short read or bad header leaves the old screen untouched. **It never draws a
partial frame.**

### `POST /x4/v1/events`

Body (≤ 2 KB):

```json
{"device":"x4-01","boot":17,"events":[
  {"seq":41,"card":"weather.today","etag":"r12","button":"confirm","press":"short","wake":"button"}
]}
```

- `seq` increases monotonically per device and is persisted in NVS. `(device, boot, seq)` is the
  idempotency key, so x4d drops duplicates.
- The device keeps at most 16 unsent events. If the queue overflows, the oldest are dropped
  (the same policy as clock-power-relay).
- `200 {"acked": 41}` → the device discards everything ≤ 41. Delivery is at-least-once.
- In a session, the response may be `200` with `X-Frame-Changed: 1`. The device then
  immediately `GET`s the frame (with FAST/HALF per `X-Refresh`) instead of waiting for the
  next poll. That's how "press Right → next card" feels responsive.

### `GET /x4/v1/frame?wait=25` (session only)

A long-poll: x4d holds the request up to 25 s and returns as soon as the card changes (for
example, Alice answers an ack with a follow-up card). The device only uses this while awake
in a session window. It is the closest thing to push the device gets, and it's cheap because
the radio is already up.

## Wake sequence (device)

1. `checkBootCombo` (Back+Up → ota_0, i.e. CrossInk). Then `holdPowerRails()`.
2. Read the wake cause and battery. If below 3.45 V: draw "low battery, Alicenet paused" once
   (if not already drawn), enter pocket mode, stop.
3. Wi-Fi join using the cached BSSID/channel, with an 8 s deadline. On failure, increment
   `fail_count`, update the status corner with a FAST refresh **only if its text changed**, and
   sleep with backoff.
4. POST queued events (if any), then GET frame with If-None-Match.
5. Render if 200. Sleep for `X-Next-Poll`, or stay awake for `X-Session` / after a button wake.
6. A **hard awake watchdog of 90 s**: whatever happens, sleep afterwards. That guards the
   #1263 failure mode.

## Provisioning (SD card, one-time)

`/alicenet/device.json` on the SD card:

```json
{"ssid":"…","psk":"…","gateway":"http://192.168.18.x:8787","device":"x4-01","token":"…"}
```

On boot, if the file exists: copy it into NVS, **overwrite the file with zeros and delete
it**, then show "provisioned x4-01". CrossInk stores its own Wi-Fi credentials in
`/.crosspoint/wifi.json`/`.bin`; we leave those alone.

## clockctl integration (Alicenet side)

- `clockctl --target x4 slide …`, `slides`, `slide-get`, `slide-remove`, `preview` reuse
  `clock_protocol` / `clock_slides` validation. x4d advertises its own `capabilities()`:
  geometry 800×480, larger limits, and `wake_model: "pull"`, `min_poll_s: 300`,
  `buttons: […]`.
- **New fields an agent must understand:** `actions` on a slide (which buttons mean what) and
  `"delivery": "next_wake"`. Being honest about latency is part of the contract: publishing
  to the X4 means "shown at the device's next wake", not now. `status` reports
  `last_seen`, `last_wake`, `battery` (as reported by the device, stale-flagged), and
  `pending_events`.
- Agents publish with `x4ctl` (`gateway/x4ctl.py`), which talks to `POST /x4/v1/agent`
  (separate agent token). Only presses of a button the card assigned are forwarded, as
  `[x4] Cassie pressed confirm (short) = 'yes' on 'alice.q'`.
  Left/Right/Back navigation stays inside x4d.
