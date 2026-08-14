Proceed with the next major opstat refactor phase as one complete engineering work package.

I want to minimize back-and-forth. Within the scope below, investigate, implement, test, validate against the real lab cluster, update durable docs, and return one complete report.

Do not stop for routine implementation choices that are already supported by repository rules, existing patterns, tests, or real-cluster evidence.

STOP only for:
- destructive VMS actions beyond cleanup of monitors created by your own test session
- production-impacting changes
- credential/security concerns
- force-push/history rewriting
- a major concurrency/architecture change
- a dependency/runtime change
- scope expansion beyond this work package
- evidence that contradicts a settled decision record

Do not commit.
Do not push.
Do not merge.
Do not create a PR.
Do not tag.

Use the repository rules, AGENTS.md, CLAUDE.md, applicable .claude/rules, skills, decision records, and validation gate throughout.

CURRENT REAL-VMS AVAILABILITY

Treat this as current operational context:

- var204.selab.vastdata.com is unavailable until I explicitly say otherwise.
- Do not use var204 for testing or treat prior var204 measurements as current.
- Use var203.selab.vastdata.com for all real-cluster validation in this work package.
- Use VAST_PASSWORD from the environment.
- Never put the password in argv, tracked files, docs, commits, API logs, or handoff files.

If the current handoff does not durably record var204's unavailability, add a short CURRENT ENVIRONMENT note to docs/REFACTOR_HANDOFF.md.
Do not create a decision record for this temporary environment condition.

CURRENT UNCOMMITTED WORK

There is already a completed SMB/S3 vast_drill implementation in the working tree that has been validated against var203 but is not committed.

Do not lose, revert, or rebuild that work unnecessarily.

Before doing anything else:

1. Inspect git status and current diff.
2. Verify the working tree contains exactly the expected SMB/S3 work from the previous phase.
3. Re-run ./scripts/validate.sh.
4. If the existing SMB/S3 work is still green, treat it as the baseline for this phase.
5. Do not commit it yet.

The previous phase established approximately:

SMB view:
- 145 candidates
- before: 18 entry calls / 104 s ranking
- after: 7 entry calls / 9 s ranking
- 5 serial ranking monitors collapsed to 1 batched rank monitor

SMB tenant:
- before: 9 entry calls / 34 s
- after: 7 entry calls / 37 s
- deterministic call-count improvement despite variable VMS latency

S3 bucket:
- before: 18 entry calls / 47 s
- after: 7 entry calls / 45 s
- 5 serial ranking monitors collapsed to 1 batched rank monitor

S3 tenant:
- before: 9 entry calls / 59 s
- after: 7 entry calls / 10 s

S3 VIP:
- preserved
- topn ranking preserved
- 192.168.* filtering preserved
- per-object VIP monitors preserved
- topn-only fallback covered by mock
- VIP cadence intentionally unchanged

SMB/S3 view/tenant/bucket steady-state polling is now throttled rather than querying every ~5 s tick.

The shared loading UX now includes:

  Gathering initial metrics, please stand by...

for SMB/S3 startup, plus drill-specific loading interstitials.

PHASE 1 — DURABLY RECORD THE NEW OPSTAT FEATURE BACKLOG

Create/update an appropriate backlog section in the durable project documentation.

Do not invent a heavyweight issue-management system if the repo does not have one.

Add these four owner-requested OPSTAT FRs:

FR-A — STANDARDIZE NAVIGATION SHORTCUT KEYS

Across all protocol engines, standardize common navigation shortcut keys wherever the same conceptual action exists.

Examples of common actions:
- quit
- sort by ops
- sort by latency
- sort by name
- cNode drill
- view drill
- tenant drill
- exit drill
- manual refresh
- VIP drill/view

Requirements:
- same concept should use the same character across engines whenever practical
- same labels, capitalization, ordering, separators, and overall visual style
- protocol-unique options are appended after the common controls
- NFSv4.1-specific [4] native telemetry and [h] v4 hosts remain unique additions
- VIP should standardize on [i], not [v]
- do not reuse one shortcut for two different concepts on different engines
- no navigation control may disappear because of terminal-width truncation
- preserve narrow-terminal behavior
- unique protocol controls should not reorder the common controls

Do not blindly change every engine immediately.
Fold this into engine work as those engines are touched, and build shared helpers where that reduces divergence safely.

FR-B — LATENCY UNIT CORRECTNESS AND DISPLAY

Audit latency units across all metric paths as engines are touched.

VAST API endpoints may return:
- seconds
- milliseconds
- microseconds
- nanoseconds
- cumulative latency sums requiring derived units
- endpoint-specific semantics

Requirements:
- verify source unit from code/API evidence, not assumption
- double-check every conversion mathematically
- distinguish native from derived values
- avoid accidental double conversion
- avoid displaying 0 when a small non-zero value is merely below formatting precision
- prefer milliseconds for user-facing latency where that improves readability
- retain microseconds where values are consistently sub-ms and ms would obscure useful detail
- make the displayed unit obvious in headers/labels
- use one consistent formatting policy across protocols where practical
- add unit tests with literal source values covering ms/us/ns conversions
- do not change a unit based only on "what looks plausible"

FR-C — BLOCK FABRIC PERCENTAGE CORRECTION

In BLOCK/NVMe:
- Fabric activity count must NOT participate in the primary workload percentage denominator.
- Primary workload percentages should be based only on actual workload categories such as READ, WRITE, and metadata if applicable.
- Fabric metrics remain visible as their own separate bar/metric.
- Fabric should not distort READ/WRITE percentages.
- Verify current math before changing it.
- Add tests proving the old denominator is wrong and the corrected denominator produces expected percentages.

FR-D — TESTING / QUALITY BUILD-IN

The repo now has a real test suite and ./scripts/validate.sh.

Treat the FR as:
- continue expanding tests until legacy behavior being touched is covered
- every code commit must run the complete validation gate
- Python 3.8 and current Python must both pass
- zero skips
- openssl-backed tests must actually run
- new defects require regression tests where practical
- API efficiency is a tested regression dimension
- TUI navigation/render behavior is tested
- metric conversion/unit semantics are tested
- mock tests do not replace real-VMS validation when the semantic question requires the cluster

Do not claim "unit testing was missing" anymore; the repo now has substantial coverage.
The remaining goal is coverage expansion around legacy paths as they are refactored.

PHASE 2 — INVESTIGATE THE CLEANUP-INTERRUPTION DEFECT

This is now a real safety issue, reproduced more than once on var203.

Observed behavior:
- clean `q` exits delete all monitors correctly
- SIGTERM during a slow synchronous cleanup can leave an earlier-created `adhoc_opstat_*` monitor behind
- one observed leaked SMB headline monitor
- one observed leaked S3 VIP per-object monitor
- cleanup can terminate without an explicit "not deleted" warning
- shared cluster has unrelated/concurrent opstat sessions, so verification must track exact session monitor IDs

Treat this as a defect investigation and, if the root cause is safely fixable without introducing concurrency or a major lifecycle redesign, FIX IT in this work package.

Requirements:
1. Reproduce reliably in the mock before changing code if possible.
2. Inspect:
   - signal handlers
   - atexit registration
   - cleanup ordering
   - session/log shutdown ordering
   - monitor tracking containers
   - timeout behavior during delete
   - reentrancy if signal arrives while cleanup is already running
3. Determine why cleanup can be truncated while still appearing complete.
4. Ensure cleanup is idempotent.
5. Ensure a second cleanup invocation cannot skip monitors because state was cleared too early.
6. Do not use SIGKILL in tests.
7. Do not add threads just to solve this.
8. Add regression tests for interrupted/partial cleanup.
9. Preserve current normal `q` behavior.

If the fix requires a major signal/lifecycle redesign, STOP that sub-item, document the root cause and recommendation, and continue with NVMe work if safe.

During real-VMS validation, you are approved to delete only temporary monitors created by YOUR test session if a failure leaves one behind.
Do not sweep pre-existing/concurrent adhoc_opstat_* monitors.

PHASE 3 — ESTABLISH A REAL NVMe BEFORE BASELINE ON var203

Use the existing BLOCK/NVMe invocation through environment credentials.

Do not put the password in argv.

Use:
- var203.selab.vastdata.com
- the existing configured test volumes
- opstat itself
- --log-api-calls where useful

Before implementation, measure the current real path.

Capture:

STARTUP
- time to any visible output
- time to first normal frame
- whether user sees a blank/frozen terminal during slow startup
- startup API-call count
- monitor count

CLUSTER REFRESH
- monitor queries per refresh
- total API calls over ~30 s
- whether the documented ~467+ calls/30 s is still accurate
- current monitor-family split
- whether BlockMetrics/VolumeMetrics/ProtoMetrics are actually rejected when mixed on this VMS build or merely historically separated

DRILL ENTRY
- candidate volume count
- whether it head-slices rather than ranks
- number of drill monitors
- entry API calls
- wall-clock
- monitor cleanup
- whether drill selection chooses active volumes or merely first returned

DRILL STEADY STATE
- queries per refresh
- any per-object explosion
- whether manual refresh changes behavior

DISPLAY / FR-C
- current workload percentage math
- exact denominator
- where Fabric enters the computation
- current Fabric bar
- whether metadata exists as a workload category
- literal before values sufficient to build a failing test

NAVIGATION / FR-A
- current key map
- compare against SMB/S3/NFS common controls
- identify conflicting/unique keys
- identify VIP key if any
- do not change yet until map is understood

LATENCY / FR-B
- identify every latency source used by BLOCK/NVMe
- source metric names
- source units
- conversion code
- display units
- verify against API/catalog/known semantics
- identify inconsistent or ambiguous conversions

Monitor cleanup verification must key on exact IDs created by your session.

PHASE 4 — RE-VERIFY THE NVMe MONITOR-FAMILY CONSTRAINT

The prior handoff says NVMe's 8 cluster monitors per refresh are a documented VMS constraint because BlockMetrics / VolumeMetrics / ProtoMetrics cannot be mixed.

Do not blindly preserve or remove that split.

Probe it safely against var203.

Use the same probe-and-fallback philosophy as D-010.

Determine:
- which families can coexist in one monitor
- which cannot
- whether rejection occurs at monitor creation or query time
- whether object scope changes compatibility
- whether fewer than 8 monitors can be used safely

No destructive VMS action.
Temporary probe monitors must be deleted.

If real evidence confirms the split is required:
- preserve it
- document it
- optimize elsewhere

If real evidence shows consolidation is possible:
- add probe-validated merge with fallback
- never assume all VMS builds behave the same

PHASE 5 — IMPLEMENT NVMe RANKING AND DRILL BATCHING

Use the proven DrillSession pattern where it fits.

Goals:
- stop head-slicing `/volumes/`
- rank by actual activity
- batch drill monitors when the API supports it
- add fallback when batch monitor shape cannot be split by object
- avoid per-object monitor/query explosions
- cache ranking
- throttle drill polling where semantically safe
- Space/manual refresh forces a refresh
- preserve protocol-specific row builders and metric semantics
- preserve cleanup

Do not force `vast_drill` into NVMe if its metric families make the shared abstraction incorrect.
Adapt or extend the shared layer only if the abstraction remains honest for NFS/SMB/S3 users.

Test first.

Add failing tests against the current implementation for:
- active volume beyond first 8 not selected
- drill entry API budget
- per-object query explosion
- ranking cache
- throttle
- manual refresh override
- batch fallback
- cleanup success
- cleanup error/interruption path
- real monitor-family constraint behavior represented in mock where appropriate

PHASE 6 — IMPLEMENT BLOCK FABRIC PERCENTAGE FIX (FR-C)

After proving the current denominator, correct it.

Primary workload mix denominator:
- READ
- WRITE
- metadata, if the BLOCK engine genuinely has a metadata workload category

Fabric:
- remains visible
- remains separately quantified
- does not contribute to READ/WRITE/MD percentages
- should have a visually separate bar/section if it does not already

Add literal unit tests:
- READ-only
- WRITE-only
- mixed READ/WRITE
- READ/WRITE + very large Fabric count
- zero workload + Fabric activity
- metadata case if applicable

For zero workload + Fabric:
- do not invent workload percentages
- Fabric should still be visible as activity
- primary workload mix should honestly show no workload mix

PHASE 7 — STANDARDIZE NVMe NAVIGATION AS THE FIRST FR-A PORT

Use NVMe as the first engine where we deliberately apply the new navigation standard.

Before changing keys, construct the desired COMMON NAV ROW.

Derive the common key map from existing engines, but standardize intentionally.

Preferred concepts:
- [q] Quit
- [o] Ops
- [l] Lat
- [n] Name
- [c] cNode where supported
- [v] View where supported
- [t] Tenant where supported
- [i] VIP where supported
- [x] Exit drill
- [space] Refresh

Unique protocol controls go AFTER the common set.

NFSv4.1 unique controls remain:
- [4] native NFSv4 telemetry
- [h] v4 hosts

Do not change NFS/SMB/S3 keys in this phase unless a shared helper requires a safe mechanical change.
Record their current deviations in the backlog for later normalization.

For NVMe:
- align any common semantics with the common map now
- preserve unique NVMe-specific controls after common controls
- update help/footer/panel labels consistently
- test narrow terminal rendering
- ensure no common control is truncated away
- avoid a key collision between sorting and drill navigation

If the common row cannot fit at historical width:
- improve shared layout/abbreviation deliberately
- do not silently raise width until the problem disappears
- preserve usability at narrow terminals

PHASE 8 — AUDIT AND FIX NVMe LATENCY UNITS (FR-B)

Trace every BLOCK/NVMe latency value from:
API response
→ raw unit
→ conversion
→ internal representation
→ rendering

For each displayed latency:
- prove the source unit
- prove the conversion
- state the user-facing unit choice
- add tests

Preferred presentation:
- milliseconds for values naturally in ms range
- microseconds for consistently sub-ms metrics where ms destroys useful precision
- formatting helper should avoid `0 ms` for a meaningful sub-ms value
- ns should be converted correctly before presentation unless a specific metric is genuinely best shown in ns

Examples of expected display behavior:
- 250 µs → either `250 µs` or `0.25 ms`, depending on panel policy
- 1,500 µs → preferably `1.50 ms`
- 2,000,000 ns → `2.00 ms`
- 0.4 µs should not become `0 µs`

Do not retrofit all engines in this phase.
Build shared unit helpers only if they are clearly reusable without changing existing semantics unexpectedly.

Record remaining protocol latency-unit audits in the backlog.

PHASE 9 — STARTUP UX FOR NVMe

The real SMB/S3 tests proved startup can take tens of seconds.

NVMe must not leave users staring at a blank/frozen terminal.

If NVMe startup can block materially:
- render `Gathering initial metrics, please stand by...` before blocking startup work
- use the shared loading helper if compatible
- status must reach the terminal first
- errors must replace the loading state cleanly
- normal dashboard replaces it automatically

If your performance work makes first-frame startup effectively immediate, keep the mechanism but avoid unnecessary visible flicker where practical.

This startup behavior is becoming a cross-protocol invariant.

Do not retrofit NFS in this phase unless the shared helper change automatically and safely applies.

PHASE 10 — TESTING / QUALITY EXPANSION (FR-D)

Add the necessary NVMe coverage.

At minimum cover:
- ranking correctness
- call budgets
- batching
- fallback
- throttle
- manual refresh
- cleanup
- cleanup interruption if fixed
- Fabric percentage math
- Fabric-only activity
- navigation key map
- footer ordering
- narrow terminal rendering
- latency conversion
- latency formatting
- startup loading interstitial
- no regression to cluster headline behavior
- monitor-family probe/fallback if implemented

Use the real-cluster findings to improve mock fidelity where needed.

Do not weaken existing tests.

Then run:
./scripts/validate.sh

Require:
- current Python PASS
- Python 3.8 PASS
- zero skipped
- openssl suites actually run
- documentation links valid

PHASE 11 — REAL-VMS AFTER VALIDATION ON var203

After the mock/full gate is green, validate the actual implementation on var203.

Use only the real cluster currently authorized:
var203.selab.vastdata.com

Do not use var204.

Measure BEFORE versus AFTER:

STARTUP
- visible loading status timing
- first normal frame
- API calls

CLUSTER
- monitor count
- queries/refresh
- total calls/30 s
- any monitor-family fallback

DRILL
- candidate count
- selected active volumes
- entry API calls
- wall-clock
- monitor count
- steady-state cadence
- rank-cache re-entry
- manual refresh

DISPLAY
- READ/WRITE/MD percentages
- separate Fabric bar
- latency units and representative displayed values
- navigation/footer behavior
- narrow terminal behavior if practical

CLEANUP
- clean q
- full process exit
- exact monitor IDs created by this session
- none left live afterward

If the cleanup-interruption fix was implemented:
- safely reproduce the old SIGTERM scenario
- verify all exact session monitors are removed
- never SIGKILL

PHASE 12 — UPDATE DURABLE DOCUMENTATION

Update docs/REFACTOR_HANDOFF.md with:
- SMB/S3 now complete if that was not already recorded
- var204 unavailable until owner restores it
- FR backlog and current status
- NVMe before/after
- cleanup-interruption status
- testing count
- startup UX status
- navigation-standardization status
- latency-audit status
- remaining work

Create a new decision record only if a genuinely durable architectural decision was made.

Good candidates, only if evidence justifies them:
- standardized navigation contract
- shared latency presentation policy
- NVMe monitor-family compatibility policy
- cleanup lifecycle contract

Do not rewrite existing decision records merely to add implementation status.

PHASE 13 — PREPARE EXTERNAL HANDOFF

At the end, create/update:

docs/CLAUDE_HANDOFF.md

This is specifically for transferring the result to ChatGPT through an approved external workflow.

Keep it concise but complete.

Include:
- branch / HEAD
- current uncommitted state
- objective
- exact changes
- real-VMS evidence
- mock/test evidence
- before/after API counts
- Fabric percentage correction
- navigation decisions
- latency-unit findings
- cleanup findings
- validation counts
- known risks
- open questions
- recommended next step

Do not include:
- passwords
- tokens
- auth data
- raw proprietary logs
- unnecessary internal environment detail

FINAL REPORT

Return one complete report:

OPSTAT NVMe / SAFETY / UX REFACTOR STATUS

Starting state:
Existing SMB/S3 work verification:
FR backlog recorded:
var204 operational note:
Cleanup-interruption root cause:
Cleanup-interruption fix:
NVMe real BEFORE:
NVMe family-mixing probe:
NVMe implementation:
Fabric percentage fix:
Navigation standardization:
Latency audit:
Startup/loading UX:
Tests added:
Mock before/after:
Real NVMe before/after:
API calls/30s before/after:
Drill entry before/after:
Monitor count before/after:
Cleanup validation:
Validation gate:
Python 3.8:
Current Python:
Files changed:
Docs updated:
Decision records added:
Known risks:
Outstanding FR work:
Recommended next engineering milestone:
External handoff file:

Do not commit.
Do not push.
Do not merge.
Do not create a PR.

You may make all normal code/test/doc changes necessary to complete this full work package.

If a directly-related defect is discovered, fix it in this same pass when safe and testable.

Only stop early for the hard-stop conditions stated at the beginning.