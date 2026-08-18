# D-008 — NFSv4 delegations are a file-path-scoped diagnostic, never a panel

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

Delegation state is genuinely useful NFSv4 diagnostic information, and
`GET /tenants/{id}/nfs4_delegs/` exists. It was investigated as a candidate for
a telemetry panel.

It also has a `DELETE` sibling that would revoke live delegations on a running
cluster. Discovery enumerates the OpenAPI surface, so that endpoint appears in
front of anyone working here as a discovered capability.

## Decision

1. **The endpoint cannot back a panel.** It requires `file_path` and answers
   "who holds a delegation on *this* file". It cannot enumerate cluster
   delegation state.
2. **Not implemented in the TUI.** A per-file lookup needs a path-entry
   interaction that would have complicated the telemetry drill. Deferred, not
   rejected. *(2026-08-18: the owner exercised this reopening clause —
   FR2 implements the path-entry lookup in the NFSv4.1 engine, and Stage-B
   validation ran it end to end against var204/VAST 5.5.0.1 the same day.
   The wire contract, its limits, and the validated scope are recorded in
   [D-017](D-017-nfs41-delegation-lookup-wire-contract.md). Points 1 and 3
   are unchanged: no panel exists, and the DELETE prohibition is permanent —
   `nfs4_delegs` DELETE must never be exposed by opstat.)*
3. **The `DELETE` sibling must never be invoked.** There is a test asserting it
   is not. This holds regardless of any future decision about the `GET`.

## Evidence

Called without `file_path`, the endpoint returns:

```text
{"detail":"['__root__->file_path: field required']"}
```

With a real path supplied it returns live records wrapped as `delegate_info` /
`delegate_info_count_total` plus pagination keys — so the data is real and
useful, and its scope is genuinely one file.

## Consequences

- No delegation panel exists, and its absence is not a defect.
- `LOCK`, `LOCKU`, `LOCKT`, `RELEASE_LOCKOWNER`, `OPEN_DOWNGRADE`,
  `OPEN_CONFIRM`, `DELEGRETURN`/`DELEGPURGE` and all pNFS operations
  (`LAYOUTGET`, `LAYOUTRETURN`, `LAYOUTCOMMIT`, `GETDEVICEINFO`,
  `GETDEVICELIST`) have **no counters** on VAST OS 5.5.0.1. The pNFS panel
  exists and is evidence-gated, so it will appear automatically if a future
  build exports them ([D-009](D-009-panels-are-evidence-gated.md)).
- This is the canonical example of the general rule: **the existence of an
  endpoint in a schema is not permission to call it.** See
  [.claude/rules/vast-api-safety.md](../../.claude/rules/vast-api-safety.md).

## What would justify reopening

The **`GET`** side: a decision that a path-entry interaction is worth building,
or a VAST release exposing an enumerable delegation listing.

The **`DELETE`** side: nothing. `opstat` is an observability tool and does not
revoke delegations.
