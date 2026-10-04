# X4 gateway and glance dashboard

The deployed X4 surface is a **built-in glance dashboard**, not an agent-card carousel.
It pulls the same configured Home Assistant weather and consented calendar sources as
`clockctl`, using `/home/cassie/services/clock-control/clock_feeds.py` and the existing
private feed config. The X4 never receives HA credentials or calendar descriptions/locations.

## Controls

The full grammar is in [docs/interaction-model.md](../docs/interaction-model.md).

On the glance dashboard (the ambient default):

- Left/Right: Home → Weather → Agenda, wrapping in either direction.
- Back: from Weather/Agenda returns Home; **from Home opens the destinations menu**.
- Agenda Up/Down: five-item pages.
- Short Confirm: ask real Muse on Alicenet for a concise briefing using source context.
- Long Confirm (700 ms): locally refresh the existing sources; not a Muse message.
- A quiet header badge (`● 2 decisions · 1 unread`) shows when agents are waiting.
  Agent records never displace the dashboard.

In the destinations menu (Home, Work, Agents, House, Life, Reports, Inbox, Status):

- Up/Down: move the highlight. Confirm: open. Back: up one level.
- Agents → an agent → a decision or report → page with Up/Down.
- On a decision, Confirm opens Approve / Reject / Defer (or the agent's own options);
  Up/Down chooses, Confirm records the answer, **Back cancels**. The answer applies only
  to the revision that was on screen; if the agent revised it meanwhile, nothing is
  recorded and the screen says so.
- On a report, Confirm marks that revision read.
- Long Confirm on any record asks Muse for fuller context. It never approves anything.
- Life and Inbox say "Not connected yet" until their adapters exist.
- An unattended timer/boot wake returns the menu to the dashboard.

House (read-only):

- Rooms come from `~/.config/x4d/house.json`, an allowlist with your own room names
  and labels (HA areas are often wrong). See `house.example.json`. Up to 8 rooms of 8
  entities: lights, switches, climate, sensors, binary sensors, media players, fans,
  covers, locks. People, trackers and media titles are refused or never read.
- The ten-minute collector reads them through the same HA client as weather; x4d
  never holds the HA token. Remove the file and House says "not set up".
- House lists rooms (`5 devices · 3 on · 1 unavailable`); Confirm opens one.
  Unavailable, unknown and unreadable entities are shown as such, never as off.
- Freshness is always on screen: `observed 4 min ago`, `STALE · observed 52 min ago`,
  or `refresh failed · showing 20 min old`. Hold Confirm to refresh now.
- Confirm on a room page does nothing yet: the first control will be one reversible,
  allowlisted action with its own confirmation and receipt.

Device behaviour:

- Firmware interaction window: 120 seconds since activity. Ambient sleep keeps the
  image visible; the gateway schedules another pull in ten minutes.
- Fresh Power hold: manual off. Power wake is ignored until released so it cannot
  immediately switch the device off again.

The clock is explicitly **sampled, not live**. Source observation age and retrieval
age are distinct. Cached refresh failures, stale data, unavailable sources and an
available calendar with zero events are separate states. No guessed forecasts.

## Agent records

Agents publish typed records through `POST /x4/v1/agent` with the agent token:

```json
{"action": "record_put", "record": {
  "id": "pyrrha.menu-order", "kind": "decision", "agent": "pyrrha",
  "title": "Ship the menu before House?",
  "summary": "Agents first keeps one interaction grammar before mutations.",
  "sections": [{"heading": "Risk", "body": "House waits a little longer."}],
  "options": ["approve", "reject", "defer"], "recommendation": "approve",
  "expires_at": null}}
```

- `kind` is `decision` or `report`. Reports take no options.
- Limits: title 80, summary 600, up to 12 sections (heading 40, body 1200), 2–4
  lowercase options. A literal `\n` in summary/body becomes a line break.
- Re-putting an identical body is a no-op. A changed body bumps `revision`, reopens a
  decision and clears any earlier answer.
- `record_get` returns `status` (`open`/`answered` or `unread`/`read`), `answer`,
  `answered_revision`. Agents poll this; answers also forward to Muse as
  `[x4 decision] ...`.
- `records` (optional `agent`) lists live records; `record_remove` deletes one.

The gateway renders long reports into screens itself (summary, then each section,
wrapped to fit); agents never format for the panel.

### From the command line

`~/.local/bin/x4ctl` wraps `gateway/x4ctl.py` (config `~/.config/x4ctl/config.json`).

```sh
# A decision with the default Approve/Reject/Defer, then block until Cassie answers.
x4ctl decide --id vesper.merge-12 --agent vesper --title "Merge PR #12?" \
  --summary "CI green, docs only." --section "Risk=Low" --recommend approve
x4ctl wait vesper.merge-12 --revision 1 --timeout 3600
# exit 0 + {"outcome": "answered", "answer": "approve", ...}
# exit 2 + outcome timeout | expired | revised   (exit 1 = error, e.g. record removed)

# A report straight from markdown: '# Title', a summary paragraph, '## Sections'.
x4ctl report --id eido.weekly --agent eido --markdown report.md   # or --markdown - for stdin

x4ctl records [--agent eido] · x4ctl record-get ID · x4ctl record-remove ID
```

Pass `--revision` to `wait` whenever you might revise the record: if it changes, `wait`
stops with `revised` instead of handing you an answer to a question you no longer asked.

## Use through clockctl

On Alicenet, the installed `/home/cassie/.local/bin/clockctl` dispatches `x4` commands
without touching the Pi Zero display endpoint:

```sh
/home/cassie/.local/bin/clockctl x4 home
/home/cassie/.local/bin/clockctl x4 weather
/home/cassie/.local/bin/clockctl x4 agenda
/home/cassie/.local/bin/clockctl x4 refresh
/home/cassie/.local/bin/clockctl x4 glance
/home/cassie/.local/bin/clockctl x4 status
/home/cassie/.local/bin/clockctl x4 events --limit 10
```

`glance` reports the chosen page/offset. `status` reports last actual device contact,
firmware, RSSI, battery and pending forwarding. A successful command changes gateway
state; `delivery: next_wake` does not establish that the X4 has displayed it yet.
Direct `gateway/x4ctl.py` supports the same verbs. Private client config is
`~/.config/x4ctl/config.json`; preserve mode 0600 and never print its token.

## Deployed services and data

- `x4d.service`: systemd user gateway, `192.168.18.61:8787`, bearer authentication
  and allowed-LAN checks; `~/.config/x4d/x4d.json`.
- `x4-glance-feeds.timer`: refreshes sources every ten minutes through the existing
  HA client and feed consent rules. The source snapshot is atomically replaced,
  mode 0600, at `~/.local/state/x4d/glance.json`.
- `glance_snapshot`: gateway config selects this dashboard instead of legacy slides.
- `refresh_cmd`: explicit command argv for on-demand source collection.
- `forward_cmd`: explicit argv invoking `x4_forward.py`; SSH stdin carries the
  message to Alicenet's existing `musegadget send-user-msg -` socket client,
  without sudo or shell interpolation. Provider acceptance is not human consumption.
- Events and per-device page selection persist in `~/.local/state/x4d/x4.db`.
  Navigation/refresh are local; only assigned short Confirm requests are forwarded.
  The gateway retains a bounded history of frame contexts by device/card/ETag and
  atomically captures the matching context with an accepted event. Queued briefs
  survive gateway restart and source refresh without substituting newer events.
  Current consent/source provenance is checked again before forwarding; unavailable
  displayed context is reported honestly rather than guessed.

Calendar cache retention is fail-closed on unknown/changed source provenance and
is re-projected through the current field consent even when HA refresh fails.
Summary-only and start-only consent preserve events with missing-time/title
fallbacks; neither means an empty calendar.

Glance PNG preview: authenticated agent GET `/x4/v1/glance.png?page=home` (also
`weather`/`agenda`). Device GET `/x4/v1/frame` returns exact 800×480 P4 PBM.
304 replies retain ETag/card/action/session/poll headers. Events are deduplicated
by device, boot and sequence.

## Build/test

Python ≥ 3.11, stdlib and Pillow; source collector also uses the installed clock
feed dependencies and dotenv. From repository root:

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_*.py'
```

Renderer tests audit one-bit dimensions, text bounds/non-overlap, reserved device
status corner, long titles, privacy exclusions, stale/unavailable/empty states,
weather zeros, sampling honesty and pagination. HTTP tests exercise built-in
page precedence, navigation dedupe, authentication and Muse/local action separation.
Host tests are not physical panel or sleep-current measurements.

## Work: public PR and build browsing

Work is a read-only GitHub surface for the explicitly approved public repository
`acgh213/alicenet-x4`. It shows open PRs and the latest GitHub Actions run on the
observed default branch. A successful build for an older head is labelled
`older head`; it never implies that newer code passed. PRs show draft/ready
state, not merge readiness or per-PR CI.

Up/Down chooses a build or PR; Confirm opens details, with Up/Down pagination.
Back returns to Work and then Destinations. Hold Confirm refreshes Work only.
Confirm binds to the identity shown on the device's frame, so reordered or
removed PRs cannot open a different item. Changed details have a notice.
An unattended timer wake still returns to the ambient dashboard.

PR and build collection ages are displayed separately. Empty successful reads,
absent builds, unavailable reads, retained failed-refresh data and data older
than thirty minutes are distinct. A full capped PR page says
`Showing first 100 PRs`; this is not a claim to have fetched every open PR.
PR update/build timestamps are separate from collection age.

The collector makes unauthenticated GET requests to the fixed GitHub API origin
only. No bodies/comments/logs/artifacts, credentials, repository mutations or
firmware changes are involved. Rate limits and network failures keep prior
valid data marked old; repository/config or default-branch changes invalidate
inapplicable cache.

Host handoff (examples only; this PR installs or activates nothing):

- `work.example.json` documents the explicit public-repository allowlist.
  Proposed config path: `~/.config/x4d/work.json`.
- `x4_work.py --config CONFIG --output SNAPSHOT` writes a separate atomic source
  snapshot, proposed at `~/.local/state/x4d/work.json`. Exit 1 can still mean a
  failure-marked snapshot was written; error response bodies are never logged.
  A shared per-output file lock serializes timer/manual collector processes;
  a busy collector exits 1 without changing the snapshot.
- Optional gateway keys `work_config`, `work_snapshot` and
  `work_refresh_cmd` select this source. The refresh command is an argv array,
  for example `["/usr/bin/python3", "/home/cassie/projects/alicenet-x4/gateway/x4_work.py"]`
  with explicit `--config`/`--output` arguments when using different paths.
  Existing HA refresh configuration remains independent.
- `x4-work-feeds.service` and `x4-work-feeds.timer` are inactive user-unit
  examples for ten-minute collection. Review paths and obtain deployment
  authorization before using them.

Without valid source configuration, Work says it is not set up. With configuration
but no valid snapshot, it says unavailable. Removing the allowlist hides cached
data immediately. A successful refresh updates the gateway; device display
still occurs on its next pull. Public unauthenticated API rate limits can prevent
fresh collection without implying that the repository is empty.

Work tests use synthetic GitHub responses, real local HTTP device requests and
offline subprocess refresh fixtures. Existing Python 3.11/Pillow gateway checks
cover the source and rendered/navigation contract. No live host, hardware or
device activation is needed to run them.

Firmware build/install instructions and pending battery sleep gates:
[`../firmware/README.md`](../firmware/README.md). Preserve CrossInk in ota_0;
install the application image via **CrossInk Settings → SD Firmware Update** only.

## Legacy agent cards

The card API remains available for tooling/tests (`slide`, `slides`, `slide-get`,
`slide-remove`, `preview`), but publishing a card **cannot displace glance pages**
while `glance_snapshot` is configured. Do not publish extra agent notes expecting
this device to show them. The standalone fake device remains useful for protocol
regressions; it is not evidence of physical X4 behavior.
