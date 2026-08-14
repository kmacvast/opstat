# var203 validation package — continuation pass

One work-laptop trip that answers every remaining live-cluster dependency of
the uncommitted continuation pass (FR-A/B/C, NVMe ranking + batching, startup/
shutdown UX). Target **var203.selab.vastdata.com only**; var204 is unavailable.

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
