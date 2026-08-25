# D-018 — What opstat keeps compatible across releases

**Status:** Accepted · **Recorded:** 2026-08-25 · **Evidence:** source audit of
`main` (`52e08bb`) against the published `v0.1.2` tag (`7d40bd0`)

## Context

opstat has exactly one published release, `v0.1.2`, and it points at a commit
far behind `main`. A real released-version upgrade therefore cannot be
validated yet. What *can* be settled now is which surfaces are promises and
which are free to change — and that turns out to be answerable from the
repository, because the compatibility surface is much smaller than it looks.

The finding that shapes everything below: **opstat persists no state.** It
writes no config file, no cache, no dotfile, no settings. Every file it
creates, it creates because the operator asked for it on the command line
(`--csv`, `--export-openmetrics`, `--log-api-calls`, `--discover-metrics`) —
some at a path the operator named, some auto-named under the temporary
directory — and it **never reads any of them back**. There is nothing to
migrate between versions, so "upgrade" means only: replace the executable and
keep the same command line and the same downstream consumers working.

## Stable across compatible releases

1. **Documented CLI flags and aliases.** Names, aliases (`--menu`/`-i`,
   `-V`/`--tool-version`, the singular/plural scoping pairs), defaults
   (`--vms-port` 443, `--user` admin, `--refresh` 5), the mutually-exclusive
   protocol selection, and the hidden `--port` compatibility alias. Enforced
   by `tests/test_cli_contract.py`.
2. **Exit-code conventions.** Argparse failure exits 2; a protocol-validation
   failure exits 1. Enforced by `tests/test_compatibility_contract.py`, which
   runs the entrypoint as a subprocess and asserts both codes.
   `scripts/smoke_packaged_opstat.py` re-checks the 2 against a *built binary*,
   but it needs an artifact and is not run by the gate.
3. **`-V` / `--tool-version` output shape** — `opstat X.Y.Z` from the single
   authoritative version. Enforced by `tests/test_version_contract.py`.
4. **CSV column identity and order, per protocol.** Existing columns are not
   renamed, reordered or removed within a major version; new columns may be
   **appended**. The schemas are protocol-specific by design (see below).

   **`--csv` appends, and the header is written only when the file is empty**
   (`ensure_csv_file` in each engine). So appending a column is additive for a
   *new* file and **breaking for a reused one**: release N+1 writes wider rows
   under release N's header, which `csv.DictReader` silently buckets into
   `restkey`. Any release that appends a column must say so in its notes and
   tell operators to rotate the file. Making the writer compare the existing
   header and start a new file on mismatch would be a behaviour change, not a
   documentation one, and is not decided here.
5. **JSON Lines export record shape.** Every line stays a single JSON object
   carrying `timestamp`, `metric_name`, `metric_type`, `value`, `unit`,
   `attributes`; metric names stay `vast.{protocol}.{suffix}`; the protocol
   ids stay `nfs3`, `nfs41`, `smb`, `s3`, `nvme_tcp`; `value` stays a JSON
   number and `attributes` a flat string map. New metric suffixes and new
   attribute keys may be **added**, and new top-level record keys may be
   added — consumers must tolerate keys they do not recognise.

   **What a present line guarantees, and what an absent one does not.** A line
   that is present was measured: `openmetrics._emit` skips `None` and never
   writes a JSON `null`, so `"value":0.0` is a real measured zero. The converse
   does **not** hold today. On the drill path every engine collapses a measured
   zero to `None` before export (`vast_drill.py`, and the per-engine row
   builders), so an idle target that genuinely measured `0.00 ops/s` produces
   no line at all — indistinguishable from a target the monitor did not return.
   That conflicts with D-009 and is recorded as an open defect below; it is a
   telemetry-semantics change (L1) and is deliberately **not** resolved here.
6. **Environment-variable names and credential precedence.** The full chain is
   `VAST_TOKEN` > `--password` > `VAST_PASSWORD` > interactive prompt: a token
   suppresses password acquisition entirely, and `--password` beats
   `VAST_PASSWORD`. `OPSTAT_API_LOG_DIR` continues to redirect API logs.

   **Not** contractual, and free to change or disappear: `TMPDIR` (which also
   relocates API logs, via `tempfile.gettempdir()`),
   `OPSTAT_API_LOG_BODY_CHARS` and `OPSTAT_NFS4_PROBE_INTERVAL`. Those three
   are diagnostic tuning knobs, not an interface.
7. **Auto-generated output paths.** `--log-api-calls` and
   `--export-openmetrics` without `--openmetrics-file` name their own files:
   `opstat-api-{protocol}-{vms}-{port}-{pid}.log` and
   `opstat-openmetrics-{protocol}-{vms}-{stamp}.jsonl`, under the temporary
   directory. Those *patterns* are stable enough to glob — they are already
   documented in three files — but the stderr line announcing the chosen path
   is diagnostic prose and may change. A script that needs a specific path must
   pass `--openmetrics-file`.
8. **Release artifact naming.** `opstat-<os>-<arch>[.exe]` with the recorded
   normalisations (`darwin`→`macos`, `amd64`/`x86_64`→`x86_64`,
   `aarch64`/`arm64`→`arm64`). Enforced by `tests/test_packaging_contract.py`.
9. **No persisted state.** opstat continues to write only files the operator
   asked for, and to read none of them back. Introducing a config or cache
   file — or making the wizard actually read `~/.vastconf` — would itself be a
   compatibility event, because it creates an upgrade surface that does not
   exist today.

## Free to change between releases

- The TUI: layout, column widths, panel composition, colour, wording,
  ordering of rows within a panel. It is a human display, not an interface.
- Diagnostic prose: status lines, loading messages, error text.
- **The `--log-api-calls` file** — with one caveat that turns out to matter.
  It is support evidence rather than a published machine format: body
  truncation and the trailing fields may change. But it is **not free-form
  either**: `scripts/var203_validation/run_var203_validation.py` and the lab
  shell scripts parse it positionally to produce the read-only and
  monitor-cleanup verdicts — the strongest safety evidence this project has.
  They pin the `<ISO timestamp> <METHOD> <URL> <N>ms` prefix order, the
  `YYYY-MM-DD HH:MM:SS` timestamp format, and the literal `body=` marker.
  Changing any of those requires updating those parsers **in the same commit**,
  or the leak and GET-only proofs silently start passing for the wrong reason.
  Durable regardless: credentials are never written (auth lives in headers,
  which `log_call` never receives), and the file is created `0o600` — though
  note the mode is applied at *creation* only, so a pre-existing file at the
  same path keeps its own mode.
- `--discover-metrics` output. A read-only investigation report.
- New flags, new protocols, new panels, new telemetry, and additional
  optional fields in existing machine output.
- Internal module layout, monitor strategy, request batching, throttles.

## Breaking changes

Renaming, reordering or removing an existing CSV column; removing or
retyping a JSON Lines key; removing a CLI flag or alias; changing a documented
default; changing credential precedence; changing the artifact naming
convention. Any of these requires an explicit release note, a deliberate
version decision, and migration guidance where downstream consumers are
affected.

## Evidence: what has actually changed since v0.1.2

Compared at source level (`git show v0.1.2:<file>` versus `main`):

| Surface | Result |
|---|---|
| CLI flags | **No flag removed.** `--bucket`, `--buckets`, `--tenant`, `--tenants` added — *additive* |
| CSV headers (nfs_v3, smb, nvme_tcp) | **Byte-identical** — *compatible* |
| CSV header (s3) | New: the S3 engine did not exist at v0.1.2 — *additive* |
| JSON Lines record keys and `_METRIC_DEFS` | **Byte-identical** — *compatible* |
| Artifact naming (`artifact_name`) | **Byte-identical** — *compatible* |
| `smb_phase0_discover.py` | Deleted as "unreferenced and broken"; never documented — *non-contractual* |

**No breaking change has occurred since v0.1.2.** That is the first real
backward-compatibility evidence this project has.

## Known gaps, stated rather than papered over

- **NFSv4.1 ignores `--csv`.** The flag parses and is accepted, and the other
  four engines write CSV, but `nfs_v41.py` contains no reference to CSV at all
  — no file is created and nothing is said. This is a pre-existing gap, not a
  regression. Four documentation sites promised the opposite and were
  corrected in the same change as this record (`README.md` flag table and the
  worked example, `NFSv41_README.md` shared-flags line, flag table and
  example); `tests/test_compatibility_contract.py` now fails if any document
  puts `--csv` in an NFSv4.1 example again. Adding a writer later is additive.
- **`~/.vastconf` is not read.** `wizard._load_config` returns `None` unless a
  loader is injected, and there is no production loader — the original
  implementation imported a monorepo module that does not exist here.
  README no longer promises the seeding. Because nothing reads it, it is
  **not** a persisted-state compatibility surface today.

## Telemetry-semantics defects surfaced while writing this record

Found during the compatibility review, **out of scope to fix here** — each
changes what a displayed or exported number means, which is an L1 decision
under `AGENTS.md`. Recorded so they are not lost:

1. **A measured zero is exported as absence on the drill path.** `vast_drill`
   and all five engine row builders map `0` to `None` (`total_ops if
   total_ops > 0 else None` and siblings), and `openmetrics` then omits the
   line. An idle-but-measured target is indistinguishable from an unmeasured
   one. Conflicts with D-009.
2. **NVMe-oTCP does the inverse.** `compute_data_io_iops` uses
   `as_float(...) or 0`, so an unavailable `ops_sec` is exported as a
   measured-looking `0.0`. Within one file, `vast.nvme_tcp.operations` and
   `vast.nfs3.operations` therefore mean different things.
3. **Aggregates sum unavailable operands as zero.** Every engine's
   `total_ops`/`total_bw` defaults a missing operand to `0`, and the total is
   exported with nothing marking it understated.
4. **`vast.nfs3.io_size` is always a derived ratio**, computed as
   `(bw * 1e9) / ops` — and in the split-monitor case from two *different*
   monitors — yet exported as a plain gauge in bytes. NVMe-oTCP uses a native
   counter when present and the same derivation when not, with nothing
   distinguishing the two. D-009 requires derived figures to be labelled.
5. **`json.dumps` uses the default `allow_nan=True`**, so a `NaN` or `Infinity`
   reaching the emitter would produce a line that strict JSON parsers reject.
   No internal path producing one was found; a VMS-supplied `NaN` literal is
   unproven either way.
6. **The `.jsonl` export is not created `0o600`**, unlike the API log, though
   it carries the same class of cluster identifiers in `target_name`.
7. **`nvme_tcp`'s `ops_monitor_id` CSV column holds a comma-separated list**,
   not a single id, despite the singular column name. The name is pinned; the
   type is not, and a consumer parsing it as an integer breaks on any
   multi-monitor run.

## What still requires a second release

Everything above is a *source-level* contract, proven by comparing code and
by tests. It is not the same as running release N, upgrading to N+1 and
observing that nothing broke. That test is defined in
[../../README.md](../../README.md#upgrading) and must wait until a second
release exists.
