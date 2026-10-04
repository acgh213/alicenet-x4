# Elsie X4 operations

## Ownership and current boundary

Elsie is the primary X4 information publisher and maintainer of reviewed GitHub
merges. Pyrrha supplies implementation/review evidence and operator coordination;
the independent reviewer decides acceptance, not the implementation author.
Existing Elsie/Pyrrha peer messaging already works. This is not a new A2A
connector, chat inbox, relay, or remote tool installation.

This change supplies an **optional, disabled-by-default own-record publisher** and
an existing `x4ctl` workflow. It does not activate it, mint/deliver a credential,
merge a PR, change live config, restart services, read Telegram or operate HA.
Release/activation belongs to the existing release lane, after independent
acceptance and exact-head green checks. Elsie's actual execution environment,
approved secret store and X4 network reachability must be confirmed there. Do not
invent a remote filesystem path or imply that peer messaging proves X4 HTTP access.

## Tested bridge and authority

`POST /x4/v1/publisher`, on the existing x4d listener and network allowlist, accepts
only these existing protocol requests with a separate publisher bearer:

| Request | Elsie boundary |
| --- | --- |
| `record_put` | Typed `report`/`decision`, `agent: "elsie"`, id beginning `elsie.` |
| `record_get` | Own id; returns body, revision, status, answer and answered revision |
| `records` | Omitted/null agent means Elsie only; explicit other agent is forbidden |
| `record_remove` | Own id only |

The stored author **and** id prefix must match; either alone is insufficient.
Foreign entries already inside `elsie.*` cannot be overwritten. Their get/remove
results are indistinguishable from missing records and lists omit them. Scope
checks and mutations share the record store lock. Ordinary missing get returns
`result: null`; remove returns `removed: false`. Lists exclude expired records;
get still supports expiry/answer polling. Ids use the existing lowercase protocol
syntax, at most 48 characters. Request limit remains 16384 bytes. Record text
limits remain title 80, summary 600, 12 sections (heading 40, body 1200), 2–4 options.

Success responses include `principal: "elsie"` for the authenticated transport
identity and the record's `agent: "elsie"` author. The original broad agent API is
unchanged, and its author field is not authentication evidence. Broad credentials
remain trusted local operator credentials; never give them to Elsie. The publisher
credential is not accepted on `/x4/v1/agent`, frame/events, PNG previews, or
calendar/glance routes. It cannot access slides, other authors' records, menu or
device operations, HA, config/admin, answer/ack mutations, or arbitrary new verbs.

An authenticated publisher is **not a human approver**. Only the existing device
flow can supply a human answer through the rendered revision/choice context.
`wait` observes an answer; it does not execute a merge or treat a report's "read"
status as approval. A decision saying "approve" does not waive CI, independent
review, named-light consent, or physical confirmation gates.

HTTP failures: 401 missing/wrong/disabled publisher credential; 403 network/scope
refusal; 400 invalid body. Publisher validation errors are fixed messages, not
request-content echoes. No bearer or body is logged by this endpoint. `x4ctl`
reports HTTP status only for publisher errors, refuses redirects, validates the
origin and header-safe credential, and omits response bodies/transport details
from errors. Successful get/list/put output intentionally contains **own record
content**, not tokens: keep private content out of public CI/artifacts. Publisher
CLI responses above 2 MiB fail explicitly rather than silently truncating lists;
get/remove known ids individually to reduce accumulated records.

## Configuration contract (inactive until approved store is known)

Keep all real config outside every repository, mode **0600**. The operator adds
`elsie_publisher_token_sha256` (64 lowercase hex SHA-256 of a separately approved
publisher credential) to the existing private x4d runtime JSON. Omit this key to
keep the feature disabled. Null, empty, malformed, empty-token digests or a digest
reused from the original agent/device credentials fail startup. The daemon checks
0600 on runtime config when the key is present; ordinary configs keep their prior
behavior. Removal of the key requires the normal scheduled daemon reload/restart;
it is not a live hot-reload revocation mechanism.

`x4ctl --config "$X4CTL_CONFIG" ...` reads an approved private client file with:

- `gateway`: the existing reachable HTTP(S) **origin**, without userinfo/path/query;
- `publisher_token`: the approved independent publisher credential;
- **no `agent_token` key** (including an empty one).

The key chooses `/x4/v1/publisher` automatically: there is no fallback to broad
agent authentication. Publisher client config permissions must be exactly 0600.
The executable and `X4CTL_CONFIG` path are chosen only after Elsie reports her
actual runtime. Python CLI prerequisites are Python >=3.11 and Pillow, as for the
existing CLI. A runtime without shell/files may use the four JSON requests via
its existing approved HTTP capability, with authorization injected by its approved
store; that capability is not assumed or built here.

No real credential generation or delivery is part of this implementation. The
release owner must confirm the receiving side's designated store and approved
operator-mediated route **before** provisioning. Never send plaintext (or a new
ciphertext detour) over A2A, chat, public PRs, shell arguments, logs or artifacts.
Do not copy, rotate or revoke existing broad/device/peer credentials. Do not add a
public exposure, tunnel, firewall exception or improvised TLS layer; an unreachable
origin remains an explicit activation gap for the operator.

## Agent-friendly publication scripts

Examples below run only **after** activation and `X4CTL_CONFIG` points to the
approved private file. Markdown is plain text: `# Title`, summary paragraph,
then optional `## Heading` sections. No panel layout or raw slide generation.

Prepare `report.md` (nonsecret example):

```markdown
# X4 release status

Independent checks passed; deployment still awaits the release gate.

## Evidence
The exact revision and review are recorded on the PR.

## Next
Keep activation separate from implementation acceptance.
```

Publish/read back a report with the existing command names:

```sh
x4ctl --config "$X4CTL_CONFIG" report --id elsie.release --agent elsie --markdown report.md
x4ctl --config "$X4CTL_CONFIG" record-get elsie.release
x4ctl --config "$X4CTL_CONFIG" records
```

Prepare a decision using the same markdown grammar, then capture the **returned**
revision (never assume it is 1 when reusing an id):

```sh
set -eu
receipt=$(x4ctl --config "$X4CTL_CONFIG" decide --id elsie.merge --agent elsie \
  --markdown decision.md --recommend approve)
revision=$(printf '%s' "$receipt" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["revision"])')
x4ctl --config "$X4CTL_CONFIG" record-get elsie.merge
set +e
x4ctl --config "$X4CTL_CONFIG" wait elsie.merge --revision "$revision" --timeout 3600
code=$?
set -e
case "$code" in
  0) printf '%s\n' 'Receipt available; inspect outcome/answer, then apply separate release gates.' ;;
  2) printf '%s\n' 'Stopped: timeout, expired or revised; no authorization inferred.' ;;
  *) printf '%s\n' 'Publication/polling failed; keep the action on hold.' ;;
esac
```

`wait`: exit 0 = answered/read; exit 2 = timeout/expired/revised; exit 1 = error,
including missing/removed id. A reject/defer is still exit 0: inspect the answer.
Identical publications are idempotent; changed text increments revision and clears
prior answers. Removing/recreating an id also advances revision: the database keeps
only id/revision tombstones (not removed body text), seeded from existing records on
startup. Old displayed choices/acks and old wait revisions cannot apply to a new
incarnation, including across daemon restart. Use unique bounded ids or deliberately revise an existing one.
`record-remove` supports cleanup of Elsie's own records only.

`delivery: next_wake` proves storage, not panel/human consumption. Cassie opens
Home → Back → Destinations → Agents → Elsie → record; reports are also available
through Reports. Confirm on a report marks the displayed revision read; Confirm
on a decision opens choices, then Confirm records that displayed answer. Back
cancels; long Confirm requests context and is never approval. Operator-only smoke
tests can use past expiry then remove, rather than unsolicited visible tests.

## Exact merge and deploy gates

1. Freeze the candidate PR head. Read the remote `headRefOid`, state, formal
   reviews and `statusCheckRollup` (for example `gh pr view "$PR" --repo
   acgh213/alicenet-x4 --json url,state,headRefOid,reviews,statusCheckRollup`).
   Require independent acceptance naming that exact head and every required
   check successful at it. A new push invalidates earlier acceptance. No auto-merge.
2. For the current multi-lane rollout, use the reviewed integration containing
   House/Inbox/Life work; do not individually merge already-integrated siblings or
   silently bundle a separate unreviewed feature. Apply this publisher change to
   the chosen candidate without editing another lane's workspace. Re-run full
   gates and independently review the **resulting exact head** if integration
   changes it; publisher approval alone does not accept the other lanes.
3. Let Elsie perform the reviewed GitHub merge using her own established GitHub
   authority. Do not deliver Pyrrha's GitHub credential. Read back `state: MERGED`,
   `mergedAt`, `mergeCommit.oid`, actor and resulting remote default-branch commit.
   If her merge capability is not available, report the exact capability gap;
   chat/peer connectivity is not evidence of GitHub merge access.
4. The release owner updates the clean deployment tree **ff-only** to the verified
   merged revision, preserving local provisioning, database and original firmware
   recovery backup. Record previous/new revision and rollback configuration;
   maintain private runtime/source/client files at 0600. No builder live edits.
5. Under the release card's deployment authority, restart **x4d only**, not Hermes,
   and verify readiness separately from systemd active status. Read actual installed
   module hashes against the merged tree, authenticated operator status, source
   snapshot provenance and device frame context. Do not flash firmware or operate
   HA. Leave House mutations disabled pending named-light opt-in and separate
   physical Confirm; preserve the washer boundary. No live Telegram ingestion
   without a separate private-source scope.
6. Once approved credential/store/network capability is confirmed, the release
   owner activates the independent publisher digest and delivers only via that
   designated store. Exercise one bounded **Elsie-authored from her actual runtime**
   report/decision; independently read id, author, principal, revision and content
   at the gateway. Record no secret values or private content in public receipts.
   Ask Cassie to navigate to that record for physical evidence. A mock/device HTTP
   event is not an actual human answer and not physical panel proof.

Pending activation evidence: actual Elsie execution/store capability, credential
provisioning through that route, existing-origin reachability from her runtime,
reviewed integrated deployment, and one real Elsie publication/readback plus
physical device acceptance. Existing peer messaging itself is **not pending**.

## Offline verification

From repository root (no live reads):

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_publisher_*.py'
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_*.py'
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 \
  -m unittest discover -s tools -p 'test_*.py'
git diff --check
```

Publisher tests start a real loopback HTTP daemon with synthetic credentials,
publish markdown, read back typed data, poll revision-bound answers/acks, refuse
cross-record enumeration/mutations and broad/private routes, exercise original
auth, and check error redaction, redirects, malformed headers/JSON and 0600 config.
They do not connect Elsie's runtime, send peer messages or establish hardware gates.
