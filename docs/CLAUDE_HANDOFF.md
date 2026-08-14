Approved to delete ONLY your leaked monitor:

2299  adhoc_opstat_s3_vip_55_1786663781

Do not touch the other pre-existing/concurrent adhoc_opstat_* monitors.

After deleting 2299:

1. Confirm by GET that 2299 no longer exists.
2. Confirm none of the monitor IDs created by your own test sessions remain.
3. Do not perform a general adhoc_opstat_* cleanup.

Then proceed with the SMB/S3 drill refactor in one complete implementation pass.

You now have both mock evidence and real-cluster BEFORE measurements. Use both.

REAL-CLUSTER BASELINE TO PRESERVE

SMB view:
- 145 candidates
- 5 rank monitors
- 18 entry API calls
- 104 s ranking wall-clock
- loading UI present
- clean-q cleanup verified

SMB tenant:
- 35 candidates
- 2 rank monitors
- 9 entry API calls
- 34 s ranking
- cleanup verified

S3 bucket:
- 145 candidates
- 5 rank monitors
- 18 entry API calls
- 47 s ranking
- steady-state re-query approximately every 5 s, unthrottled
- cleanup verified

S3 tenant:
- 35 candidates
- 2 rank monitors
- 9 entry API calls
- 59 s ranking
- cleanup verified

S3 VIP:
- topn ranking confirmed
- 192.168.* filtering confirmed
- topn-only fallback remains unverified because the real cluster accepts VIP monitors
- preserve this path exactly unless a regression test proves otherwise

IMPLEMENTATION SCOPE

Port SMB and S3 view/tenant/bucket drill mechanics to vast_drill.DrillSession.

Do not broaden the work into:
- NVMe
- NFS
- delegation lookup
- exporter changes
- headline-monitor redesign
- cNode batching unless required for correctness
- cleanup-interruption defect repair

The cleanup-interruption issue is separate follow-up work.

SMB

Port:
- view ranking
- tenant ranking
- drill monitor creation
- rank caching
- re-poll throttle
- forced refresh
- shared loading helper where appropriate

Use the proven NFSv3/NFSv4.1 vast_drill pattern.

S3

Port:
- bucket ranking
- tenant ranking
- drill monitor creation
- rank caching
- re-poll throttle
- forced refresh

Do NOT route the VIP path through DrillSession unless strictly necessary.

Preserve:
- VIP topn ranking
- 192.168.* filtering
- topn-only fallback
- VIP-specific refresh behavior

CNodes

Keep SMB/S3 cNode behavior unchanged in this pass unless the port requires touching it.

Do not opportunistically batch cNodes just for symmetry.

STARTUP / LOADING UX

The real-cluster measurements exposed a UX requirement that now applies beyond drill ranking.

Some startup paths take approximately 30 seconds before the first usable frame appears.

Users must not stare at an apparently frozen terminal during blocking startup work.

Before any startup phase that can materially block before the first normal frame, paint a visible status frame first.

Preferred wording:

  Gathering initial metrics, please stand by...

Use wording that accurately reflects the actual work being performed.

For drill-specific blocking operations, retain specific messages such as:

  Ranking VIEW activity, please stand by...
  Loading TENANT drill-down, please stand by...

Do not add a visible interstitial if the corresponding work is effectively immediate after the refactor.

The desired behavior is:
- no blank/frozen terminal during multi-second startup
- status must reach the terminal BEFORE blocking API work
- normal dashboard replaces it automatically when ready
- errors must still surface cleanly
- footer/navigation behavior must remain correct once the normal frame appears

Prefer a shared helper/path over engine-specific duplicated status logic.

Investigate whether the existing with_loading_status helper can safely cover startup as well as drill entry.

Do not introduce threading/concurrency for this.

PERFORMANCE TARGET

The goal is not merely code reuse.

Measure actual before/after behavior.

Expected target:

SMB view:
18 calls -> approximately 4 calls

S3 bucket:
18 calls -> approximately 4 calls

SMB/S3 tenant:
9 calls -> approximately 4 calls

Re-poll:
~1 query every 5 s -> ~1 query every 15 s

Re-entry:
full rank scan -> cached rank result with no new rank monitors inside cache TTL

The exact real-cluster wall-clock may vary significantly, so report both:
- API-call count
- measured elapsed time

Do not claim a 4-second result unless the real cluster produces it.

TEST-FIRST REQUIREMENTS

Before changing implementation, add regression tests that fail against the current HEAD for:

1. SMB view entry API-call budget.
2. S3 bucket entry API-call budget.
3. Ranking correctness with busy objects beyond the first 32.
4. Rank cache on re-entry.
5. Re-poll throttle.
6. Space/manual refresh bypasses throttle.
7. Batch monitor fallback.
8. Ranking-monitor cleanup on success.
9. Ranking/drill cleanup on error.
10. S3 VIP topn behavior unchanged.
11. S3 VIP 192.168.* filtering unchanged.
12. S3 VIP topn-only fallback unchanged via mock rejection.
13. Loading/status frame rendered before blocking drill work.
14. Startup "Gathering initial metrics..." frame rendered before any proven blocking startup path.

Prove the budget/ranking tests fail against the pre-port implementation.

Do not weaken existing tests.

VALIDATION

After implementation:

1. Run targeted SMB/S3 drill tests.
2. Run API-efficiency tests.
3. Run the complete ./scripts/validate.sh gate.
4. Require:
   - current Python green
   - Python 3.8 green
   - zero skips
   - openssl-backed suites actually running
5. Exercise SMB and S3 in a PTY against the mock.
6. Then validate against the real VMS.

REAL-VMS AFTER VALIDATION

Use VAST_PASSWORD from the environment.

Do not put the password in argv, logs, docs, commits, or handoff files.

Run real:

SMB:
- view
- tenant

S3:
- bucket
- tenant
- VIP regression sanity check

For each real run capture:

- startup time to first visible status
- startup time to first normal frame
- candidate count
- rank API calls
- total entry API calls
- ranking elapsed time
- steady-state query cadence
- rank-cache behavior on re-entry
- cleanup result keyed to this session's exact monitor IDs

Verify no monitor created by the test session remains after clean exit.

Do not use SIGKILL.

Prefer clean q and wait for the process to exit fully.

If SIGTERM is required, wait for cleanup to complete and verify exact monitor IDs afterward.

Do not fix the cleanup-interruption defect during this work unless it prevents safe completion.

DOCUMENTATION

When implementation and validation are complete:

- update docs/REFACTOR_HANDOFF.md
- update any relevant decision/status documentation only if a durable decision changed
- do not rewrite settled decision records just to record implementation progress
- record the cleanup-interruption defect as outstanding if it is not already durable
- record the new startup/loading UX behavior if it becomes a general invariant

REPORT

Give me one complete report:

SMB/S3 VAST_DRILL IMPLEMENTATION AND REAL-VMS VALIDATION

Cleanup of 2299:
Files changed:
Tests added:
Pre-fix regression proof:
SMB implementation:
S3 implementation:
S3 VIP preservation:
Startup/loading UX:
Mock before/after:
Real SMB view before/after:
Real SMB tenant before/after:
Real S3 bucket before/after:
Real S3 tenant before/after:
Real S3 VIP sanity:
Steady-state API cadence:
Rank-cache behavior:
Monitor cleanup:
Validation gate:
Python 3.8:
Current Python:
Known remaining risks:
Cleanup-interruption follow-up:
Documentation updated:
Recommended next engineering item:

Do not commit.
Do not push.
Do not merge.
Do not create a PR.

You may fix defects you discover that are directly caused by, or required for, this SMB/S3 drill port and startup/loading UX work.

If you encounter a destructive VMS action, production-impacting change, major concurrency/architecture change, or scope expansion beyond these areas, stop and ask.

Otherwise, carry this entire implementation, test, and real-VMS validation cycle through before returning.