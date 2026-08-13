# D-012 — The NFSv4 client-attribution drill is called "v4 hosts"

**Status:** Accepted · **Recorded:** 2026-08-13

## Context

The `h` drill attributes NFSv4 traffic to source addresses, from
`host_view` series keyed by client IP × view path
([D-006](D-006-host-view-is-the-nfs4-attribution-source.md)). It was originally
labelled "v4 clients".

## Decision

The terminology is **"v4 hosts"**, in the panel title, the footer key legend,
and any documentation describing it.

## Rationale

"Client" is an overloaded term in NFSv4 and the overload is not cosmetic. The
protocol has a formal client identity — `EXCHANGE_ID` / `clientid`, established
per client instance and carried through sessions — which is what an NFSv4
engineer means by "client". What this drill shows is neither that nor
necessarily one-to-one with it: it is a network source address observed
delivering traffic to a view path.

Labelling a source address "client" invites the reader to interpret it as
protocol client state, which `opstat` does not have — the monitor API exposes no
NFSv4 protocol-state telemetry at all
([D-001](D-001-monitor-api-and-exporter-are-separate-paths.md)).

Requested by the repository owner during real-cluster validation.

## Consequences

- Panel and footer read "v4 hosts".
- If NFSv4 `clientid`-scoped telemetry ever becomes available, "clients" is
  still free to mean the right thing, and the two can coexist without either
  being renamed.

## What would justify reopening

Nothing anticipated. If a genuine `clientid`-scoped view is ever built, it takes
the name "clients" and this drill keeps "hosts".
