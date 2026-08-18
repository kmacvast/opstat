# D-017 — The NFSv4.1 delegation lookup wire contract (var204/5.5.0.1)

**Status:** Accepted, real-VMS validated · **Recorded:** 2026-08-18 ·
**Cluster:** var204, VAST OS 5.5.0.1 (decisive discovery run
`fr2-var204-20260818-194511`; Stage-B production validation run
`fr2val-var204-20260818-205116`)

This record distinguishes three levels of certainty: the **proven API
contract** (captured wire behavior), the **implemented behavior** (what the
diagnostic does with it), and the **real-VMS validated behavior** (what was
exercised end to end in production code against a live cluster).

## Context

[D-008](D-008-delegations-are-a-file-scoped-diagnostic.md) deferred the
per-file delegation lookup pending a path-entry interaction. On 2026-08-18
the owner approved implementing it (FR2, five recorded product decisions —
see `docs/FR_BACKLOG.json` FR2 notes), which exercises D-008's own reopening
clause for the **GET** side only. The DELETE prohibition in D-008 is
unchanged and permanent.

This record captures the wire semantics the implementation relies on, and —
just as importantly — which parts of the payload remain **unproven**.

## Proven API contract (captured verbatim on var204)

- `GET /api/tenants/{tenant_id}/nfs4_delegs/?file_path=<path>` is the whole
  contract. `file_path` is the **full VAST namespace path** with a leading
  slash, URL-encoded. Without it: HTTP 400
  `['__root__->file_path: field required']`.
- A path that resolves (file **or** directory) answers HTTP success with the
  wrapper `{"delegate_info": [...], "delegate_info_count_total": N,
  "xeystore_pagination": false, "xeystore_pagination_next_client_id": <int,
  populated even when pagination is false>}`. An empty `delegate_info` on a
  valid path means **no client holds a delegation** — information, never an
  error.
- A path that does not resolve answers HTTP 400
  `get_handle_by_path returned an error : GetHandleByPathCode.ILLEGAL_PATH`.
  Empty-success and ILLEGAL_PATH are therefore distinguishable and the UX
  keeps them distinct (`empty` vs `invalid`).
- ILLEGAL_PATH also fires when the **wrong tenant** is asked about a path
  that exists elsewhere — an entire lab run of cross-cluster ILLEGAL_PATH
  noise proved this. Tenant targeting comes from the view that owns the
  namespace (`vast_drill.namespace_candidate_views`): an exact non-root view
  is authoritative (no fallback); a root-only prefix match allows exactly
  one bounded fallback; more than three distinct candidate tenants is
  honest ambiguity and no query is made.
- Live records carry exactly six fields (five real WRITE delegations
  captured): `client_id` int, `delegation_client_ip` str,
  `delegation_stateid` int, `delegation_type` str (arbitrary string; WRITE
  observed), `revoke_in_progress` bool, `vip_addr` str. The captured
  `delegation_client_ip` matched the real mounted client and `vip_addr`
  matched the mount VIP (one client/VIP configuration — "Serving VIP" is
  backed by that single sample).
- A **directory** queries as empty while files inside it hold live
  delegations (the capture's directory probe, no trailing slash, returned 0
  records while five files inside had WRITE delegations). The empty state
  therefore always carries the directory caveat, unconditionally — path
  syntax cannot tell a file from a directory.
- File-scoped GETs ran 217–709 ms; the no-`file_path` 400 took ~1.8 s.

## Unproven (displayed with provenance, never interpreted)

- `xeystore_pagination = true` has **never been observed**. The UI names the
  flag when set ("response marked xeystore_pagination - additional records
  may exist") rather than asserting its meaning.
- `delegate_info_count_total > len(delegate_info)` has never been observed;
  every capture had them equal. The "cluster reports more than this
  response carried" wording renders only in that unobserved case.
- HTTP 404 on this route has never been observed on a real build (the
  endpoint existed on 5.4.6 and 5.5.0.1); the unavailable state names its
  provenance (status and tenant) rather than claiming a cluster-wide
  capability fact.
- Trailing-slash path resolution, `delegation_type` values other than WRITE,
  and `revoke_in_progress = true` in the wild.

## Real-VMS validated (Stage B, 2026-08-18)

Run `fr2val-var204-20260818-205116` (validated SHA `5fd6909`, selab-var-204,
VAST 5.5.0.1) drove the production `nfs_v41` engine in-process against the
live cluster: all 24 checks PASS, validator rc 0, raw-first review of the
archive confirmed agreement. Validated end to end: `[d]` entry and prompt
(q-as-path-text included), a live WRITE delegation on a real workload file
with all six fields populated (`delegation_client_ip` equalled the mount's
own `clientaddr`; `vip_addr` equalled the mount VIP), the answering tenant
recorded, the valid-empty vs invalid (ILLEGAL_PATH) distinction, the
directory caveat, `[space]` re-querying exactly once, zero delegation calls
across poll ticks, the session `/views/` cache, GET-only safety (5
delegation calls in the log, all GET), and per-id monitor cleanup.

**Validated release scope is VAST 5.5.0.1 on var204 only.** VAST 5.4.6 has
never had the production diagnostic run against it; the endpoint exists
there (Stage-A discovery), but the record shape and semantics on 5.4.6 are
unvalidated. The "Unproven" payload aspects above remain unproven — the
validation run also observed only `xeystore_pagination: false`,
`count_total == len(delegate_info)`, WRITE delegations, and
`revoke_in_progress: false`.

## Consequences

- The lookup is **on-demand only**: zero delegation API calls on the normal
  refresh path, `[space]` is a manual re-query, and there is no timed
  refresh. All test-asserted.
- The `/views/` inventory backing tenant resolution is fetched **once per
  session** on first use (`nfs_v41._DELEG_VIEWS`). Deliberate: a view
  created mid-session is unresolvable until restart; a deleted view fails
  closed as ILLEGAL_PATH within the ≤2-GET budget. Do not "fix" this by
  polling views.
- The only operation the feature can perform is the GET —
  `_deleg_lookup_get` has no method parameter, and D-008's DELETE
  prohibition stands verbatim.

## What would justify reopening

A captured `xeystore_pagination = true` response or a
`count_total > len(delegate_info)` response (either would let the truncation
wording assert meaning instead of provenance); a VAST release changing the
wrapper or record shape; real-cluster evidence that the session-lifetime
view cache misresolves in practice.
