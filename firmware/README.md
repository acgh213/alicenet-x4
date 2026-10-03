# alicenet-x4 firmware

## Build

`pio run -e x4` builds the Xteink X4 image. The SDK is pinned to the Escape Hatch
revision that ran on this unit (`e41f683`). `pio run -t upload` is intentionally
refused: the serial upload address is `ota_0`, which is CrossInk on this device.

## Install

Copy the generated `.bin` to the SD card root and install it from CrossInk's
**Settings -> SD Firmware Update**. The CrossInk board tag is embedded and the
image is validated with `tools/validate_sd_image.py` before delivery.

## Runtime contract

- Mounts the SD card read-only for `/.crosspoint/wifi.json`, using CrossInk's
  MAC-bound credential format; it does not ask for or rewrite the Wi-Fi password.
- Joins the saved network, pulls `GET /x4/v1/frame` from the configured gateway,
  validates an 800x480 P4 PBM, and refreshes the SSD1677.
- Posts button events to `POST /x4/v1/events`; boot and sequence counters plus
  the ETag persist in the `alicenet` NVS namespace.
- Sleeps after a card with no assigned actions, or holds a 30-second interaction
  session for cards with actions. The gateway's `X-Next-Poll` arms the timer wake.
- Back+Up recovery remains the stock SDK path to ota_0/CrossInk.

The local deployment header `src/x4_secrets.h` is intentionally not committed.
It contains the gateway address and per-device bearer token.
