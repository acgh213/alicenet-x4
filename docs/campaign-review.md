# X4 campaign integration review — 2026-10-04

Verdict of the initial independent review: REQUEST_CHANGES. Existing automated
gates passed; adversarial probes found seven defects. The repair phase below has
regression-tested corrections ready for independent re-review, not release
acceptance. Do not merge individual PRs or this branch, deploy it, enable House
controls, or activate live Inbox ingestion on the basis of green CI.

## Exact inputs

Base/deployed gateway: `c6b56ace461405e85e005956c048b1690ebf4317`.

| Lane | Open, unmerged PR | Reviewed head |
| --- | --- | --- |
| Work rollout docs | https://github.com/acgh213/alicenet-x4/pull/7 | `e64d794bb65719ba9af788f10dd725f31e892d6b` |
| House | https://github.com/acgh213/alicenet-x4/pull/8 | `bdb118b4f0a4af3c1d34516ab5f13450b5435d44` |
| Inbox | https://github.com/acgh213/alicenet-x4/pull/9 | `ce7b3e3cd5038f1b90f9e9ab125465a1d368ff40` |
| Life | https://github.com/acgh213/alicenet-x4/pull/10 | `c3d81bed6f1b3aecc1098d8bce0622e52968405c` |

Remote PR/head/check readbacks confirmed each input; each has both exact-head
Python checks successful. All input commits are preserved as integration
ancestors. Main and worker lanes were not edited. No Forgejo metadata is present.

## Reconciliation decisions

`gateway/x4_menu.py` is the hotspot. Combine independent HouseViews, InboxViews,
and LifeMenuMixin; preserve House's positional fourth argument and pass optional
clients by keyword in App. Preserve the House observation-based ETag, Inbox
opaque context-based ETag and Life card context. Route Life before the common
left/right rejection. Preserve Inbox delayed-frame isolation and Life entry
fences. Snapshot each view once for settling/rendering. Keep Life local-first
label assignment and Inbox legacy-slide isolation together, with legacy slide
mode unchanged. No new configuration flags or source permissions were enabled.

Conflict paths: `docs/protocol.md`, `gateway/README.md`, `gateway/x4_menu.py`,
`gateway/x4d.py`, `gateway/x4_store.py`, `gateway/test_x4_menu.py`. The old
"unbuilt" destination test now positively exercises implemented Life and
unconfigured Inbox rather than silently iterating an empty list.

## Initial required changes (line numbers in initially reconciled source)

The numbered findings below are the preserved independent review reproduction,
not a claim that they remain uncorrected after the repair phase.

1. H1 / P2 — `gateway/x4_house_actions.py:83-101`: the helper uses invocation
   time to check expiry, then performs a blocking state read and policy check.
   Synthetic invocation at TTL-1, read latency 10s: service sent at expiry+9s;
   receipt still says verified with the old timestamp. Recheck current helper
   clock after preflight and immediately before service send; record actual
   receipt times. Inject a clock for deterministic tests, retaining process
   deduplication and uncertainty-before-send.
2. H2 / P3 — `gateway/x4_house_view.py:49-57,69-86`: two concurrent distinct
   Confirm events can schedule two executors; a delayed busy/uncertain completion
   overwrites a verified gateway receipt. Synthetic concurrent probe scheduled
   two executors, sent once, helper verified, gateway uncertain. Later frame
   reconciliation recovers, but terminal outcomes must not regress. Use an atomic
   preview-to-receipt transition and/or monotone conditional receipt updates;
   preserve newer navigation and action IDs.
3. H3 / P2 — `gateway/x4_menu.py:242-267`: after timer/Home navigation, a delayed
   House preview Confirm falls through glance and returns `label='brief'`; the
   HTTP handler assigns/queues that Muse forward at `gateway/x4d.py:411-414`.
   Protect stale/evicted/foreign House frames before glance forwarding (short and
   long Confirm), like Inbox/Life. Add real HTTP regression with no pending
   forward or source refresh, including batched navigation.
4. I1 / P2 — `gateway/x4_inbox_view.py:8-11,95`: unsupported glyphs are silently
   drawn as identical missing boxes. Accepted bodies `北京` and `上海` produced
   byte-identical whole PBMs despite different text transcriptions. Add an explicit
   glyph fallback/escape policy with raster tests, including titles/source labels,
   pagination and non-BMP text. Do not substitute transcription assertions for
   pixel coverage. This is unreadable content, not ETag substitution.
5. I2 / P2 — `gateway/x4d.py:256-265`: five-second socket timeout is inactivity,
   not an overall body deadline. Synthetic authenticated loopback chunks at
   t=0/3/6s returned HTTP200 after 6s. Add a monotonic overall body-read deadline;
   preserve byte/negative-length bounds and generic privacy errors. Define/test
   timeout response behavior: stalled input currently closes without JSON.
6. I3 / P3 — `gateway/x4_inbox.py:159-164`: remove lacks the consent fence used
   by put/status/snapshot. Revoking immediately after policy read returns
   `removed=True`, revealing a revoked existence bit. Fail closed under policy
   change while preserving committed revocation cleanup and safe deletion.
7. L1 / P2 — `gateway/x4_life.py:142-143,198,222` and
   `gateway/x4_menu.py:187-199`: Life changes semantic card but not ETag for equal
   pixels. Current `firmware/src/main.cpp:301-309` ignores X-Card/ETag on 304,
   updating them only after 200 at lines 339-340. Same-pixels note replacement
   therefore keeps the old authenticated card indefinitely until pixels change,
   and Confirm cannot open the new note. Probe: equal PBM/ETag, differing Life
   cards; this is fail-closed liveness, not wrong-note approval. Prefer hashing
   semantic Life context into ETag (gateway-only), testing real conditional GET
   returns 200 and current-firmware event identity. Update the 304 contract.

Additional Inbox contract caveats, not additional numbered defects:
`x4_inbox.py:126,193` reuses identity on identical remove/republish; either add
incarnation or narrow blanket detail-invalidation docs. The current render fence
in `x4_menu.py:173-185` precedes PBM serialization: revocation during serialization
can return prior pixels. Move the last check as late as practical and accurately
state the response-boundary race. Already-downloaded sleeping e-ink pixels cannot
be erased remotely before wake/pull; no forensic-erasure promise is made.

## Actually executed verification

- Canonical isolated Life gateway suite: 302 tests, OK, 113.201s, with
  ResourceWarning errors. House/Inbox independent subreviewers also executed
  their full suites (305 and 310); the parent reran their concrete probes.
- Reconciled canonical gateway suite: 379 tests, OK, 161.811s. An earlier run
  had one newly written integration assertion expecting 'not set up' rather than
  actual 'not configured'; corrected that assertion, not product behavior.
- Tools: 16 run, 15 passed, one skip (private drill artifact absent).
- Firmware host Python: 10 passed. Six C++ suites compiled with C++17,
  `-Wall -Wextra -Werror`, and ran successfully. Initial http_line execution from
  firmware root crashed because it requires test_host fixture cwd; canonical cwd
  rerun passed. No product fix was made for a wrong test invocation.
- Full PlatformIO x4 build: SUCCESS, 34.47s. Synthetic build-only provisioning,
  not live credentials; SD validator x4 tag, six segments, 1,094,640 bytes;
  SHA256 `09962ab2e9243ee102ac9d1844c7851ac357bbaadb3fa0e5c4492a19ad62c032`.
  Build-only artifact is not suitable for deployment and was not flashed/shared.
- House adverse script reproduced H1/H2 with synthetic HA only. Inbox adverse
  tests reproduced I1/I2/I3 (three failures against required behavior).
- Integration probes: four tests, two pass (pure offline projection and END/read
  without ack plus revocation/recreation), two fail (H3/L1). Passing discovery
  does not negate these independent failures.
- Synthetic House/Inbox/Life previews regenerated; transcriptions and selected
  rendered PNGs reviewed for clipping/source/time/END. No live calendar pixels
  published, no HA service mutation, Telegram ingestion, A2A echo or report
  publication on the live gateway.

Reproduce existing combined suite:

```sh
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 -W error::ResourceWarning \
  -m unittest discover -s gateway -p 'test_*.py'
env -u PYTHONPATH -u PYTHONHOME /usr/bin/python3 \
  -m unittest discover -s tools -p 'test_*.py'
```

Adversarial scripts are preserved in the task's downloadable review-evidence
bundle; run `pr8_adversarial.py`, `inbox_pr9_review_probes.py Probe`, and
`x4_integration_review_probes.py Review` with cleared Python environment paths.
They use synthetic temporary data, not live transport.

## Repair phase — t_8b572cc3, independent re-review pending

Implemented in integration only, preserving all input lane heads as ancestors:

- H1: injected current helper clock checked after fresh preflight and immediately
  after the uncertainty commit/before send; rejected/verified receipts use actual
  helper time. Process-shared deduplication and uncertainty-before-send remain.
- H2: conditional atomic SQL preview-to-receipt claim schedules one executor;
  updates apply only while the matching receipt is uncertain, never regressing a
  terminal result or restoring obsolete navigation/action IDs.
- H3: stale/evicted/foreign House Confirm/hold remains local before glance routing;
  real HTTP tests cover timer/Home and batched Back-to-glance, no Muse/refresh.
- I1: actual selected-font missing masks escape unsupported BMP/non-BMP glyphs
  before measured wrapping. Body/title/source rasters equal literal escape rasters
  and differ for distinct CJK/non-BMP strings. Supported accents stay native;
  source escapes bypass breadcrumb uppercasing. Pagination reaches END.
- I2: read1 with shrinking monotonic five-second body budget; stalled and trickled
  input returns fixed generic HTTP408 JSON and closes. Wire bounds/short EOF/bad
  UTF8/malformed length still return generic Inbox400 without private echoes.
- I3: remove fences consent before existence return, rolls back on revocation and
  commits revocation pruning independently; real HTTP403 and empty-table checks.
- L1: gateway-only Life ETag hashes full semantic frame context. Equal-pixel note
  replacement returns 200, supplies new identity to current firmware, rejects old
  Confirm and opens new note from current identity. No firmware edit/flash.

Inbox caveats addressed explicitly: final render check moved after serialization,
bounded re-projection then empty fallback; post-check/network-response race and
sleeping e-ink retention remain documented. Identical remove/republish under the
same grant keeps logical key/revision if no frame observes absence; changed body,
missing row or changed grant invalidates details. No incarnation guarantee.

Actually executed final repair verification:

- Cleared-env ResourceWarning-errors gateway discovery: 400 tests OK, 195.146s.
- Cleared-env tools: 16 run, 15 passed, one skip (private drill artifact absent),
  4.524s. No firmware changed; original firmware gates are recorded above, not
  rerun or claimed as new hardware acceptance.
- Original independent integration probes copied unchanged from durable evidence:
  four OK, 2.568s, including equal pixels/distinct Life ETag and no House brief.
- Every numbered fix had a focused failing behavioral regression observed before
  implementation. Real loopback deadline test completes with 408 around t=5 after
  chunks at t=0/3, before planned t=6 completion; short trickle and stall also pass.
- Synthetic House/Inbox/Life previews regenerated; actual glyph fallback pixels
  inspected through page 5/5 END, with correct literal BMP/non-BMP escapes and no
  clipping. Verified House target/request/source/no retry and Life note END,
  publish age/source attribution and no-dismiss footer are legible. Pixel review
  is not physical panel acceptance. No live private pixels in evidence.

Repair code commits: `4fb6738`, `ae75f4a`. Draft PR12 receives normal fix push only,
no merge/deploy. Exact-head CI and remote head readback are recorded in the repair
card handoff (not inferred from earlier 3b956f5 CI). Initial REQUEST_CHANGES stands
until independent buildreviewer clears this exact repaired head; campaign and
physical acceptance are still pending.

## Deployed versus pending

Read-only operator check: existing x4d PID 1867851 active/running, both source
 timers active/waiting, Work collector ExecMainStatus=0. Authenticated `x4ctl
status` returned success, glance mode, firmware 0.3.1-ambient, pending_forwards=0.
No restart or menu/device probing was needed. This is readiness/contact, not new
physical feature acceptance.

Work is deployed at c6b56ace; Cassie's originating-DM report 'yo that. yo. it works'
is basic user-reported Work functionality acceptance. Do not request a repeat of
that basic test. Exact SHA text/hold-refresh, calibrated timing, battery current,
endurance and interruption characterization were not separately established.

House/Inbox/Life: implemented, host-tested, unmerged, undeployed, physical pending,
REQUEST_CHANGES. House needs current collector source/state fingerprints and
helper/collector HA-origin agreement, separately authorized controls, and a
separate approved-light cancel/execute/readback/reverse drill; washer excluded.
Inbox needs explicit exact source/chat/thread/display-label/publisher permission;
never infer chat scope from the current DM. No live private source is enabled.

Single next physical test, only after fixes, independent re-review and separately
approved integration deployment: Home → Back → Life → compare Now/Day → Notes →
a separately published synthetic rest report → END → Back. Check time/source/age
and confirm no dismissal or Muse. No physical testing is claimed in this review.
