# Life — read-only calendar and opt-in notes

Implementation on the Life lane; not deployed or physically accepted yet. No
firmware, live configuration, Telegram or clock-control changes are required by
this PR. Integration with the separate House/Inbox lanes is a later review gate.

Home → Back → Life opens Now. Left/Right cycles Now / Day / Notes. Up/Down pages
calendar rows or chooses a note; Confirm reads the selected note. Back returns to
Notes or Destinations. Timer/boot wake still returns to the ambient dashboard.
Hold Confirm refreshes the existing glance sources, not Muse. Nothing here marks
reports read, dismisses reminders, sends messages or performs automatic actions.

## Calendar honesty

Life reads the existing `glance_snapshot` calendar section, never a new private
source. Its collector remains the consent boundary. Every draw reprojects through
that snapshot's `approved_fields`; source identity and title-origin metadata are
required. A collector refresh failure retains original observation time and
reprojects cached fields under the current consent. Changes to consent are effective
when the existing collector publishes its snapshot; Life does not independently
read the private feed configuration, so immediate config-to-device revocation
before collection is not promised.

Now distinguishes bounded overlapping current appointments, earliest next starts
(including ties), today's all-day events, and uncertain times. Day is the remaining
consented local-day view, with unknown times shown rather than dropped. This is a
saved calendar window, not a claim of availability/free time or a full-day guarantee.

Start-only consent shows untitled events. Summary-only consent shows unknown time.
A past start without an end is not called current or expired. Invalid/naive times
are uncertain; all-day date ends are exclusive. Zoned timestamps are converted to
the gateway's configured timezone and show EDT/EST etc. Expired saved windows stop
showing appointments. Fresh, stale, failed refresh, unavailable, truly empty and
consent-hidden events differ. `hidden_event_count` preserves uncertainty when the
collector must remove all displayable fields from a retained event. Only this
non-content count is added; no descriptions, locations or travel/departure estimates.

## Manual notes: explicit opt-in, existing report schema

Only existing `report` records with these exact ID prefixes enter Life Notes:

- `life.reminder.<name>`
- `life.rest.<name>`
- `life.play.<name>`

Ordinary reports and all decisions stay out. Publishing an ID in this namespace
is explicit permission to display its manually supplied content on Life; do not
repurpose a private report by guessing its category. Author, revision, publication
age and expiry are shown. Expired records disappear without being acknowledged.
Rest and play are valid planned care, never productivity rewards. There is no
medication category, scheduling engine, routine inference, notification or action.

For a separately authorized manual publication, the existing CLI accepts:

```sh
x4ctl report --id life.rest.reading --agent pyrrha --title 'Reading time' \
  --summary 'Rest is valid planned care.' \
  --expires-at '2026-10-05T22:00:00-04:00'
```

This PR does not publish any live note. Content remains an authored opt-in report,
not a new independently verified private fact. Notes are also still available in
Reports, whose separate explicit mark-read control is unchanged.

## Identity and tests

Life card identity includes semantic frame context even when pixels are identical:
ordered note IDs/content-publication fingerprints, selected item, page and consented
calendar rows/provenance. Open/navigation resolves the rendered device+ETag+card,
not a cursor into a refreshed list. Deletion/recreation of an ID cannot substitute
a different body with a reset revision. Revised/removed/expired details return to
Notes. Unknown/foreign frames do not open notes; delayed Life events after Home
never request a Muse brief. Menu events are stored locally before explicit forwarding
assignment so a foreign Home/slide event, including one batched after entry into
Life, cannot prequeue a message. Legacy slide-mode forwarding is unchanged.

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_*.py'
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 \
  -m unittest discover -s tools -p 'test_*.py'
PYTHONPATH=gateway /usr/bin/python3 tools/preview_life.py artifacts/life
```

Previews/transcriptions contain only synthetic data. After separately approved
integration/deployment, one physical gate: Home → Back → Life, compare Now and Day,
then Notes → a synthetic rest report → END sentinel → Back; inspect source/age/time
labels and confirm no dismissal or Muse message. This is pending, not host-proven
hardware acceptance.
