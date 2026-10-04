# X4 Work: read-only PR/build browsing
Design for Cassie's review · 4 October 2026
Status: scope approved; design approval and Pyrrha ownership confirmation pending.

## Purpose and scope
Cassie wants to continue useful features on her working X4 alongside Pyrrha. This slice makes the existing Work destination useful: browse open pull requests and the latest default-branch GitHub Actions build for one allowlisted public repository, initially acgh213/alicenet-x4. Success means a readable, navigable Work screen with explicit source and freshness, including when no PRs exist or collection fails.

No firmware changes, repository writes, merge actions, private-repository credentials, deployments, House controls, issue browsing, or Telegram integration belong to this slice. Host installation is a later handoff, not part of design preparation.

## Approach
Recommend a separate Work collector and snapshot, with the existing gateway menu rendering them. This isolates GitHub failures from weather/calendar/House and keeps network calls out of rendering and button handling. Publishing everything as agent reports would reuse pagination but would leave Work as a placeholder and blur source freshness with report acknowledgement. Adding a general multi-provider work-record framework is unnecessary for this first source.

## Screen and buttons
Work opens a compact overview: repository, default branch and short head SHA, latest build name/status, open-PR count, and source collection age. Selectable rows are Latest build, then PRs sorted by update time. An empty PR list explicitly says “No open pull requests”; it still shows the build row.

Up/Down moves the highlight. Short Confirm opens the highlighted item's details; it performs no remote action and does not mark a report read. Build details show workflow, branch, tested SHA, running/completed state, conclusion and timestamp. If the latest run tested an older head, say so. PR details show number/title, author, draft/ready state, target branch, last update, and its source URL. This first slice does not claim per-PR CI or merge readiness. Long titles wrap using existing pagination/text helpers; full normalized details and URLs remain in the gateway snapshot.

Back returns details → Work → Destinations. Hold Confirm requests Work refresh. Left/Right retain existing menu behavior. Timer/boot wake returns to the ambient dashboard using the current wake rule. Footer wording presents source status and button hints; it never implies that a successful refresh has already reached the sleeping device.

## Collector and data boundary
New gateway/x4_work.py provides pure validation/projection functions, an injected bounded HTTP reader for tests, and a CLI collector. It makes GET requests only to GitHub's fixed API origin for the configured owner/repository: repository metadata, open pulls, and Actions runs filtered to the resolved default branch. No pull bodies, comments, logs, artifacts or executable content are downloaded.

An explicit Work config names the public repository, snapshot path, collection interval and freshness threshold. Initial defaults: ten-minute collection, stale after thirty minutes. Repository metadata must confirm public visibility. No token is required or introduced; rate limits are a normal failure state.

The versioned snapshot carries repository identity/default branch/head SHA; separate PR/build collection timestamps and success/failure state; PR identities, bounded text fields and URLs; latest default-branch run identity/workflow/status/conclusion/head SHA/timestamps. Stable identities include repository plus PR number or run id. Fetch limits: 5-second request timeout, 1 MiB response cap, at most two 50-item PR pages. A remaining page link produces “Showing first 100 PRs”, never a false complete count. The collector has a 15-second overall deadline; exhausted sections become failed refreshes. Default-branch changes invalidate old build data.

Successful sections replace their previous values atomically; failed sections retain prior data with the original successful timestamp and a failed-refresh marker. Cache retention requires matching repository/config identity. Removing/changing the allowlist prevents showing the former repository. Error text is a bounded category such as rate-limited/unreachable/invalid response, never an HTTP response dump.

## Failure and stale states
- Fresh successful empty PR collection: “No open pull requests”.
- Successful build collection with zero runs: “No default-branch builds found”.
- No successful section data: “Unavailable”; unknown is never success or zero.
- Failed refresh with retained data: “Refresh failed · showing N min old”.
- Age beyond threshold: “STALE · collected N min ago”, independently of refresh failure.
- Partial collection: label PR and build freshness separately.
- Queued/in-progress build: show that state; completed neutral/skipped/cancelled/timed-out outcomes retain their actual labels.

The screen identifies GitHub and the repository. Collection time is distinct from PR update/run time. A run's successful conclusion says nothing about code newer than its tested SHA.

## Gateway identity and refresh integration
Extend x4_menu.py with Work overview/detail states and a snapshot reader configured through x4d. Reuse the existing PBM/ETag/frame-context store and per-device SQLite menu state. Frame context records the highlighted Work identity and its projected revision. Confirm resolves that identity from the frame Cassie saw, never a current cursor fallback. If an item vanished, return to Work with “That item changed; refresh the list.” If details changed, show the current details with an explicit changed notice; there is no mutation to authorize.

Keep GitHub requests in the collector subprocess. Add a Work-specific configured argv refresh command using x4d's existing lock, bounded subprocess and bump pattern. A Work long-Confirm event explicitly selects this refresh path rather than refreshing HA; other page refreshes preserve their behavior. On successful command completion, bump frames; failed commands also bump when a failure-marked snapshot was written. Concurrent refresh requests coalesce under the lock.

A separate optional ten-minute systemd user timer runs the collector on the Debian gateway, writing ~/.local/state/x4d/work.json by temporary-file replacement. Config can live at ~/.config/x4d/work.json; absent config/snapshot yields “Work isn't set up yet”. Rendering never invokes network or subprocesses. Host unit/config examples are reviewable deliverables; activation waits for separate authorization.

## Tests and acceptance
Fixture tests cover public-repository validation, default-branch discovery/change, GET boundaries, pagination/truncation, response/time bounds, rate limits, malformed input, partial failures, atomic snapshot writing and cache invalidation. Rendering tests cover empty PRs, missing builds, running and all completed outcomes, different section ages, older tested SHA, long text, 800×480 one-bit output and text bounds.

Menu/HTTP tests cover navigation, Back, Work-only refresh, concurrent refresh, deduped events, changed/reordered/removed highlighted items, and ambient return on timer wake. Run the existing gateway and tool suites under CI's Python 3.11/Pillow environment with ResourceWarning treated as errors. No live GitHub requests are needed for regression tests.

A later authenticated local preview and device navigation check verify readability and existing buttons without requiring a new firmware image. Host tests do not establish battery current, timer wake or physical input timing. Existing physical gates remain separate.

## Ownership and approval
Proposed Elsie ownership: Work collector, snapshot/refresh integration and Work menu tests/rendering. Avoid House and firmware. Before implementation, obtain Pyrrha's unpublished-work confirmation, inspect the actual checkout instructions/skills, and rebase this design against any newer published changes. Then Cassie reviews this written design; after approval, prepare the implementation plan for her review and execution choice. Do not commit this design or start implementation under the current read-only repository authorization.

## Evidence
Baseline: master at 0586e97be79da010dc940d10bbaa17cdd37e55ea.
- Feature request: https://github.com/acgh213/alicenet-x4/issues/3
- Existing menu and displayed-frame identity: https://github.com/acgh213/alicenet-x4/blob/0586e97be79da010dc940d10bbaa17cdd37e55ea/gateway/x4_menu.py
- Existing bounded subprocess refresh: https://github.com/acgh213/alicenet-x4/blob/0586e97be79da010dc940d10bbaa17cdd37e55ea/gateway/x4d.py
- Existing source collector/atomic snapshot pattern: https://github.com/acgh213/alicenet-x4/blob/0586e97be79da010dc940d10bbaa17cdd37e55ea/gateway/x4_sources.py
- Hardware/test constraints: https://github.com/acgh213/alicenet-x4/blob/0586e97be79da010dc940d10bbaa17cdd37e55ea/firmware/README.md

