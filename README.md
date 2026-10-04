# alicenet-x4

A pull-based **Xteink X4 (ESP32-C3, 800×480 e-ink) terminal** for Alicenet:
weather, calendar, agent information, decisions, work reports, Home Assistant, and
life automation—without sacrificing a calm, readable ambient surface.

## Current status

The first physical vertical slice is working on real hardware. The X4 currently has:

- Home / Weather / Agenda pages using the existing clockctl weather and consented
  calendar sources;
- Left/Right/Back navigation, agenda scrolling, short/long Confirm actions;
- short Confirm forwarding a page-specific briefing to Muse and long Confirm refreshing
  sources locally;
- ambient sleep with panel retention, timer wake, and Power wake;
- a Debian `x4d` gateway, Alicenet `clockctl x4` routing, and a ten-minute source timer.

The [ambient drill log](docs/drill-log.md#2026-10-03-ambient-dashboard-navigation)
records functional acceptance on `0.3.1-ambient`, including navigation, real-source
rendering, short/long Confirm and sleep/wake. Battery sleep-current measurement
and repeated-cycle endurance remain optional, unmeasured characterization.

The gateway now also serves read-only Work PR/build browsing for the single
allowlisted public repository `acgh213/alicenet-x4`, with its own ten-minute
collector. The merged Work revision is host-deployed and its authenticated frame
and source readbacks are verified; physical Work button/panel acceptance is still
pending. This gateway update requires no firmware flash.

See [docs/ambient-release.md](docs/ambient-release.md) for the verified release artifact
and physical gates, and [docs/future-surface.md](docs/future-surface.md) for the next
expansion: agent control/information → Home Assistant → life automation → pullable
agent reports and themed/fun surfaces.

## Architecture and safety

The device is intentionally boring: it pulls complete 1-bit PBM frames and posts
button events. The gateway owns source adapters, typed records, menu state, rendering,
provenance, consent, audit history, and forwarding. CrossInk remains the recovery
system in ota_0; the Alicenet firmware is installed in the other slot through the SD
updater. Never use serial PlatformIO upload.

- [gateway/README.md](gateway/README.md): deployed gateway, glance pages, clockctl and Muse
- [docs/architecture.md](docs/architecture.md): option comparison and recommendation
- [docs/protocol.md](docs/protocol.md): X4 protocol v1 (PBM frames down, JSON events up)
- [docs/future-surface.md](docs/future-surface.md): product and implementation roadmap
- [docs/interaction-model.md](docs/interaction-model.md): persistent destinations and button grammar
- [docs/ambient-release.md](docs/ambient-release.md): release handoff and physical verification
- [docs/drill-log.md](docs/drill-log.md): hardware evidence log
- [firmware/README.md](firmware/README.md): build/install contract
- [docs/vertical-slice-plan.md](docs/vertical-slice-plan.md): original gates and measurements

## Development

Run Python with the project environment cleared so local dependency paths do not leak:

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_*.py'
PATH="$HOME/.platformio/penv/bin:$PATH" pio run -e x4  # from firmware/
```

Tools:
- `tools/inspect_x4_image.py <dump|otadata.bin>` reads partitions, the active slot and per-slot firmware identity (read-only)
- `tools/validate_sd_image.py IMAGE --board x4` validates an SD application image

`refs/` (upstream clones), `.pio/` build output, secrets, and `artifacts/` release
binaries are intentionally not tracked. The FreeInk SDK is pinned as a submodule at
the proven revision documented in `firmware/README.md`.
