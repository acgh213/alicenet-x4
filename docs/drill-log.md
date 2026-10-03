# Drill log

## 2026-10-03: Escape Hatch drill (runbook: `drill-runbook.md`)

Image: `escape-hatch-x4.bin`, sha256 `d9ac08f8…4ce4` (upstream `122aaa5`, freeink-sdk `e41f683`).

| Step | Result |
| --- | --- |
| 1 Reject corrupt image | _not yet reported_ |
| 2 Flash Escape Hatch from CrossInk SD menu | **PASS**: booted to the Escape Hatch menu |
| 3 Readings | **PASS**: see below |
| 4 Back+Up → CrossInk | _pending_ |
| 5 Re-flash → Boot Other Slot → CrossInk | _pending (optional)_ |

### Readings (Cassie, from the Escape Hatch screens)

**Hardware Detect**
- Profile `xteink_x4`. I²C fingerprint: X4 confirmed (0/3 + 0/3 X3-only chips: gauge, RTC, IMU).
- Panel controller: **Default (assumed)**. VER `FF FF FF FF FF`, FLG `FF`.
- Interpretation: both UC81xx probe passes failed and FLG `FF` is a floating line, so no
  UltraChip part answered (`XteinkDetect.cpp:264-338`: both-fail → `PrimaryAssumed`; the
  UC8279d fallback needs a *driven* FLG). So this is the original **SSD1677**. The
  positive evidence is that Escape Hatch drew every screen correctly with the SSD1677 driver.
  → The firmware needs **no UC8179 path**; use the SDK default.

**Button Test** (raw ADC; SDK thresholds from `InputManager.cpp`: ladder 1
`3900 > Back > 3100 > Confirm > 2090 > Left > 750 > Right`; ladder 2 `3900 > Up > 1120 > Down`)

| Button | This unit | SDK average | Margin to nearest threshold |
| --- | --- | --- | --- |
| Back | 3423 / 3431 | 3512 | 323 (to 3100) |
| Confirm | 2620 | 2694 | 480 (to 3100) |
| Left | 1455 | 1493 | 635 (to 2090) |
| Right | 4 / 5 | 5 | 745 |
| Up | 2171 | 2242 | 1051 (to 1120) |
| Down | 4 | 5 | 1116 |
| Power | 4095 | n/a | separate digital GPIO, not on a ladder |

The unit reads ~2–3 % low across the board. That's consistent, and every button sits well
inside the SDK's window, so **stock thresholds are fine**.

**Battery Info**: 100 %, 4.15 V (4146 mV), no charge-detect pin.

**EFuse / Security**: secure boot off, flash encryption off, serial download enabled,
USB-JTAG enabled, pad JTAG enabled, chip v0.4. "Bootloader writable; serial download still
available as a recovery path." Matches Eido's espefuse read.
