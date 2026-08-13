# Engineering decisions

Durable decisions for `opstat`, with the evidence that settled them.

These are **not** point-in-time notes. A record here means the question was
investigated, the evidence is recorded, and a future session should not reopen
it casually. Branch state, SHAs, test counts, measurements and open work belong
in [REFACTOR_HANDOFF.md](../REFACTOR_HANDOFF.md); permanent behavioral policy
belongs in [AGENTS.md](../../AGENTS.md).

## How these records work

- **Accepted records are not rewritten.** To change one, add a new record that
  explains why, and mark the old one `Superseded by D-nnn`. The reasoning behind
  a decision is as valuable as the decision, and it is lost the moment someone
  edits it in place.
- **Reopening one is an L1 decision** — it requires new evidence and explicit
  approval. See the decision hierarchy in [AGENTS.md](../../AGENTS.md#decision-hierarchy).
- Every record states what would justify reopening it. If that condition
  occurs, reopening is expected, not forbidden.

All records below were established during the `refactor/tui-performance` effort
against a real cluster running **VAST OS 5.5.0.1** (3 cNodes, 429 views, 38–39
tenants), and recorded on 2026-08-13. Where a finding is version-specific, the
record says so.

## Index

| # | Decision | Area |
|---|---|---|
| [D-001](D-001-monitor-api-and-exporter-are-separate-paths.md) | The monitor API and the Prometheus exporter are separate data paths | Telemetry provenance |
| [D-002](D-002-nfs4metrics-counters-are-cumulative.md) | `Nfs4Metrics` counters are cumulative lifetime totals; rates come from differencing | Telemetry semantics |
| [D-003](D-003-nfs4metrics-latency-is-microseconds.md) | `Nfs4Metrics` latency is expressed in microseconds | Telemetry semantics |
| [D-004](D-004-heavy-exporter-endpoints-off-the-refresh-path.md) | `/prometheusmetrics/basic` stays off the refresh path; `/all` is never requested | API efficiency |
| [D-005](D-005-native-telemetry-is-an-on-demand-throttled-drill.md) | Native NFSv4 telemetry is an on-demand throttled drill, scraped synchronously | Architecture |
| [D-006](D-006-host-view-is-the-nfs4-attribution-source.md) | `host_view` is the NFSv4 host and view attribution source | Telemetry provenance |
| [D-007](D-007-topn-is-unusable-for-protocol-attribution.md) | `/monitors/topn/` cannot provide protocol-specific attribution | Telemetry provenance |
| [D-008](D-008-delegations-are-a-file-scoped-diagnostic.md) | NFSv4 delegations are a file-path-scoped diagnostic, never a panel | Scope / safety |
| [D-009](D-009-panels-are-evidence-gated.md) | Panels are evidence-gated; derived values are labelled; zero is not unavailable | Evidence |
| [D-010](D-010-merged-monitors-are-probe-validated-with-fallback.md) | Merged headline monitors are probe-validated with a fallback | API efficiency |
| [D-011](D-011-newest-complete-sample-scoped-per-family.md) | Select the newest *complete* sample, scoped per metric family | Telemetry correctness |
| [D-012](D-012-terminology-v4-hosts.md) | The NFSv4 client-attribution drill is called "v4 hosts" | Terminology |
