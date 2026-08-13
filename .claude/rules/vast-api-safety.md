# VAST API safety

Permanent rule for `opstat`. Applies to every code path that issues a request
to a VMS, and to every diagnostic run against one.

Short form lives in [AGENTS.md](../../AGENTS.md). This file holds the evidence.

## Why this rule exists

`opstat` is pointed at production storage clusters that other people depend on.
It has no business changing anything. Three specific hazards have already been
met on this branch.

**Swagger advertises destructive siblings.** `GET /tenants/{id}/nfs4_delegs/`
is a legitimate read. It has a `DELETE` sibling that would revoke live NFSv4
delegations on a running cluster. Discovery enumerates the OpenAPI surface, so
that endpoint appears in front of the agent as a discovered capability. **The
existence of an endpoint in a schema is not permission to call it.**

**Destructiveness is a property of the effect, not the stated purpose.** A
measurement, a retry, or a "quick reproduction" can mutate state. Judge the
effect.

**Temporary monitors are real cluster objects.** `opstat` creates ad-hoc VMS
monitors named `adhoc_opstat_*`. One that is not deleted persists on the
customer's cluster after the process exits. Drills at one point held nine live
monitors simultaneously.

And one hazard of a different kind: **catalog presence is not queryability.** A
real VAST OS 5.5.0.1 build advertised `OPEN`/`CLOSE` counters that no monitor
would ever return. Anything gated on the catalog rather than on a live result
will display metrics the cluster does not measure.

## Mandatory behavior

### Read-only by default

- Discovery (`--discover-metrics`) and all diagnostics issue **`GET` only**,
  plus temporary monitors that are created and deleted.
- Creating a temporary monitor is the *only* sanctioned write, and it is
  sanctioned because it is always undone.
- Any other non-`GET` request against a VMS is an **L1 action** requiring
  explicit approval for that specific call, on that specific cluster.
- Never change cluster configuration.

### Never call a destructive endpoint because it exists

- The `nfs4_delegs` `DELETE` sibling must never be invoked. There is a test
  asserting it is not.
- When discovery surfaces a mutating endpoint, **record it and move on.**
  Enumerating it is the finding; calling it is not.
- Do not construct a mutating request by hand (`curl -X DELETE`, a hand-rolled
  `http.client` call) to "just check" behavior.

### Clean up temporary monitors, including on the error path

- Every path that creates a monitor deletes it — success, exception, keyboard
  interrupt, and teardown.
- Tests assert `vms.live_monitors() == {}` after teardown. Keep that assertion
  in any new path.
- On quit, no `adhoc_opstat_*` monitor may remain on the VMS. This is checkable
  on a real cluster and is part of the validation cookbook.

### Credentials never enter files, logs, or documentation

- Auth comes from `VAST_TOKEN` / `VAST_PASSWORD` or an interactive prompt.
- `--password` works but warns: it leaks through `ps` and shell history. Use the
  environment variable in every example.
- `--log-api-calls` writes request and response bodies to `/tmp`. Those files
  contain cluster identifiers and must never be committed.
- Discovery reports (`/tmp/opstat-nfs41-discovery-*.txt`) are the same.
- Use `<VMS_HOST>` placeholders in tracked files.

### Monitor API and Prometheus exporter are different systems

They are separate data paths with different semantics, and conflating them
produces confident wrong answers. See
[docs/decisions/D-001-monitor-api-and-exporter-are-separate-paths.md](../../docs/decisions/D-001-monitor-api-and-exporter-are-separate-paths.md).

| | Monitor API (`/monitors/`) | Prometheus exporter (`/prometheusmetrics/*`) |
|---|---|---|
| Shape | Sampled rows on a schedule, `prop_list`-indexed | Text exposition, label-scoped series |
| Newest sample | May be **partially filled** — select the newest *complete* row, scoped per metric family | N/A |
| `Nfs4Metrics` counters | absent | **cumulative lifetime totals** published as gauges; rates require differencing |
| `host_view` gauges | absent | **instantaneous**; one scrape, no differencing |
| Cost | small | 276 KB – 4.8 MB, 1.2–9.0 s |

Never present a figure from one as equivalent to a figure from the other.
`ViewMetrics` reported no meaningful NFSv4 activity while `Nfs4Metrics`
measured ~1553 SEQUENCE/s on the same cluster at the same time. Both were
working correctly; they measure different things.

### Never fabricate a metric or a derivation

- Panels are **evidence-gated**: a row appears only when a live query actually
  returned that property.
- A derived figure is labelled as derived — operations per compound prints
  `DERIVED RATIO (not a native metric)` because VMS publishes no compound
  counter.
- **`0` and "unavailable" are different values.** A measured `0.00 ops/s` means
  no traffic; `-` means no data. Never collapse them.
- Do not infer units, scope, or cumulative-versus-instantaneous from a metric
  name. Every semantic this project relies on was proven and recorded in
  [docs/decisions/](../../docs/decisions/).
- Do not derive a value across metric families to fill a gap. An earlier claim
  that SEQUENCE ≈ the sum of other operations was withdrawn once it emerged
  that the probe used a curated 14-operation list omitting `putfh`, `getfh` and
  `access` — the apparent match was an artifact of the omission.

### Request volume is a regression dimension

The invariants table is in [AGENTS.md](../../AGENTS.md#api-efficiency-principles).
The two that are most easily broken by accident:

- **`/prometheusmetrics/*` must never enter a normal refresh path.** Moving
  `basic` onto the 5-second NFSv4.1 path requires new evidence and explicit
  approval (L1). See
  [docs/decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md](../../docs/decisions/D-004-heavy-exporter-endpoints-off-the-refresh-path.md).
- **Keep-alive connection reuse is load-bearing.** `vast_common.request` holds
  one persistent HTTPS connection per session; a handshake per call was ~10x
  slower. Do not change that behavior incidentally.

## What automated enforcement detects

- `tests/mock_vms.py` records **every** request. `tests/test_api_efficiency.py`
  and `tests/test_drill_semantics.py` assert per-refresh and per-drill call
  budgets, keep-alive reuse, and monitor cleanup.
- A test asserts the `nfs4_delegs` `DELETE` sibling is never invoked.
- Tests assert `vms.live_monitors() == {}` after teardown.
- `.claude/settings.json` denies hand-rolled mutating HTTP from the shell
  (`curl -X DELETE/POST/PUT/PATCH` and long-form equivalents) and denies reading
  key and environment files.

## What automated enforcement CANNOT detect

- **A mutating request issued from Python rather than the shell.** Nothing stops
  `http.client` or `urllib` inside a script; the `curl` denials only cover the
  shell.
- **Which cluster is on the other end.** `./opstat --vms <host>` looks identical
  whether `<host>` is a lab machine or a customer's production cluster.
- **Whether a displayed number is honest.** The tests prove a value reached the
  panel; they cannot prove the label above it describes what it actually is.
  Evidence-gating is enforceable; *truthful labelling is not.*
- **A monitor leaked on a path with no test.** The assertions cover the paths
  that have tests. A new code path with no test has no cleanup guarantee — the
  NFSv4.1 drill once failed to display anything *and* leaked monitors because a
  function assigned a module global without `global`, which is why
  `tests/test_globals_hygiene.py` exists.
- **Real-cluster behavior.** The mock reproduces known quirks; it cannot
  reproduce unknown ones. Several defects on this branch appeared only against
  VAST OS 5.5.0.1.

## Stopping conditions

Stop and report rather than proceeding when:

- Progress would require any non-`GET` request outside the monitor lifecycle.
- Discovery surfaces a capability whose semantics you cannot prove from the
  data you actually have.
- A metric's units, scope, or cumulative-versus-instantaneous nature is
  unproven, and displaying it would require guessing.
- A monitor cannot be guaranteed deleted on every path.
- You would need to put a real credential or cluster identifier into a file.
