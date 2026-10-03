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

## Physical acceptance still pending

The last read-back device version before handoff was `0.2.4-net`; this document
DOES NOT claim the ambient image has been installed or run on the X4 yet.

After installation:
1. Verify Home renders and Left/Right show Weather/Agenda; Back returns Home.
2. Short Confirm should show its receipt and send a page-specific Muse brief.
   Long Confirm should trigger local source refresh without a duplicate brief.
3. Leave it idle for about two minutes: image should remain, not an off screen.
4. On battery without USB, observe a ten-minute timer wake and Power wake without
   immediate off. Deliberate new Power hold should still manually switch off.

Battery sleep current, real timer wake/latch retention, physical 700ms timing and
panel fidelity remain unmeasured. Recovery was established in earlier drills;
this release's changed physical behavior still needs observation, not inference.

## Relevant commits

- Renderer: `95e468a`.
- Ambient firmware: `219a589`.
- Glance/source/Muse integration: `1cb0a31`.
- Consent/displayed-context corrections: `04b7a6a`.
- Clockctl target dispatcher: clock-control `36a1ed1`.
- Corrected firmware: `c0a6101`.
