# var203 validation package — continuation pass

One trip that answers every remaining live-cluster dependency of the
continuation pass (FR-A/B/C, NVMe ranking + batching, startup/shutdown UX).
Target **var203.selab.vastdata.com only**; var204 is unavailable.

> ## Run this on the Linux lab host, not a tethered laptop
>
> **One command does everything below, unattended:**
>
> ```bash
> python3 scripts/var203_validation/run_var203_validation.py
> ```
>
> It drives `opstat` through a PTY itself — no key-pressing, no timing by
> hand — and writes `/tmp/opstat-var203-validation.txt`. Use it in preference
> to the manual steps; the steps remain here as the specification of what it
> checks and as a fallback if the driver cannot run.
>
> **Wall-clock is only trustworthy from a host near the cluster.** The report
> stamps its own hostname so a run over a distorted network path can be
> discounted. API-call counts, monitor layout, `object_id` behaviour and
> cleanup results stay valid regardless of network quality.
>
> Round 1 already established the batch/splittability results — see
> [D-013](../../docs/decisions/D-013-nvme-drill-batching-is-scope-dependent.md).
> What is still outstanding: real drill **cost**, startup/shutdown UX, the
> Fabric screen, navigation under a real terminal, and the latency units.

Safety, for every step below: credentials come from `VAST_PASSWORD` /
`VAST_TOKEN` in the environment (never argv); nothing modifies VMS
configuration; only temporary `adhoc_opstat*` monitors are created, their
exact ids recorded and deleted, and cleanup is verified by id — **never**
touch other `adhoc_opstat_*` monitors (concurrent sessions exist on this
shared cluster). Exit the TUI with a clean `q`; never SIGKILL.

Run the steps in order; each says what it proves and what to bring back.
Total time ≈ 30–40 min with loadgens running.

---

## Step 0 — Preconditions

```bash
cd ~/git/opstat && git status --short     # this working tree, as reviewed
export VAST_PASSWORD=...                  # or VAST_TOKEN
./scripts/validate.sh --fast              # local sanity before touching the cluster
```

Start the **block/NVMe loadgen** and the **NFSv4.1 loadgen**
(`scripts/README-systemd.md`) and leave both running throughout — idle
counters cannot prove rates or units.

## Step 1 — Automated probes (~5 min)

```bash
python3 scripts/var203_validation/probe_var203.py \
  --vms var203.selab.vastdata.com --user admin \
  > /tmp/opstat-var203-probe.txt 2>&1
tail -25 /tmp/opstat-var203-probe.txt     # RESULT SUMMARY + cleanup verdict
```

**Proves:** (a) whether multi-`object_id` BlockMetrics monitors are accepted
and splittable at cnode/vip/blockhost scope → decides batch vs per-object
fallback for the NVMe drill; (b) whether the 2-counter rank monitor is
accepted → decides ranked vs stable-order candidates; (c) latency source
units — BlockMetrics and `host_view` values printed next to the proven-µs
NFS4Common reference for the same moment of load.

**Bring back:** the whole `/tmp/opstat-var203-probe.txt`.

## Step 2 — NVMe real BEFORE/AFTER (~10 min)

The BEFORE numbers are already established (entry 65 calls, 64 queries/poll,
mock + architecture audit); this measures AFTER on real hardware.

```bash
./opstat --block --nvme-over-tcp --vms var203.selab.vastdata.com --user admin --log-api-calls
```

1. Note wall-clock: launch → first `Connecting…` frame → normal dashboard.
2. Let it sit 60 s (headline cadence).
3. `c` → cNode drill. Note entry wall-clock; leave 60 s; press `space` once.
4. `x`, then `i` → VIP drill, 30 s. `x`, then `h` → Host drill, 30 s
   (blockhost is unmodeled in the mock — this is its only test).
5. `x`, quit with `q`. Note the `Cleaning up N temporary monitors…` line.

Then, from the log (`/tmp/opstat-api-nvme-tcp-*.log`):

```bash
L=$(ls -t /tmp/opstat-api-nvme-tcp-*.log | head -1)
grep -c 'POST /api/monitors/' "$L"                      # monitors created
grep -c 'GET .*/query/' "$L"                            # total queries
grep -oE 'POST /api/monitors/[^ ]*' "$L" | head -30     # creation sequence
grep -iE 'batch|rank' "$L" | head -20                   # batch/rank monitor names
```

**Proves:** drill entry ≈ 13 calls (batch) or the per-object fallback count;
8 queries per drill re-poll; ranked candidates rather than the first 8 by API
order (check the drill panel names against the busiest initiators you expect);
15 s drill throttle; `space` forcing an immediate query; exact-id cleanup on
quit (no `adhoc_opstat_*` from THIS pid remain — verify with the pid in the
log name).

**Bring back:** the four command outputs, the panel's ranked names (photo or
copy), the wall-clock notes, and the pid-scoped leftover check.

## Step 3 — Startup, shutdown, navigation, fabric screens (~10 min)

One run per engine (NFSv3, NFSv4.1, SMB, S3, NVMe):

```bash
./opstat --nfs --version=3.0 --vms var203.selab.vastdata.com --user admin   # etc.
```

Verify and note per engine:

- **Startup:** `Connecting to <host>:443…` appears before any delay, is
  replaced by `Preparing metrics on <cluster>…`, then
  `Gathering initial metrics…`, then the dashboard; footer visible in every
  startup frame.
- **Navigation:** footer reads the canonical order —
  `[q] Quit | [o] Ops | [l] Lat | [n] Name | [c] cNode | [v] View | [t] Tenant | [i] VIP | [x] Exit drill | [space] Refresh`
  (each engine shows only its supported subset), protocol-specific keys after
  (`[4]`/`[h]` on v4.1; `[b]` on S3; `[h]`/`[r]` on NVMe; `[r]`/`[w]` on v3).
  Confirm `v` does nothing on NVMe and `p` does nothing anywhere.
- **NVMe fabric panel:** Read/Write/Reclaim bars sum to ~100% of real I/O
  while the Fabric bar shows its own `of all activity` share; with the block
  loadgen running, read/write percentages must NOT shrink when fabric traffic
  spikes. Sub-ms combined latency must show a µs value, never `0.00 ms`.
- **Shutdown:** on `q`, the cleanup message appears, the process exits, and
  no monitors from that session remain (spot-check ids from the API log).

**Bring back:** per-engine PASS/FAIL notes + a capture of any screen that
looks wrong (that is how the last three real defects were found).

## Step 4 — Optional narrow-terminal spot check (~2 min)

Resize to ~100 and ~60 columns on any engine: footer truncates legibly,
frame never wraps into garbage.

---

## What comes back feeds

- `docs/decisions/` — a family/batch-compatibility record if Step 1 is
  decisive; latency-unit records if Step 1c is conclusive under load.
- `docs/REFACTOR_HANDOFF.md` — NVMe AFTER numbers; FR statuses flip from
  "REAL-VMS VALIDATION PENDING" to validated.
- The logical commit breakdown (already proposed) — commit after, not before,
  the evidence returns.

---

## The automated driver (`run_var203_validation.py`)

Preferred over the manual steps above. Same safety contract, executed rather
than remembered.

```bash
cd ~/git/opstat
export VAST_PASSWORD=...                 # or VAST_TOKEN; never argv
python3 scripts/var203_validation/run_var203_validation.py
```

Useful flags: `--skip-others` (NVMe only), `--skip-probe` (probes already
run), `--cadence-window 60` (longer idle observation), `--vms` / `--user`.

**What it does, in order**

1. Prerequisites: repo root, branch/HEAD, Python version, `opstat` present,
   credential present (presence only — the value is never printed or logged),
   DNS + TCP reachability. Refuses to touch the cluster if any fail.
2. Reports committed loadgen unit state. It **never** starts them: that needs
   privilege and changes machine state, so it prints what to run instead.
3. Runs `probe_var203.py` and folds its `PROBE:` verdicts into the report.
4. NVMe session: startup phase timings, footer, Fabric/workload frame
   excerpt, then the `c` / `i` / `h` drills — per-drill entry API calls
   (keypress → panel rendered), monitors created, batch-vs-per-object layout
   read from the monitor **names**, idle cadence, and a forced-refresh check.
5. Navigation: the canonical legend, plus proof that the retired bindings are
   dead — `p` must not exit a drill and `v` must not open VIP. An unbound key
   produces no repaint at all, so the driver forces one with `space` and reads
   the resulting frame rather than inferring from silence.
6. Clean `q`, cleanup frame, exit code, and per-id cleanup verification.
7. Short startup/footer/clean-`q` checks for SMB, S3, NFSv3 and NFSv4.1.

**Cleanup accounting.** Monitor ids come from the session's own API log
(`POST /monitors/` response bodies), and each is verified individually with a
`GET` where **404 is proof of deletion**. It never lists-and-deletes: other
sessions' `adhoc_opstat_*` monitors exist on this shared cluster and must
never be touched or counted against the run.

**Termination.** `q` is the exit path. If a session does not exit within the
drain budget it gets one SIGTERM and the PTY is deliberately held open so the
monitor drain can finish — closing it mid-drain is what truncated cleanup
historically. **SIGKILL is never sent.**

**Bring back:** `/tmp/opstat-var203-validation.txt`,
`/tmp/opstat-var203-probe.txt`, and the `/tmp/opstat-api-*.log` files it
names. Do not commit any of them — they carry cluster identifiers.

### Known behaviour when something is unavailable

A drill whose panel never renders is reported `FAIL` with the panel title it
waited for, and the run continues. That is how the driver behaves against the
mock for the `h` (blockhost) drill, which the mock deliberately does not
model — against a real cluster it should open.
