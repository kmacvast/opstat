Continue the opstat refactor from the current working tree.

IMPORTANT WORKING STYLE
=======================

I am relaying your output between machines, so round trips are expensive.

Work in LARGE, coherent phases. Do not stop after every small implementation decision, test failure, measurement, or discovery.

Within the approval boundaries already established in AGENTS.md and .claude/rules/, investigate, implement, test, measure, fix regressions you introduce, update documentation, and continue through the work package below.

If you discover an ordinary L2/L3 implementation choice, make the best engineering decision, document it in your final report, and keep going.

STOP only for a genuine L1 decision, destructive action, credential/security issue, unexpected risk to VMS, or a decision that materially changes product semantics beyond what I have specified here.

Do not commit, push, merge, tag, create a PR, or modify main. I want the entire work package completed and validated in the working tree first.

REAL VMS AVAILABILITY
=====================

var203.selab.vastdata.com is the ONLY real cluster currently available.

Treat var204.selab.vastdata.com as unavailable until I explicitly say otherwise.

Use var203 for real validation where the required protocol/data is available.

Continue using the established real-VMS safety procedure:

- prefer VAST_PASSWORD/environment credentials rather than putting passwords in argv
- no hand-written mutating VMS requests
- temporary opstat monitors are allowed only through the application's normal behavior
- clean-q is the preferred exit
- never SIGKILL
- verify cleanup using the exact monitor IDs created by the test session
- do not sweep unrelated/pre-existing adhoc_opstat monitors
- do not put credentials, cluster identifiers from scratch logs, API logs, or temporary measurement artifacts into commits
- /tmp or another untracked scratch location is appropriate for transient test artifacts

The cleanup-interruption defect discovered during previous testing is now known evidence. Do not knowingly create another leaked monitor just to reproduce it.

CURRENT BASELINE
================

The current uncommitted working tree already contains completed work from previous phases, including:

- SMB/S3 vast_drill port
- SMB/S3 ranking/cache/throttle improvements
- S3 VIP preservation work
- startup loading UX
- shared three-phase startup status helper
- startup UX across all five engines
- additional SMB/S3 drill tests
- startup/render tests
- mock VMS improvements
- handoff updates

Last reported gate:

466 passed, 0 failed, 0 skipped, 0 error
on both current Python 3.14.6 and Python 3.8.

Do not discard, reset, stash away, or overwrite this work.

Before beginning, inspect git status/diff and reconcile this prompt against the ACTUAL tree. The repository is authoritative.

WORK PACKAGE
============

Work through the following items in a sensible dependency order.

You may reorder them if doing so reduces risk or duplicate work.

============================================================
PHASE A — REAL-VMS STARTUP UX VALIDATION
============================================================

Validate the new three-phase startup UX against var203 on the protocols that can be exercised there.

I specifically want to know that the user sees feedback BEFORE a slow API call rather than staring at an apparently frozen terminal.

Expected progression:

1. Connecting to <VMS>...
2. Preparing metrics on <cluster>...
3. Gathering initial metrics...

Exact wording may already differ slightly. Preserve the implementation unless real testing exposes a UX problem.

Verify:

- first status frame paints before the first slow operation
- later status changes actually become visible
- footer remains visible
- terminal does not appear frozen during a long startup
- normal dashboard replaces startup status
- clean-q still cleans up all session monitors
- no regression at narrow widths

Do not spend excessive time waiting on protocols that var203 cannot meaningfully exercise.

============================================================
PHASE B — QUIT/CLEANUP SAFETY AND UX
============================================================

Address the cleanup-interruption defect discovered during real-VMS testing.

Observed behavior:

A SIGTERM during a slow cleanup drain could leave an adhoc_opstat monitor behind, and the user could see a long silent pause during shutdown.

Treat these as related but separate concerns:

1. shutdown correctness
2. shutdown user feedback

Investigate the cleanup lifecycle in vast_common and all engine-specific cleanup paths.

Requirements:

- normal q exit must attempt to delete every temporary monitor owned by the session
- SIGINT/SIGTERM/SIGHUP cleanup must remain safe
- cleanup should not silently abandon the remainder of the session's monitors because one delete is slow or fails
- failed deletes must be surfaced truthfully
- no unrelated monitors may be deleted
- monitor ownership must remain session-specific
- no SIGKILL-based behavior is expected to be recoverable
- avoid introducing threads/concurrency merely to solve this unless absolutely necessary; that is still an L1 architectural decision

Add shutdown UX similar in spirit to startup.

If cleanup can take noticeable time, render something like:

    Cleaning up temporary monitors, please stand by...

If the number is safely known, something like:

    Cleaning up 4 temporary monitors, please stand by...

is better.

Do not fake progress percentages.

If practical without making the implementation brittle, update the count as cleanup progresses. If that requires ugly coupling, use a truthful static message instead.

Add regression tests for:

- multiple monitors cleaned
- cleanup continues after one delete failure
- exact session monitors only
- error/warning surfaced
- signal path
- q path
- status cleared/finalized appropriately
- no monitor leak in normal tested paths

Use mock evidence for deliberately induced failures.

Use var203 for a SAFE real validation of normal clean-q cleanup. Do not intentionally interrupt cleanup on the real cluster merely to reproduce the known leak.

============================================================
PHASE C — NVME PERFORMANCE REFACTOR
============================================================

NVMe is the next major performance target.

The handoff has identified it as the remaining engine with the old expensive drill architecture, approximately 467+ API calls / 30 seconds in prior measurements.

Do a fresh read-only audit against the CURRENT code before modifying it.

Characterize:

- startup API cost
- normal refresh API cost
- volume discovery behavior
- ranking behavior
- drill monitor creation
- per-object versus batched monitors
- BlockMetrics / VolumeMetrics / ProtoMetrics usage
- monitor-family mixing assumptions
- cache/throttle behavior
- cleanup behavior
- any head-slicing of /volumes/
- anything proportional to volume count

Use the SMB/S3/NFS vast_drill work as a pattern, but do not blindly port it.

IMPORTANT:

Re-verify the historical assumption that BlockMetrics / VolumeMetrics / ProtoMetrics cannot safely share the desired monitor layout.

Use probe-and-fallback where appropriate.

Do not fabricate compatibility based on the mock.

Where the real VMS can answer the question safely, var203 evidence outranks mock assumptions.

Performance goals:

- eliminate unnecessary per-volume/per-object API calls
- rank by activity rather than API ordering
- batch monitor creation/query where VMS semantics allow
- cache rankings where appropriate
- throttle expensive drill refreshes
- preserve manual refresh behavior
- preserve responsiveness
- preserve all existing metric semantics
- leave no temporary monitors behind

Capture BEFORE and AFTER API-call counts.

Wall-clock is useful but secondary because var203 latency is variable. API-call counts and request shape are the primary deterministic regression criteria.

Add API-efficiency tests so these improvements cannot silently regress.

============================================================
PHASE D — BLOCK FABRIC PERCENTAGE CORRECTION
============================================================

Implement this backlog FR:

The Block/NVMe screen currently allows Fabric count to distort the primary workload percentages.

That is wrong for the intended presentation.

The PRIMARY workload percentage calculation should include only actual workload categories:

- Read
- Write
- Metadata, if the applicable telemetry genuinely has a metadata category

Fabric must NOT be part of the denominator used to calculate Read/Write/MD percentages.

Fabric should remain visible as its own separate metric/bar on the screen.

Before changing the calculation:

- trace the exact endpoint/metric fields feeding Read, Write, Fabric, and any Metadata value
- verify units and semantics
- determine whether Fabric overlaps with or is independent from the workload operation categories
- do not assume based on variable names

Then implement the presentation rule above.

Add tests with deliberately disproportionate Fabric values so the regression is obvious.

Example conceptually:

Read = 60
Write = 40
Fabric = 900

Primary workload percentages must remain:

Read 60%
Write 40%

not:

Read 6%
Write 4%
Fabric 90%

Fabric remains displayed separately.

Use the actual opstat metric model rather than forcing this exact toy structure if the real fields differ.

============================================================
PHASE E — NAVIGATION STANDARDIZATION
============================================================

Begin the navigation-standardization FR across ALL five protocol engines.

Goal:

The common navigation controls should have the same key, meaning, wording, ordering, and visual style everywhere possible.

Protocol-specific controls are appended AFTER the common controls.

Inventory every interactive key and footer/help-bar presentation across:

- NFSv3
- NFSv4.1
- SMB
- S3
- NVMe-oTCP

Build an explicit before matrix first.

Then define a canonical navigation vocabulary based on the best existing behavior.

Requirements:

- same concept = same key everywhere practical
- same key must not mean different things across engines unless unavoidable
- same concept should use the same label text
- common controls should appear in the same relative order
- protocol-specific controls should come at the end
- preserve q for Quit
- preserve common x/l/v/t/etc semantics where they genuinely represent the same action
- VIP MUST standardize on lowercase `i`
- do not use `v` for VIP
- NFSv4.1-specific controls such as `4` and `h` remain protocol-specific and appear after common controls
- keep narrow-terminal behavior usable
- do not silently truncate controls in a misleading way
- update both key handling and displayed footer together

Do not merely make the footer LOOK consistent while leaving conflicting key handlers behind.

Search documentation/tests for key references before changing them.

Add regression tests for the canonical mapping and footer ordering so drift between engines becomes difficult.

If one protocol genuinely cannot conform because of a semantic collision, preserve correctness and document the exception rather than forcing a bad abstraction.

============================================================
PHASE F — LATENCY UNIT AND CONVERSION AUDIT
============================================================

Perform the latency-unit FR across all engines.

This is not merely a formatting task.

The VAST APIs/exporter paths can expose latency in different units depending on endpoint/family.

Audit EVERY user-visible latency value.

For each latency source establish:

- endpoint / telemetry family
- source field
- native unit returned by VAST
- whether it is instantaneous, aggregate, sum/count derived, cumulative-derived, etc.
- conversion currently applied
- unit displayed to the user
- whether that conversion is proven by real VMS evidence, code contract, or only mock assumption

Preferred presentation:

- milliseconds for ordinary user-facing latency where that produces useful values
- microseconds where the values are inherently sub-millisecond and ms would destroy useful precision
- never display a bare latency number whose unit is ambiguous

Triple-check arithmetic.

Pay particular attention to the already-settled NFSv4.1 decision that Nfs4Metrics latency is microseconds. Do not accidentally reinterpret that source as milliseconds.

Rules:

- never infer unit from magnitude
- never fabricate semantics
- never silently convert an unproven unit
- if a source unit cannot be established, mark it as unverified and preserve existing behavior until evidence exists
- zero latency and unavailable latency are different states
- preserve enough precision that a legitimate non-zero latency does not render as 0.00

Use var203 where it can safely establish real endpoint semantics.

Add conversion/unit tests with values chosen to catch 1000x and 1,000,000x errors.

If this audit reveals an existing unit bug, fix it only when the source unit is proven and add a regression test demonstrating the prior error.

Document the resulting unit contract somewhere durable if the audit establishes reusable facts.

============================================================
PHASE G — TEST ARCHITECTURE / BACKLOG FR
============================================================

One backlog item said:

"We need to add unit testing to the repo. We need to go back and put tests in place to catch us up to current build. Every time new code is going to be committed, run the entire testing suite."

The repository now HAS substantial unit coverage and ./scripts/validate.sh, so do not create redundant infrastructure merely because the old FR wording predates the current scaffold.

Instead, assess the CURRENT state against the intent of that FR.

Determine:

- which major production modules still have weak/no direct tests
- which protocol behaviors are under-covered
- whether every engine has API-efficiency regression coverage
- whether render/navigation behavior is covered
- whether cleanup lifecycle is covered
- whether Python 3.8 is mechanically enforced
- whether current-Python is enforced
- whether the full suite is the documented pre-commit quality gate
- whether CI and local validation disagree materially

Fill the HIGHEST-VALUE holes exposed by the work in this package.

Do not chase coverage percentage for its own sake.

Tests should protect behavior, semantics, API budgets, cleanup, compatibility, and regressions that have actually mattered.

If the existing AGENTS.md / validation contract already satisfies "full suite before commit," preserve it rather than inventing a second system.

============================================================
PHASE H — DOCUMENTATION AND BACKLOG
============================================================

Update docs/REFACTOR_HANDOFF.md to reflect actual completed work, measurements, open defects, and remaining work.

Also make sure these FRs are durably represented as either completed, partially completed, or remaining:

1. navigation shortcut standardization
2. latency unit clarity/conversion audit
3. Block Fabric excluded from workload percentage denominator
4. comprehensive unit/regression testing and full-suite-before-commit policy

Do not create bureaucracy for its own sake.

If the repo has no lightweight backlog location, use the existing handoff/open-work structure rather than inventing a product-management system.

Do not rewrite settled decision records unless new evidence genuinely requires reopening one under the documented L1 process.

============================================================
QUALITY AND EVIDENCE REQUIREMENTS
============================================================

For each meaningful defect/performance fix:

- add a regression test
- where practical, prove the test fails against the prior behavior
- distinguish pre-existing defects from introduced regressions
- never weaken/delete/skip a test merely to get green
- mocks establish deterministic behavior, not real VMS semantics
- real VMS evidence outranks mock assumptions
- preserve Python 3.8 compatibility
- preserve current Python compatibility
- avoid new runtime dependencies

Run targeted tests throughout rather than waiting until the end.

At the end run the complete:

    ./scripts/validate.sh

A final PASS is mandatory before calling the work package complete.

Also perform PTY/manual render exercises where useful for navigation/startup/cleanup behavior.

REAL-VMS FINAL VALIDATION
=========================

After implementation and local/mock validation, perform a coherent real-var203 validation pass rather than many tiny cluster sessions.

Batch the scenarios where practical.

Validate at least:

- startup UX
- SMB drill still healthy after unrelated changes
- S3 bucket/tenant and VIP preservation
- NVMe normal screen
- NVMe drill behavior/performance
- Block percentages/Fabric presentation
- navigation keys affected by the standardization
- clean-q cleanup

For every real session:

- capture session-specific monitor IDs
- verify all of that session's temporary monitors are gone afterward
- do not touch unrelated monitors

If var203 lacks data needed to prove a semantic claim, say UNVERIFIED rather than manufacturing confidence.

SCOPE GUARDS
============

Do NOT:

- use var204
- modify main
- commit
- push
- merge
- tag
- create a PR
- force-push
- delete unrelated VMS monitors
- change VMS configuration
- add threads/concurrency without stopping for approval
- add new runtime dependencies without stopping for approval
- alter settled telemetry semantics without evidence and the documented approval process
- expand into unrelated feature work

You MAY:

- modify the current working tree
- add/update tests
- add/update development scripts if genuinely needed
- update handoff/backlog documentation
- use the mock VMS
- use var203 safely
- create temporary untracked/scratch measurement tools outside the repo
- fix defects you introduce
- fix directly-related pre-existing defects when required to complete this work safely and when behavior is clear

STOPPING RULE
=============

Do not come back to me simply because one phase finished.

Continue through the entire work package.

If a later phase is blocked, complete the independent phases that are not blocked before returning.

Return early only for:

- a genuine L1 decision requiring my approval
- a destructive operation
- a credential/security problem
- evidence that the requested behavior would be technically wrong
- a real-VMS safety issue that cannot be resolved read-only
- an architectural fork where proceeding would materially constrain the product

Otherwise make the engineering decision, test it, document it, and continue.

FINAL REPORT
============

When the entire work package is complete, give me ONE consolidated report.

Include:

1. Starting git state and ending git state
2. Exact files changed/added
3. Startup UX real-VMS results
4. Cleanup defect root cause and fix
5. Cleanup UX behavior
6. NVMe architecture before
7. NVMe architecture after
8. NVMe API calls before/after
9. NVMe real-VMS results
10. Block Fabric percentage root cause and correction
11. Navigation before matrix
12. Canonical navigation mapping after
13. Any unavoidable protocol-specific navigation exceptions
14. Latency-unit audit table:
      protocol
      screen/metric
      source endpoint/family
      native unit
      conversion
      displayed unit
      evidence
15. Any latency bugs found and fixed
16. Unit/regression tests added
17. Test-first failure proofs
18. PTY/render validation
19. Real-var203 validation
20. Session-monitor cleanup verification
21. Final ./scripts/validate.sh output and exact counts for BOTH Python versions
22. Pre-existing defects encountered
23. New risks / unresolved items
24. Documentation/backlog updates
25. Recommended next engineering work
26. Proposed commit breakdown for ALL currently uncommitted work

For the proposed commit breakdown, group the accumulated working-tree work into logical commits with:

- proposed commit subject
- files belonging to it
- why they belong together
- dependencies/order between commits

DO NOT actually commit them.

The goal is that after this response I can review the whole package and, if satisfied, give you one approval to create the logical commits.

Start by reconciling this work package against the actual current tree and current handoff, then proceed autonomously.