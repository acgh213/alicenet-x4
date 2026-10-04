# Ambient release handoff — 0.3.1-ambient

## Delivered software

- Bespoke Home/Weather/Agenda, 800×480 one-bit dashboard; source data comes from
  the exact configured clock weather and consented calendar feeds, not agent cards.
- `clockctl x4` deployed and exercised on Alicenet; Pi Zero endpoint unchanged.
- Gateway and ten-minute source timer are enabled/active; source refresh was
  exercised from Alicenet and read back as a recent, available, provenance-tagged
  weather/calendar snapshot. Snapshot and context DB are mode0600.
- Earlier actual X4 short Confirm on `network-smoke` is recorded `forward: sent`.
  This is Muse provider acceptance, not proof of a user seeing/executing a message.
- Firmware adds ambient image-preserving sleep, GPIO13 HIGH timer retention,
  waking-Power suppression, 700ms Confirm distinction, 120-second activity window,
  five-second conditional pulls during sessions, bounded HTTP, staged PBM updates.
- Review fixes: decode CrossInk saved Wi-Fi after MAC XOR; reserve/persist distinct
  event identity before any network attempt; consent-aware cache re-projection,
  source-change invalidation, partial-consent event retention; matching displayed
  page context captured atomically with accepted events and retained for retries.
  Calendar sorting/expired filtering/pagination is shared with briefing selection.
- Failed firmware POSTs are not automatically retried and there is no device
  outbox. Ambiguous/lost acknowledgements cannot reuse identity for later presses.

## Exact install artifact

`artifacts/network/alicenet-x4-0.3.1-ambient.bin`

- Size: **1,094,672 bytes**.
- SHA256: `6362561bb4b6f5a84267db6e206023b6ecdf74da604caa54a04f88c09367d7b0`.
- Parent firmware commit: `c0a6101` (isolated implementation `00e752d`).
- Parent build succeeded and exact copied artifact validated: x4 tag, six segments.
- Binary contains local device provisioning; share only through the established
  private handoff, not public artifact hosting. Secret header remains gitignored.

Install through CrossInk **Settings → SD Firmware Update** only. Full backup and
restore procedure remain the established runbook; no new recovery research needed.
Never use serial PlatformIO upload or CrossInk online OTA in this dual-boot layout.

## Verification

- 131 gateway tests passed with ResourceWarning treated as errors.
- Six C++ firmware helper suites passed, including credential and lost-ACK cases.
- Ten Python firmware wiring/event-body contract tests passed.
- Fourteen SD validator/Wi-Fi compatibility tool tests passed.
- Eight clockctl dispatcher/carousel tests passed; actual remote home/glance/status/
  refresh commands exercised, with deployed clockctl/doc hashes matching local.
- Actual source previews generated privately and Home inspected for layout.
- `git diff --check` clean. Only intentionally untracked SDK checkout remains.

## Physical acceptance and remaining characterization

The [2026-10-03 ambient drill](drill-log.md#2026-10-03-ambient-dashboard-navigation)
records `0.3.1-ambient` on the physical X4. Cassie verified Left/Right navigation,
Back → Home, short Confirm → Muse, long Confirm → local refresh, ambient panel
retention, and the timer/Power wake/latch loop. Photographs confirmed real-source
Home, Weather and Agenda rendering. These supersede the original pre-install
handoff's `0.2.4-net` readback and pending-install language.

This is functional hardware acceptance, not a battery-current measurement or a
calibrated test of the 700 ms boundary. Battery sleep current, repeated-cycle
endurance, exact timing under display/network delays, and interrupted-download
fault characterization remain unmeasured. Recovery was established in earlier
drills. See [firmware/README.md](../firmware/README.md) for the remaining targeted
checks; host tests do not establish those measurements.

The gateway-only Work rollout requires no new firmware installation. Its service
frame readback is not physical Work button/panel acceptance.

## Relevant commits

- Renderer: `95e468a`.
- Ambient firmware: `219a589`.
- Glance/source/Muse integration: `1cb0a31`.
- Consent/displayed-context corrections: `04b7a6a`.
- Clockctl target dispatcher: clock-control `36a1ed1`.
- Corrected firmware: `c0a6101`.
