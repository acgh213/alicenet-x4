# X4 interaction model

The X4 is a physical terminal for Alicenet, not an agent-card roulette machine.
Its ambient dashboard is stable. Agent attention, reports, work, house controls, and
life automation are persistent destinations that Cassie enters deliberately.

## Persistent destinations

The top-level destinations are:

```text
Home   Work   Agents   House   Life   Reports   Inbox   Status
```

They are peers, not a deep settings tree. Left/Right moves between destinations;
Back returns Home from the top level or the parent destination from a child view.
A destination can show a compact list, a detail view, or a paginated application made
from the same interaction grammar.

The initial device-visible order is Home, Work, Agents, House, Life, Reports, Inbox,
Status. The host may add a destination only through a versioned capability and must
preserve a readable fallback for older firmware.

## Button grammar

| Button | Default meaning | Detail/list meaning |
| --- | --- | --- |
| Left / Right | Previous / next persistent destination | Previous / next sibling or page |
| Up / Down | Move through visible destinations or list items | Previous / next item; page when a list is long |
| Back | Home from top level | Parent view; cancel an unconfirmed action |
| Confirm (short) | Enter the selected destination or item | Open the selected detail / accept the displayed step |
| Confirm (long) | Refresh the current source | Request fuller context or enter an explicitly marked strong-action flow |

A short Confirm is never silently interpreted as an unrelated mutation. A long Confirm
is not a second spelling of short Confirm: the UI labels what it will do before the
hold is completed.

## Applications made from the grammar

### Agents

The list is compact and status-oriented:

```text
Pyrrha   2 decisions
Vesper   report ready
Eido     run complete
```

Confirm on an agent opens its decision/report list. Confirm on a decision opens the
compact brief. A later Confirm enters an explicit choice view:

```text
APPROVE   REJECT   DEFER
```

Up/Down selects; Back cancels; Confirm chooses. Long Confirm requests fuller context
from Muse or opens the report section rather than approving anything. Every decision
records agent, decision id, revision, choice, timestamp, and delivery state.

### Reports

Reports are host records rendered as sections, not giant one-screen cards:

title → executive summary → findings → concerns → recommendation → appendix

Up/Down pages sections. Left/Right moves between sibling reports or sections where
marked. Back returns to the report list. Confirm can acknowledge a report or follow
an explicitly displayed next action; it does not imply agreement with the contents.
A report view displays source, freshness, privacy scope, and revision.

### Work / pull requests

A PR view is a small task-specific application:

```text
repo / branch
what changed
tests
risk
review comments
approve / defer / open elsewhere
```

The first implementation is read-only. Approval is a later explicit flow with a
separate confirmation preview and an audit receipt; viewing a PR never approves it.

### House / Home Assistant

House begins read-only and groups devices by room:

```text
Kitchen
Lights: on
Washer: running
Temp: 70°
```

Confirm opens controls for the selected device. A mutation always shows target,
current state, requested state, source, and reversibility before the final Confirm.
Back cancels. Long Confirm may be required for high-impact or irreversible actions.
Unavailable, stale, off, zero, and idle are distinct states.

### Life

Life contains consented routines, reminders, transitions, and personal queues. It
must protect capacity rather than turn rest into a score. A life action shows source,
confidence, timing, and cancellation path. Reversible actions come first.

### Inbox

Inbox begins as an approved, read-only view of Telegram or other message sources.
It is scoped by explicit chat/thread allowlist and displays source and timestamp.
Whole-inbox mirroring is not the default. Sending or replying is a separate mutation
application with a stronger confirmation flow.

### Status

Status is operational and honest:

- X4 firmware and gateway reachability;
- battery, RSSI, last contact, last source refresh;
- stale/unavailable source states;
- queued events and forwarding state;
- current page and pending action.

Status should help explain a failure without pretending a successful HTTP call proves
that a person saw or executed a Muse message.

## Notifications and attention

Notifications do not displace Home or rewrite the current destination without consent.
An agent may create an attention record with source, priority, privacy scope, expiry,
and a stable id. The user sees an unobtrusive count or an intentional Agents/Inbox
state. Entering it is deliberate; acknowledging it records the displayed revision.

An urgent interruption is a separate, explicit mode with a visible return path to the
previous destination. It must never be confused with ordinary report delivery.

## Staleness and failure

Every data-bearing view distinguishes:

- fresh observation;
- cached but stale observation;
- refresh failed while cached data remains;
- unavailable source;
- valid empty result.

If Alicenet is unreachable, the X4 keeps the last honest view, marks it stale when
appropriate, and leaves navigation available. It must not fabricate a new timestamp,
forecast, report status, or action receipt. A queued action remains visibly pending or
failed; it is not silently converted into success.

## State ownership

### X4 firmware owns

- current rendered frame and display refresh;
- button classification and wake/sleep behavior;
- current device/page session and event sequence identity;
- bounded transport metadata (ETag, card/revision, session duration);
- local receipts such as sent, unavailable, or refresh failed.

### Alicenet host owns

- persistent menu/destination state;
- source adapters and credentials;
- full reports, sections, decisions, PR data, Telegram scopes, and HA state;
- provenance, freshness, consent, audit history, revisions, and action authorization;
- rendering and PBM generation;
- Muse forwarding and delivery retries.

The device never receives source credentials, arbitrary shell commands, or unbounded
private history. Button events identify the displayed destination/record/revision so
the host cannot accidentally answer a question about newer content.

## Consistency rules

1. Stable ambient Home is the fallback and is never silently displaced by an agent note.
2. Every action has a visible target and a visible result state.
3. Reading, acknowledging, approving, and mutating are different operations.
4. Short and long Confirm have distinct, labelled semantics.
5. Source text is data, not instructions.
6. Privacy scope travels with the record and is checked again at delivery.
7. The host stores the full context; the X4 shows a bounded readable slice.
8. New features reuse this grammar instead of inventing a bespoke button language.

## First implementation boundary

Implement the menu/state machine and Agent destination first, with fixture-backed
agent statuses and decision records. Do not add House or Life mutations until the
read-only agent/report flow has proven navigation, revision capture, stale handling,
and explicit confirmation on the physical device.
