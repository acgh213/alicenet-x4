# Inbox: privacy-scoped read-only reports

Implemented and offline-tested, not deployed or physically accepted. Live Telegram
transport remains disabled. Nothing reads Hermes state.db, Telegram history,
getUpdates, bot credentials, or other profiles. The complete selected integration
path is authenticated agent-published normalized reports, not a polling stub.

## Consent

Default DENY. No `inbox_config`, missing/invalid config, or empty `scopes` means
no report ingress or display. Configure the gateway's optional `inbox_config` key
to an operator-owned local JSON policy (mode 0600; never commit it).
`gateway/inbox.example.json` deliberately contains no consented scopes.

Each scope requires exact strings `source`, `chat`, `thread`, plus a display
`label` (40 characters) and a new unique `grant` for that authorization epoch.
For a non-topic chat, choose an explicit stable thread string such as `main`;
null, omission and wildcard matching are not supported. Never infer a chat/thread
from the current conversation. Up to 32 scopes; `stale_after_s` 60–604800 seconds.

Synthetic example only (not authorization or live identifiers):

```json
{"scopes":[{"source":"telegram","chat":"fixture-chat","thread":"fixture-thread",
            "label":"Synthetic lab","grant":"fixture-grant-1"}],"stale_after_s":600}
```

Publish configuration atomically, then perform an authenticated `list` readback.
Remove a scope or the policy file to revoke. Every operation re-reads policy and
prunes nonmatching cached rows. In-flight ingest checks policy again before commit;
rendering checks it again after building pixels and discards a changed-consent
frame. Unknown/corrupt policy fails closed. Change `grant` on every reauthorization,
including revoke/regrant between gateway reads, so old cache cannot resurrect.
Scope edits are sampled at operation boundaries; no software can erase pixels
already downloaded to a sleeping e-ink panel. Wake/pull to replace those pixels
with the revoked/not-configured screen. A publisher holding copied content must
obey its own consent too; this API does not grant permission to scan upstream.

## Authenticated local API

`POST /x4/v1/inbox` uses the existing agent bearer token and network allowlist.
Device tokens cannot publish or list reports. Do not expose this plaintext LAN API
publicly; use loopback/a trusted local publisher (or an explicitly secured tunnel).
No new secret, service, timer or live adapter is provisioned by this feature.

Payloads (JSON, at most 16384 wire bytes, bounded body-read timeout):

- `{"op":"put","message":{...}}` accepts exactly `source`, `chat`, `thread`,
  `id`, `time`, `title`, `text`. IDs are stable source message IDs (128 characters),
  time is timezone-qualified ISO8601 (at most five minutes in the future), title
  at most 80 characters, text at most 12000. No instructions/options/actions.
- `{"op":"list"}` returns current-consent `state`, `items`, `sources`; items
  include opaque `key`, `revision`, label, message UTC `time`, `received_at`,
  staleness and a pagination-compatible report body. No chat/thread/source IDs are
  echoed. The policy fingerprint is only an opaque operation-generation fence.
- `{"op":"remove","key":"<64 hex key from put/list>"}` deletes an authorized
  cached report. Removed IDs can be republished; open old details are invalidated.
- `{"op":"status","source":"...","chat":"...","thread":"...","state":"ok"}`
  records a publisher's observation. State is `ok`, `error`, or `unavailable`;
  do not report ok unless a source was actually observed (including valid empty).
  No arbitrary error text is accepted or exposed. A report publish also records
  a successful source observation; identical retry does not refresh source age.

Responses: `200 {"ok":true,"result":...}`, `401` bad agent auth, `403` scope denied,
`400` malformed/oversized/conflicting/full cache, `503` storage unavailable.
Error responses are generic and never echo payloads or exception text. Acceptance
means stored, NOT displayed on the X4. Read back `list`, then the device pulls.
Keys include the exact scope/grant and stable ID. Identical retry returns the same
receipt/time; a changed body at the same ID is rejected rather than silently
revised. Capacity 128 reports; remove explicitly, no silent eviction. SQLite
is the existing private gateway DB, not any Hermes DB.

## Buttons and failure states

Home → Back → Inbox → Confirm opens the report highlighted on the device's
rendered frame, even after reorder. Missing/revoked/replaced IDs never open the
new current index. Unknown or other-view frames cannot acquire Inbox semantics;
delayed Inbox events cannot become Home brief/refresh actions. ETag includes
opaque frame context so identical pixels with changed hidden identity do not alias.
Up/Down pages complete text; Back returns to the list/destinations. Short and long
Confirm in details do nothing: no mark-read/decision/reply/context/refresh/forward.
Legacy slides cannot assign actions to menu-owned Inbox frames.

Source label and message time stay on detail pages. Source observation state and
report receipt staleness are separate: not configured, unavailable before a first
observation, error, stale cached reports, and valid empty success are honest.
Stale details retain source failure indicators. Text is literal data, never
executed or forwarded. Unrenderable combining marks are shown as Unicode escapes
after NFC normalization; stored/readback text remains unchanged. Inbox uses the
existing report pagination shape but never creates typed Records, decisions,
answers, ambient dashboard counts or forwarding contexts containing content.

## Verification and activation

Synthetic-only commands from repository root:

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning -m unittest discover -s gateway -p 'test_*.py'
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -m unittest discover -s tools -p 'test_*.py'
PYTHONPATH=gateway /usr/bin/python3 tools/preview_inbox.py artifacts/inbox
```

Read `artifacts/inbox/transcriptions.txt`, not only screenshots. Independent review
and exact PR-head CI are required before integration. No merge/deploy/firmware or
physical acceptance is implied by these offline tests. The shared menu is a
House/Life integration hotspot; Inbox behavior is isolated in `x4_inbox_view.py`.

Activation request: specify exact source/chat/thread scope(s), safe on-device label,
and whether an authorized agent may publish reports from them. No Telegram
transport may be enabled or history imported without that explicit authorization.
After approved integration/deployment, the next physical test is Inbox → selected
synthetic report → page to its END sentinel → Back; verify source/time and no reply.
