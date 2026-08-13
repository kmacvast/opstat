# Testing and evidence

Permanent rule for `opstat`. Tests are part of implementation, not a follow-up,
and a claim without evidence is not a result.

Short form lives in [AGENTS.md](../../AGENTS.md).

## Why this rule exists

**A green run can be a lie.** 171 of the 400 tests are gated on the `openssl`
binary by module-level `pytestmark` — `tests/mock_vms.py` needs it to generate a
throwaway TLS certificate. Without `openssl` those five suites **skip cleanly**
and pytest still prints a pass. Every API-efficiency assertion, every drill
budget, every monitor-cleanup check, and the entire discovery and native
telemetry coverage disappears, and nothing looks wrong.

**3.12 is not a proxy for 3.8.** CI enforces a 3.8 floor. Several changes on
this branch needed 3.8 fixes that 3.12 accepted without complaint. Testing on
the development interpreter alone proves nothing about the floor.

**The mock's awkwardness is the point.** Each quirk exists because a real
cluster did it and a defect followed:

- Partially-filled newest sample buckets — a real cluster monitor row had 2 of
  46 metrics populated; a `ViewMetrics` row had exactly one.
- 429 views with the busy ones planted deep — a head-slice of `/views/` showed
  arbitrary idle views.
- An `object_id` cap and mixed-family rejection — real VMS constraints.
- Per-cNode hostnames differing only in a trailing digit — three cNodes rendered
  identically.
- Sub-microsecond `sequence`/`getfh`/`putfh` latencies — these displayed as
  `0 µs`.

"Simplifying" any of those removes the defect it was built to catch.

**A summary is not evidence.** The most expensive error on this branch was
reading an aggregate console line instead of the report underneath it. The
discovery output contained `Nfs4Metrics` 944 times; the console printed only
"1769 relevant metrics". The finding was missed until the repository owner read
the raw file. Discovery now prints names, not just counts.

## Mandatory behavior

### Run both interpreters

```bash
./scripts/validate.sh
```

runs the current interpreter and the 3.8 floor, checks for `openssl` first, and
reports collected/passed/failed/skipped for each leg. Use it. `--fast` skips the
3.8 leg and is for inner-loop iteration only — never for sign-off.

The underlying commands, if you need one directly:

```bash
python3 -m pytest -q
uv run --python 3.8 --no-project --with pytest -- python -m pytest -q
```

### Never accept a skip silently

- If `openssl` is missing, the gate **fails** with an explicit message. Do not
  work around it by running `pytest` directly and reporting the green result.
- If it must be bypassed, `./scripts/validate.sh --allow-missing-openssl` runs
  anyway and prints a prominent **VALIDATION INCOMPLETE** banner. If you use it,
  that banner goes in your CHECKS section verbatim.
- Report skipped counts always. `400 passed, 0 skipped` and `229 passed,
  171 skipped` are very different results and only one of them is validation.

### Watch the count

400 tests collect at the time of writing. A **much smaller** green run means
suites are silently skipping. The gate asserts a minimum collection count for
exactly this reason. If the real count changes because tests were legitimately
added or removed, update the floor in the same change and say so.

### A defect fix requires a regression test

- The test must reproduce the **literal payload shape** observed — the actual
  row, the actual null pattern, the actual label set. Not a stylized version.
- It must **fail before the fix**. Prove it: create a worktree at the prior
  commit and run the new test there. This is how
  `test_bandwidth_survives_a_monitor_that_mixes_metric_families` was verified
  failing on `cb6e5f8` with the message *"read bandwidth lost to the mixed
  scoring"*.
- Reference the defect in the test name or docstring so the intent survives.

### Never weaken a test to pass

Never delete, skip, loosen an assertion on, or narrow the scope of a test to
make a change go green. Never edit `pytest.ini`, a `skipif`, or a fixture to
suppress a failure. Fix the code, or fix the test's *correctness* — never its
strictness.

If a test is genuinely wrong, say so explicitly, explain why, and change it as
its own visible act — not folded into an unrelated change.

### Mock versus real cluster

- **Real-VMS observations outrank mock assumptions.** Where they disagree, the
  cluster is right and the mock is a bug.
- Do not present mock-only behavior as cluster behavior. Loopback scrape latency
  of ~1 ms, synthetic metric values, and the planted busy views are artifacts of
  the harness.
- Some things **cannot** be proven from mock data — counter semantics, units,
  scope reconciliation, real latency distributions, whether a family is
  queryable at a given scope. Those need a real cluster. If you cannot run one,
  say the claim is unproven rather than inferring it.
- Real-cluster validation is the repository owner's step. Do not describe
  anything as real-cluster validated unless it actually was.

### Interactive behavior needs a PTY

Rendering tests capture frames from `_render_frame()` without a terminal, which
covers layout, width and footer presence. It does **not** cover the event loop,
key handling, resize signals, or perceived responsiveness. Those need a real
process in a pseudo-terminal, and no PTY harness is committed — the one used for
the benchmark numbers in [docs/REFACTOR_HANDOFF.md](../../docs/REFACTOR_HANDOFF.md)
lived in a session scratchpad. Treat interactive claims as unproven unless a PTY
run or a real-cluster session backed them.

### Report honestly

- Every check in a completion report carries its **exact observed result**.
- Say what was *not* run and why.
- If a measurement contradicts an earlier claim, correct the claim.
- Distinguish a pre-existing defect from one the current work introduced, with
  evidence for which it is.

There is one known unreproduced flake:
`test_smb_merged_monitor_single_query_per_refresh` failed once on Python 3.8
during a full run and never again across three consecutive full runs plus an
isolated run. Suspected mock TLS startup transient. **Unresolved** — if it
recurs, capture the output rather than re-running until it passes.

## What automated enforcement detects

- `./scripts/validate.sh` — `openssl` presence, both interpreters, collection
  count floor, and per-leg passed/failed/skipped counts.
- `.github/workflows/test.yml` — pytest on 3.8, 3.10, 3.12 and 3.14 for every
  push to `main` and every pull request.
- `tests/mock_vms.py` — records every API call; the measurement instrument for
  call-count assertions.
- `tests/test_globals_hygiene.py` — AST check that no function assigns an
  ALL_CAPS module global without `global`, the defect that made the NFSv4.1
  drill silently show nothing.

## What automated enforcement CANNOT detect

- **A test that is weakened rather than deleted.** A loosened assertion still
  passes and still counts. Nothing measures assertion strength.
- **A regression test that does not actually reproduce the defect.** A test
  written after the fix, never run against the broken code, proves nothing —
  and looks identical to one that does.
- **Whether the mock still matches the cluster.** The mock is a model. Drift
  between it and a new VAST OS version is invisible until someone runs against
  the real thing.
- **Interactive responsiveness.** No committed test drives a PTY.
- **Whether a claim in a report was actually run.** CHECKS sections are written
  by the agent. The only defense is the discipline of pasting real output.
- **CI does not run on this branch.** `test.yml` triggers on pushes to `main`
  and on pull requests. Work on `refactor/tui-performance` gets no CI signal
  until it is published — local validation is the only gate that exists.

## Stopping conditions

Stop and report rather than proceeding when:

- `openssl` is unavailable and the mock-backed suites cannot run.
- A test fails and the correct fix is not obvious — do not adjust the test.
- A claim requires real-cluster evidence you do not have.
- The collection count drops unexpectedly.
- A flake appears: capture it, do not re-run until green.
