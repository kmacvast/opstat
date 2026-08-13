# D-010 — Merged headline monitors are probe-validated with a fallback

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

Each engine originally created one monitor per metric family and queried each
one per refresh — NFSv4.1 queried **five** monitors every 5 seconds. Merging
families into a single monitor collapses that to one query per tick.

But VMS does not accept arbitrary family combinations. `NVMe_TCP_README.md`
records that `BlockMetrics`, `VolumeMetrics` and `ProtoMetrics` cannot be mixed
in one monitor. Assuming a merge works and discovering otherwise at runtime
would break the headline panel on an unknown set of clusters.

## Decision

Merged headline monitors are **probe-validated at startup, with automatic
fallback** to the historical split layout when the merge is rejected.

The same pattern applies to batched drill monitors: probe, then fall back to
per-object monitors if the batch is refused.

## Evidence

Measured over 32-second sessions at `--refresh 5` against the instrumented mock:

```text
Monitor queries per refresh     Before   After
  nfs v3                          2        1
  nfs v4.1                        5        1
  smb                             2        1
  s3                              2        1

API calls, 32s session          Before   After
  nfs v3                         20       11
  nfs v4.1                       48       12
  smb                            25       15
  s3                             21       11
```

The fallback is not hypothetical: the NVMe engine's family constraint is real
enough to be documented, and per-object drill fallback was needed for cNode
batching on some paths.

`tests/test_api_efficiency.py` asserts both the merged budget and the fallback
budget, so a merge that silently stops working shows up as a call-count change
rather than as missing data.

## Consequences

- One extra probe call at startup, once per session. It pays for itself on the
  first refresh.
- Two code paths per engine (merged and split) that must both stay correct.
  Both are tested.
- A merged monitor returns props from multiple families in one row — which is
  what makes [D-011](D-011-newest-complete-sample-scoped-per-family.md)
  mandatory. Merging without per-family row scoping loses whole columns, and
  did.

## Enforcement limit

The mock can reject mixed families on demand, which exercises the fallback. It
cannot tell you which combinations a *particular* VAST build will reject. The
probe exists precisely because that is unknowable in advance.

## What would justify reopening

Evidence that a specific merge is rejected on clusters in the field often enough
that the probe cost is not worth it — or, conversely, verification that the NVMe
family constraint is historical rather than current. The latter is an open item
in [REFACTOR_HANDOFF.md](../REFACTOR_HANDOFF.md): the constraint has not been
re-tested with the probe-and-fallback pattern used successfully elsewhere.
