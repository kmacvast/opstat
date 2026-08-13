---
name: run-quality-gates
description: >-
  Run opstat's deterministic validation gate and interpret its output honestly,
  including the silent-skip traps. Use before reporting any change complete, and
  whenever asked whether the repository is green.
---

# run-quality-gates

Run `./scripts/validate.sh` and report exactly what happened.

The point of this skill is that a green `pytest` run is not evidence. 171 of
opstat's 400 tests are gated on the `openssl` binary and skip **cleanly**
without it, and Python 3.12 accepts syntax that the mandatory 3.8 floor
rejects. The gate exists so nobody has to remember that.

## Preconditions

- Repository root is the working directory.
- Only run commands documented in `AGENTS.md` ("Command and test contract").
  Do not invent a test invocation.

## Workflow

1. **Run the gate.**

   ```bash
   ./scripts/validate.sh
   ```

   It checks tooling, runs the current interpreter, then Python 3.8 via `uv`,
   and prints a summary block with per-leg counts.

   Use `--fast` only for inner-loop iteration (skips the 3.8 leg). Never for
   sign-off.

2. **If it fails on tooling, fix the environment — not the gate.**
   - `openssl` missing → install it. See `docs/WORKSTATION_BOOTSTRAP.md`.
   - `uv` missing → install `uv`, or supply any 3.8 interpreter and run the 3.8
     command from `AGENTS.md` directly.
   - Do **not** fall back to a bare `python3 -m pytest -q` and report that as a
     pass. That is the exact failure the gate prevents.
   - `--allow-missing-openssl` exists as a deliberate escape hatch. If you use
     it, the run prints a **VALIDATION INCOMPLETE** banner, and that banner goes
     into your report verbatim.

3. **If tests fail, capture the failure.** Report the exact output. Do not
   re-run hoping for green, and do not adjust the test. The one known flake is
   `test_smb_merged_monitor_single_query_per_refresh` on Python 3.8 — if it
   reappears, capture it as evidence rather than papering over it.

4. **Check the counts, not just the exit code.** The gate enforces a minimum
   collection count; a much smaller green run means suites are skipping. If the
   real count changed because tests were legitimately added or removed, update
   the floor in `scripts/validate.sh` in the same change and say so.

5. **Targeted runs** are fine while iterating:

   ```bash
   python3 -m pytest tests/test_nfs4_native.py -q
   python3 -m pytest -q -k "loading or footer"
   ```

   They never substitute for the full gate before reporting completion.

## Expected output

A CHECKS block carrying exact observed results:

```
CHECKS
  ./scripts/validate.sh
    openssl        : OpenSSL 3.x (present)
    Python 3.12.13 : 400 collected, 400 passed, 0 failed, 0 skipped
    Python 3.8.x   : 400 collected, 400 passed, 0 failed, 0 skipped
    result         : PASS
  Not run: real-cluster validation (no cluster reachable from this session)
```

Every number is copied from output, not recalled. State which interpreters ran.
State what was **not** run and why.

## Stopping conditions

- Stop and report if `openssl` or a 3.8 interpreter is unavailable. Do not
  proceed to a partial run and describe it as validation.
- Stop and report on any test failure. Never delete, skip, weaken, or loosen a
  test, and never edit `pytest.ini`, a `skipif`, or a fixture to suppress a
  failure.
- Never claim a check passed that you did not run.
- The gate is local-only. CI (`.github/workflows/test.yml`) triggers on pushes
  to `main` and on pull requests, so a feature branch gets **no CI signal**
  until it is published. Do not imply CI covered a change that it never saw.
