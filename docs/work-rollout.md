# Read-only Work host rollout — 2026-10-04

## Deployed, not merely merged

The existing local `x4d` gateway was rolled forward from
`0586e97be79da010dc940d10bbaa17cdd37e55ea` to merged Work revision
`c6b56ace461405e85e005956c048b1690ebf4317`
([PR #6](https://github.com/acgh213/alicenet-x4/pull/6)). The main checkout was
clean, on `master`, and an ancestor of that revision; deployment used a
fast-forward only. Local private provisioning files were preserved. No firmware
was flashed, no new feature PR was merged, and no Home Assistant mutation ran.

Only the existing user `x4d.service` was restarted. Authenticated agent status
returned HTTP 200 afterward, with glance mode and no pending forwards. The
Hermes gateway process was not restarted. `x4-glance-feeds.timer` remained active.
The separate `x4-work-feeds.timer` is now enabled/active at ten-minute intervals;
its real collector returned success. The configured on-demand Work command also
returned success and advanced collection time without changing the HA snapshot.

Changed host configuration paths (values and credentials intentionally omitted):

- `~/.config/x4d/x4d.json`
- `~/.config/x4d/work.json`
- `~/.config/systemd/user/x4-work-feeds.service`
- `~/.config/systemd/user/x4-work-feeds.timer`

Both JSON configs and the atomic Work snapshot are mode 0600. The existing House
allowlist, glance service/timer, and firmware provisioning were byte-preserved.
The washer remains protected and not controllable. Work never shares the HA
refresh command or credentials.

## Source and service evidence

The sole Work source is the explicitly allowlisted public
`acgh213/alicenet-x4`. Collection uses unauthenticated GETs to the fixed GitHub
API origin; it does not fetch PR bodies, comments, logs or artifacts. No arbitrary
repository access or authenticated collection was added. Public API failure/rate
limits remain failure states, not empty or fabricated successes.

At rollout verification, fresh, successful PR/build collection reported:

- default branch `master`, observed head
  `c6b56ace461405e85e005956c048b1690ebf4317`;
- zero open PRs at that observation (not a permanent repository-state claim);
- successful default-branch workflow `tests`, run
  [37209328292](https://github.com/acgh213/alicenet-x4/actions/runs/37209328292),
  on exactly the observed/deployed SHA.

Authenticated device-contract GETs returned exact 800×480 one-bit P4 frames
(48,011 bytes) for Work overview and build details. Read-only DB queries verified
matching device/card/ETag contexts bound to
`gh:acgh213/alicenet-x4:run:37209328292`. The build-detail transcription showed
`Tested: c6b56ace` and `Observed head: c6b56ace`. The host probe temporarily
selected Work then restored the prior menu view, generated no events or forwards,
and deliberately identified its telemetry as `HOST-PROBE-NOT-HARDWARE` without
inventing battery/RSSI. These reads are not evidence of physical Work display.
A subsequent genuine device pull must supersede that probe before interpreting
status as current hardware telemetry. Pre-probe genuine telemetry already
reported `0.3.1-ambient`.

Synthetic previews and transcriptions exercised Work overview/detail, stale
retained failure, older-head labels, successful empty results, configuration
revocation, and the existing agent/report menu. Live Work frames were inspected
for readable layout, clipping and overlap. Preview evidence is host-only.

## Reproducible gates

Host versions: Python 3.13.5, Pillow 11.1.0, PlatformIO Core 6.1.19. Public CI uses
Python 3.11 and its private-clock-feed test stub. There is no configured standalone
lint/type gate; `git diff --check` passed and C++ helpers use warnings as errors.

From repository root, with inherited Python environment paths cleared:

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_*.py'
# 269 tests, OK

(cd gateway && env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 \
  -W error::ResourceWarning -m unittest test_x4_work test_menu_work test_work_http)
# 51 Work-focused tests, OK (included in the full gateway suite)

env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s tools -p 'test_*.py'
# 14 tests, OK in the main checkout with existing drill artifacts
# Isolated lane: 14 discovered, 1 skipped because private drill artifacts absent

env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s firmware/test_host -p 'test_*.py'
# 10 wiring/event-body tests, OK
```

Six C++ suites (`ambient`, `bounded_http`, `http_line`, `json_out`,
`wifi_password`, `event_sequence`) were compiled with
`g++ -std=c++17 -Wall -Wextra -Werror -Isrc` and executed successfully from
`firmware/test_host`; temporary executables were kept in the configured scratch
directory. See the full commands in [firmware/README.md](../firmware/README.md).

`pio run -e x4` from the main checkout's `firmware/` succeeded. Work does not
change firmware. The exact existing application output passed
`tools/validate_sd_image.py ... --board x4`: x4 tag, six segments, 1,094,672 bytes,
SHA256 `6362561bb4b6f5a84267db6e206023b6ecdf74da604caa54a04f88c09367d7b0`.
It matches the established ambient release and was not installed again or made
public; it contains local provisioning.

## Physical acceptance boundary

The [ambient drill log](drill-log.md#2026-10-03-ambient-dashboard-navigation)
already records physical ambient rendering, navigation, short/long Confirm and
sleep/wake acceptance. That supersedes older pending-install wording. It does
not establish battery sleep current, exact threshold timing, repeated-cycle
endurance or interruption/fault measurements.

Work is host-deployed and verified. Cassie reported the basic Home → Back → Work
→ Latest build → Confirm path working in the originating DM on October 4. This
is user-reported functional acceptance, not a separate measured receipt for exact
SHA text, every button, hold-refresh, panel fidelity or power/endurance. Do not
ask her to repeat basic Work acceptance. A later targeted hold-refresh check can
confirm Work refresh stays local without Muse. No additional firmware flash is
required. House controls and other feature lanes are outside this rollout; see
[campaign review](campaign-review.md) for integration rework and the next physical gate.
