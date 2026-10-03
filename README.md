# alicenet-x4

Turning a Xteink X4 (ESP32-C3, 800×480 e-ink) into a pull-based Alicenet endpoint that
clockctl can drive, dual-booted next to CrossInk.

Status: research and plan only. Nothing has been flashed.

- [docs/architecture.md](docs/architecture.md): the option comparison and recommendation
- [docs/protocol.md](docs/protocol.md): x4 protocol v1 (PBM frames down, JSON events up)
- [docs/vertical-slice-plan.md](docs/vertical-slice-plan.md): steps, gates, and the measurements that need hardware
- [docs/hardware-recovery-checklist.md](docs/hardware-recovery-checklist.md): identification, restore, and new risks
- [docs/research-notes.md](docs/research-notes.md): citations into `refs/`

Tools:
- `tools/inspect_x4_image.py <dump|otadata.bin>` reads partitions, the active slot and per-slot firmware identity (read-only)
- `tools/test_inspect_x4_image.py` is a synthetic-image test that builds on real artifacts

`refs/` (upstream clones) and `artifacts/` (release binaries) are not tracked.
