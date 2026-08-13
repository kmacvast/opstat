---
name: validate-against-real-vms
description: >-
  The real-cluster validation cookbook for opstat — what to run, what success
  looks like per protocol, and which artifacts to keep. Use when preparing a
  validation pass, or when interpreting logs and screens returned from one.
---

# validate-against-real-vms

Mock behavior is necessary but not sufficient. Several defects on this branch
appeared only against VAST OS 5.5.0.1 — the missing navigation footer, the
still-filling newest sample bucket, identical cNode hostnames, sub-microsecond
latencies rendering as `0 µs`, and bandwidth lost to mixed-family row scoring.

## Preconditions

- **A cluster is reachable and the repository owner has authorized the run.**
  Running `opstat` against a cluster is read-only, but it is a live system and
  the owner decides when it is exercised.
- Credentials come from the environment. **Never** put a password on the command
  line — `--password` works but leaks through `ps` and shell history.

  ```bash
  export VAST_TOKEN=...        # preferred
  # or
  export VAST_PASSWORD=...
  ```

- Load generators are running if rates are to be measured. On an idle cluster,
  counter deltas are zero and nothing can be concluded about rates. Tracked in
  `scripts/` — see [`scripts/README-systemd.md`](../../../scripts/README-systemd.md).

## Workflow

### NFSv3

```bash
./opstat --nfs --version=3.0 --vms <VMS_HOST> --user admin --log-api-calls
```

Success:
- One `POST /monitors/` at startup and **one** `GET /monitors/<id>/query/` per
  refresh in `/tmp/opstat-api-nfs-v3-*.log`.
- Health panel and COMBINED footer show a real `GB/s` under load.
- `v` drill entry completes in seconds, not ~47 s.

### NFSv4.1

```bash
./opstat --nfs --version=4.1 --vms <VMS_HOST> --user admin --log-api-calls
```

Success:
- Cluster view: one monitor query per 5 s tick, and **no `prometheusmetrics`
  request at all**.
- `c` / `t` / `v`: a *"Loading the … please stand by…"* frame appears
  immediately, before any delay.
- `4`: loading frame, then a warm-up panel; `space` completes the warm-up and
  populates all four panels. `SEQUENCE`/`GETFH` show sub-microsecond values; the
  three cNode rows are distinguishable by ID and hostname tail.
- `h`: only `protocol=NFS4` rows, ranked by IOPS.
- At most **two** `GET /api/prometheusmetrics/basic` per minute while in the
  drill.
- On quit, **no `adhoc_opstat_*` monitors remain on the VMS.**

### SMB / S3 / NVMe

Neither SMB nor S3 has been validated against the real cluster during this
effort, and NVMe is the least refactored engine (~467–507 calls per 30 s
session). Treat any claim about them as unproven until a log says otherwise.

### Artifacts worth keeping

- `/tmp/opstat-api-*.log` from `--log-api-calls` — body cap 32 KB, override with
  `OPSTAT_API_LOG_BODY_CHARS`.
- `/tmp/opstat-nfs41-discovery-*.txt` from `--discover-metrics`.
- Terminal captures of any panel that looks wrong.

These contain cluster identifiers. **They live in `/tmp` and are never
committed.**

## Expected output

For each protocol exercised: the command run, the observed call pattern from the
log, the panel behavior observed, and an explicit statement of what did **not**
match expectation.

Report real-cluster findings **separately from** any implementation change made
in response. Root cause first, then the fix.

## Stopping conditions

- **Do not claim real-cluster validation that was not performed.** If this
  session cannot reach a cluster, say the work is mock-validated only and name
  what remains unproven.
- Stop if anything would require a non-`GET` request outside the application's
  own monitor lifecycle.
- Stop if a monitor is left behind — that is a defect to report immediately, not
  to clean up quietly.
- Stop and report rather than adjusting the application to make a screen look
  right. A panel that shows a number the cluster did not return is worse than a
  panel that shows nothing.
