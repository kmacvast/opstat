# D-007 — `/monitors/topn/` cannot provide protocol-specific attribution

**Status:** Accepted · **Recorded:** 2026-08-13 · **Cluster:** VAST OS 5.5.0.1

## Context

Ranking drill candidates by activity is required — a head-slice of `/views/`
picks arbitrary idle views on a cluster with 429 of them. `/monitors/topn/` is
the obvious server-side ranking primitive, and using it would remove the need to
rank client-side entirely.

## Decision

`/monitors/topn/` is **not usable for protocol-specific attribution**. Ranking
is done by `vast_drill.DrillSession`, which tries topn, falls back to batched
rank monitors, and falls back again to a chunked scan.

Do not reopen this without new evidence from a cluster that behaves differently.

## Evidence

The endpoint **ignores `object_type`**. Whatever is requested, it returns a
fixed payload with:

- dimensions: `client`, `cnode`, `user`
- fields: `title`, `read`, `write`, `total`, `scan`

There is **no protocol label anywhere in the response**. A "top client by
total" cannot be attributed to NFSv4 rather than SMB or S3, which is exactly the
question a per-protocol drill is asking.

It also exposes no `view` dimension, which is why the topn path correctly
no-ops in the view drill rather than producing a wrong ranking
([D-006](D-006-host-view-is-the-nfs4-attribution-source.md)).

## Consequences

- `vast_drill` keeps its three-tier ranking strategy. The topn tier remains
  because it is correct where its dimensions match, and it costs one call to
  discover that they do not.
- Ranking results are cached (5 minutes) because the fallback tiers are not
  free.
- Client-side ranking is the reason the VIEW drill went from 47.5 s / 45 calls
  to 4.2 s / 4 calls.

## What would justify reopening

A VAST release where `/monitors/topn/` honors `object_type`, or exposes a
protocol label or a `view` dimension. Verify by requesting a specific
`object_type` and confirming the response actually changes.
