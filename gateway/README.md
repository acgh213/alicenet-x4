# gateway: x4d, x4ctl and the fake device

This is step 1 of `docs/vertical-slice-plan.md`: everything on the Alicenet side, plus a stand-in
device. No hardware is involved. Stdlib + Pillow, Python ≥ 3.11.

- `x4_protocol.py`: the card contract. It follows clockctl's slide shape with 800×480 limits
  and adds per-card `actions`.
- `x4_render.py`: renders a card to a 1-bit 800×480 image and packs it into a P4 PBM.
- `x4_store.py`: SQLite store for the deck, rotation/hold, devices, and button events (dedupe + forward state).
- `x4d.py`: the HTTP gateway. The device pulls frames and posts events; agents publish cards.
  The wire spec is `docs/protocol.md`.
- `x4ctl.py`: the agent CLI, with clockctl's verbs.
- `fake_x4.py`: runs the firmware's wake sequence, using a JSON file as its NVS and PNGs as its panel.

## For agents: publishing a card

```sh
x4ctl slide --id alice.brief --owner alice --avatar alice --title "Saturday evening" \
  "Clear tonight, low 48°F.\nTomorrow: sun, high 63°F."
```

- **Line breaks**: in TEXT, a literal `\n` becomes a break, so it never shows up on screen. `--stdin`
  takes real newlines. `--line` (repeatable) is literal, which is useful when a line really contains a
  backslash.
- **Avatar**: `none`, `alice`, `pyrrha`, `muse`, or any local PNG/JPEG. Images are
  downscaled to 32×32 1-bit, transparency counts as white, and they're drawn at 96×96.
- **Buttons**: `--action confirm=yes --action down=later`. You can assign `confirm`,
  `confirm_long`, `back`, `up` and `down`. A press arrives in Muse as
  `[x4] Cassie pressed confirm (short) = 'yes' on 'alice.q'`. Left/Right always page the deck
  and are never forwarded. A card with actions keeps the device awake for 30 s after drawing.
- **Delivery is honest**: `"delivery": "next_wake"` means the card is stored and will be shown
  the next time the X4 wakes. That's up to `poll_s` (default 30 min) unless someone presses a
  button. Use `--intent next` to be shown first at the next wake, or `--intent hold --hold-s N`
  to pin a card.
- **Too much text is an error, not clipping**: the body shrinks from 40 px to 18 px to fit, and
  if it still doesn't fit, the publish fails with a message telling you to shorten it.
- `x4ctl preview --output p.png < request.json` renders locally without touching x4d.
- `x4ctl status` shows device battery, RSSI, last wake and pending forwards. Use it to tell
  whether the X4 is actually alive.

## Run the tests

```sh
python3 -W error::ResourceWarning -m unittest test_x4ctl test_fake_x4 test_x4d test_x4_store test_x4_render test_x4_protocol
```

## Run it for real (once there's a device)

1. Copy `x4d.example.json` to `~/.config/x4d/x4d.json`. Generate two tokens with
   `python3 -c 'import secrets; print(secrets.token_hex(32))'`, and store **only their sha256**
   in the config.
2. Put the agent token in `~/.config/x4ctl/config.json`. The device token goes in the SD
   `/alicenet/device.json` file (see `docs/protocol.md` § Provisioning).
3. `cp x4d.service ~/.config/systemd/user/ && systemctl --user enable --now x4d`.
