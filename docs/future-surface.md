# X4 / Alicenet future surface

The X4 is no longer only an ambient weather display. It is a small, physical control and
information surface for Alicenet: a pull-based, glanceable terminal with local state,
real buttons, and an honest boundary between reading information and taking actions.

The existing Home / Weather / Agenda dashboard remains the default ambient surface.
Future surfaces should be entered deliberately through menus or an assigned action; an
agent publishing a note must not silently displace the dashboard.

## Product shape

The X4 should feel like an instrument panel, not a miniature web browser:

- menus are sparse, hierarchical, and readable at 800×480;
- every page says where its data came from and how fresh it is;
- Left/Right changes the current page; Back returns to the parent/home page;
- Up/Down scrolls lists or changes a bounded selection;
- short Confirm reads/accepts the current thing;
- long Confirm refreshes or opens a deliberately stronger action;
- mutations require an explicit confirmation step and produce a receipt;
- ambient mode always remains recoverable and never becomes an unlabelled agent deck.

The device can become playful—pirate weather, themed reports, fun cards, small logos—but
style must remain subordinate to legibility and source honesty.

## Delivery layers

### 0. Foundation — complete

- `x4d` pull gateway on the Debian host.
- FreeInk firmware in the non-CrossInk slot, with safe SD installation and recovery.
- 800×480 P4 frames, ETag/304 polling, event deduplication and physical receipts.
- Home / Weather / Agenda pages from the same configured clock weather/calendar sources.
- `clockctl x4` dispatch on Alicenet without mutating the Pi Zero clock target.
- Real Muse forwarding for assigned short Confirm actions.
- Ambient sleep, preserved panel image, timer wake and Power wake.

### 1. Agent control and information — first expansion

Build the menu and information model before adding more automation:

- **Agent menu:** agent presence, current task, last report, queued attention, and
  capability/status summaries.
- **Work menu:** PRs, review requests, CI failures, issues, and project work cards.
- **Decision queue:** bounded decisions an agent is asking Cassie to make, with context,
  choices, consequences, and a clear Confirm/Back path. A decision should be readable
  without opening a phone, while long material becomes a report available page by page.
- **Reports:** daily digests, research handoffs, build results, and incident reports;
  each report has a source, timestamp, status, and a stable report id.
- **Telegram inbox view:** start read-only. Show explicitly addressed messages, selected
  threads, or summaries from an approved chat; never mirror the whole private inbox by
  default. Replying or sending must be a separate, strongly confirmed action.
- **PR/work/fun categories:** content classes with distinct visual treatment, not three
  unrelated card implementations.

A first menu could be:

```text
HOME
├── AGENTS
├── WORK
│   ├── DECISIONS
│   ├── PULL REQUESTS
│   └── REPORTS
├── WEATHER
├── AGENDA
└── HOUSE
```

### 2. Home Assistant control — second expansion

Add Home Assistant as an explicit `HOUSE` surface, initially read-only:

- room/device status and active scenes;
- lights, climate, covers, media, and presence summaries;
- stale/unavailable state shown separately from an actual off/closed/zero state;
- a bounded action preview before mutation: target, current state, requested state,
  and source of the command;
- Confirm performs only the displayed action; Back cancels; long Confirm can require a
  second confirmation for high-impact actions;
- every mutation gets an audit receipt and is never hidden behind a generic “done”.

The gateway should call Home Assistant through the existing authenticated integration,
not place HA credentials or unrestricted service calls in firmware or reports.

### 3. Life automation — third expansion

Only after agent and house controls are trustworthy:

- reminders and routines grounded in the consented calendar;
- morning/work/home/night modes;
- transition prompts such as “leave soon”, “next appointment”, or “weather for the
  walk”, with source timestamps and no invented certainty;
- personal queues: errands, care tasks, meals, medication or other categories only
  when explicitly configured;
- reversible actions first, with durable audit history and an easy cancel path.

Life automation should protect capacity rather than turn the X4 into a productivity
scoreboard. Rest and play are valid planned states.

### 4. Agent-delivered reports and pullable cards

Agents can publish report records to the gateway, but the device pulls a typed view:

- `report`: long-form material paginated into readable sections;
- `decision`: question, options, recommendation, and status;
- `work`: PR/issue/build state with links and next action;
- `house`: current state or proposed mutation;
- `fun`: optional themed content that cannot masquerade as operational truth.

Each record carries provenance, freshness, privacy scope, expiry, and a stable revision.
The X4 stores only the small current view and event identity; the gateway stores the
full report and context. Confirm forwards the displayed record id/revision, not merely
whatever happens to be newest when the event reaches Muse.

## Technical direction

Keep the first implementation deliberately boring:

- extend the existing Python gateway and SQLite store before introducing a message bus;
- use typed records and a menu/page state machine, not arbitrary HTML or a UI framework;
- keep renderers pure and fixture-tested, with synthetic previews for tests and private
  live previews only on the local machine;
- keep firmware transport dumb: pull a complete PBM, expose buttons, post events;
- keep source collectors separate from renderers and action handlers;
- use one event/audit model for navigation, decisions, Home Assistant mutations, and
  Muse forwarding;
- add capabilities and versioned protocol fields before adding a second client.

The Raspberry Pi 4 remains the main Alicenet/Muse runtime. The Debian gateway remains
the X4's local boundary and renderer. Home Assistant, Telegram, GitHub and future
sources enter through explicit adapters with per-source consent and privacy scopes.

## Immediate build sequence

1. Add a typed menu/page state machine and a status/agent menu without changing the
   ambient default.
2. Add report and decision records to the gateway store, with fixture data and a local
   authenticated preview.
3. Add PR/work adapters as read-only sources and render a Work page.
4. Add Telegram read-only summaries behind an explicit approved-chat/thread allowlist.
5. Add a read-only Home Assistant House page, then one reversible action with a
   confirmation preview and audit record.
6. Add report pagination and agent-delivery scheduling/pull semantics.
7. Add themed/fun render variants, including pirate weather, only after the data model
   and controls are stable.

## Non-goals for the first expansion

- no arbitrary remote code execution from a button;
- no unrestricted Telegram mirroring or sending;
- no generic “agent card” priority that can hide the ambient surface;
- no cloud dependency where a local source is sufficient;
- no animation system or busy game layer;
- no action without a visible target, source, confirmation state and receipt.
