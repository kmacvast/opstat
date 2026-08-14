The work-laptop var203 probe completed successfully enough to give us useful
real-VMS evidence, but I am currently tethered through a weak phone connection
while riding in a car.

DO NOT use any wall-clock timing from my current Mac runs as performance
evidence. The network path is badly distorting those numbers.

API call counts, API response shapes, monitor behavior, object_id behavior, and
cleanup results remain valid evidence.

I want to continue without making the bad WAN path a blocker.

REAL-VMS EVIDENCE ALREADY RETURNED
==================================

The automated var203 probe produced:

cNode batch:
  create      PASS
  query       PASS
  splittable  PASS
  ids         [4, 3]
  rows/object 120 each

VIP batch:
  create      PASS
  query       PASS
  splittable  FAIL
  object ids  [755,55,56,57]
  rows/object 0

blockhost batch:
  create      PASS
  query       PASS
  splittable  FAIL
  object ids  [1,2,3,4]
  rows/object 0

cNode rank monitor:
  accepted PASS
  read_req delta rates:
    object 4 = 1062.353/s
    object 3 = 0.0/s

Latency:
  NFS4Common proven-us reference returned 0
  BlockMetrics read_latency__avg returned 488.99
  host_view returned 0 latency series

Therefore latency units are NOT proven by this run.
Do not infer units from 488.99 alone.

Cleanup:
  monitor ids created:
    2345 2346 2347 2348 2349 2350

  exact-id cleanup PASS
  all six confirmed deleted

INTERPRETATION BOUNDARY
=======================

Treat these as real-VMS findings:

1. cNode multi-object BlockMetrics batching is supported and object_id
   splittable on var203.

2. VIP and blockhost monitors can be created and queried with multiple ids,
   but the tested result is NOT object_id-splittable.

3. Do not use the optimized batch-display path for VIP or blockhost merely
   because monitor creation succeeds.

4. Preserve/use per-object fallback for those modes unless further evidence
   proves another correct representation.

5. cNode activity ranking using BlockMetrics read_req deltas is viable on
   var203.

6. Latency units remain UNVERIFIED from this run.

7. Exact-session monitor cleanup works.

First reconcile these findings against the current implementation.

If the implementation already falls back correctly for VIP/blockhost when the
batch response cannot be split, keep that behavior and add/adjust literal
regression coverage based on the real payload semantics.

Do not optimize around a batch monitor that returns no per-object rows.

TIMING / INTERACTIVE VALIDATION CHANGE
======================================

I do NOT want to continue doing interactive timing tests from this work Mac
while it is phone-tethered.

Instead, prepare ONE automated validation script that I can run on the Linux
lab server located close to var203.

The Linux lab server should be used for:

- meaningful wall-clock measurements
- API call counts
- drill entry behavior
- ranking/cache behavior
- refresh throttle
- manual refresh
- startup stages
- shutdown stages
- navigation key-driving
- Fabric screen capture
- exact monitor cleanup

I cannot interact with the TUI reliably right now.

The script must drive opstat itself automatically.

AUTOMATED LAB VALIDATOR
=======================

Create a single self-contained validation driver under something like:

  scripts/var203_validation/run_var203_validation.py

Use the exact repository conventions and Python 3.8 compatibility rules.

Prefer Python rather than a complicated shell script because the validator
needs PTY/key driving, timing, API-log parsing, cleanup accounting and
structured output.

The driver should:

1. Verify prerequisites:
   - repo root
   - correct branch/HEAD
   - VAST_PASSWORD or VAST_TOKEN present
   - DNS/connectivity to var203
   - Python version
   - opstat executable exists

2. Never print the credential.

3. Target ONLY:
     var203.selab.vastdata.com

4. Run the existing automated probe first or integrate its safe checks.

5. Drive opstat through a PTY without human interaction.

6. Never SIGKILL.

7. Prefer clean q.

8. If a timeout occurs:
   - send a graceful termination only if necessary
   - wait for cleanup to finish
   - do not close the PTY while cleanup is still running

9. Track exact monitor IDs created by each session from its own API log.

10. Verify those exact ids are gone afterward using per-id GET.

11. Never enumerate-and-delete other adhoc_opstat monitors.

12. Leave raw API logs in /tmp only.

13. Produce ONE concise result file, for example:

     /tmp/opstat-var203-validation.txt

14. Also print a short final summary to stdout.

VALIDATION SCENARIOS
====================

Automate as much of Steps 2-4 from the existing README as possible.

A. NVMe baseline dashboard

Start opstat:

  --block --nvme-over-tcp
  --vms var203.selab.vastdata.com
  --user admin
  --log-api-calls

Automatically capture:

- time process starts
- time first "Connecting" frame appears
- time "Preparing metrics" appears
- time "Gathering initial metrics" appears
- time normal dashboard first appears
- startup monitor POST count
- startup query count

Do NOT assume timing is valid if executed from the phone-tethered Mac.
The script output should identify hostname so we know where it ran.

B. NVMe cNode drill

Automatically send:

  c

Wait until the drill panel is visibly rendered.

Capture:

- candidate count if observable
- selected names
- ranking monitor calls
- display monitor calls
- entry API-call count
- entry wall-clock
- whether batch layout or fallback layout was used
- number of queries during ~45 seconds
- effective polling interval

Then send:

  space

Confirm an immediate forced query occurred.

Exit drill with:

  x

C. NVMe VIP drill

Automatically send:

  i

Capture:

- selected VIPs
- monitor layout
- whether engine correctly falls back away from an unsplittable batch
- entry calls
- query count
- panel rendered successfully

Exit with x.

D. NVMe Host / blockhost drill

Automatically send:

  h

This is especially important because blockhost was not modeled by the mock.

Capture:

- object count
- selected hosts
- monitor layout
- confirmation that the unsplittable multi-id result does NOT cause bogus
  per-host rows
- fallback behavior
- entry calls
- query cadence

Exit with x.

E. Navigation

Automatically test:

  i = VIP
  x = Exit drill
  space = Refresh

Confirm old bindings do not navigate:

  v must NOT mean VIP on NVMe
  p must NOT mean Exit drill

Do not quit accidentally while testing them.

F. Fabric / workload panel

Capture a text frame from the NVMe main screen while block load is active.

Include enough rendered text in the output for us to verify:

- Read %
- Write %
- Reclaim/other real workload category if present
- Fabric count/bar/share
- combined latency

We need to prove Fabric is visible but excluded from the workload denominator.

G. Shutdown

Send clean:

  q

Capture:

- "Cleaning up N temporary monitors..." message
- process exit code
- shutdown wall-clock
- exact monitor IDs created
- exact IDs deleted
- per-id verification afterward
- NONE remaining from this session

OTHER PROTOCOL STARTUP/NAV CHECKS
=================================

If practical without turning the script into a monster, also automate short
startup/footer/clean-q checks for:

- SMB
- S3
- NFSv3
- NFSv4.1 only if var203 actually supports the required NFS paths

The important evidence is:

- three startup messages appear in order
- footer appears
- canonical navigation subset/order is visible
- clean q cleanup succeeds

Do not make NFS validation block the NVMe pass if var203 lacks equivalent test
workloads.

LOAD GENERATORS
===============

Detect whether the committed systemd/loadgen helpers are available on the Linux
lab host.

If they are available, print their state and optionally start only the
documented lab load generators required for validation.

Do not make undocumented system changes.

If starting loadgen requires privilege or would change machine configuration,
do not do it automatically.

Instead print exactly what I need to run first.

RESULT FORMAT
=============

The generated result file should end with:

VAR203 AUTOMATED VALIDATION SUMMARY

Host running validation:
Branch:
HEAD:
Target VMS:
Start:
End:

PROBE
cnode batch:
vip batch:
blockhost batch:
cnode ranking:
latency reference:
cleanup:

NVME STARTUP
connecting frame:
preparing frame:
gathering frame:
dashboard:
startup wall-clock:
startup API calls:

NVME CNODE
selected:
entry calls:
entry seconds:
batch/fallback:
queries:
manual refresh:
result:

NVME VIP
selected:
entry calls:
batch/fallback:
result:

NVME HOST
selected:
entry calls:
batch/fallback:
result:

FABRIC
rendered workload:
fabric:
percentage validation:
latency rendered:

NAVIGATION
i:
v:
x:
p:
space:

SHUTDOWN
cleanup frame:
exit:
ids created:
ids deleted:
ids remaining:

OTHER PROTOCOL UX
NFSv3:
NFSv4.1:
SMB:
S3:

PASS:
FAIL:
UNVERIFIED:

FILES TO RETURN:
...

IMPLEMENTATION RULES
====================

You are currently back on the personal development laptop.

You CANNOT execute this lab script here.

Build and test its mechanics locally using the mock/PTY support where possible.

Do not invent real VMS results.

Do not commit.
Do not push.

At the end:

1. Reconcile the real probe evidence above into the current code/tests/docs
   where it is already decisive.

2. Build the automated Linux-lab validation script.

3. Test the script mechanics locally without contacting a VAST cluster.

4. Tell me exactly which files changed.

5. Give me the exact git instructions needed to make the script available on
   the work/lab side when I approve publication.

6. Tell me ONE command to run on the Linux lab server once the branch is
   available there.

Continue autonomously through this package.

Do not stop merely because the remaining timing validation must run elsewhere.