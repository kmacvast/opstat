---
name: run-vms-discovery
description: >-
  Read-only interrogation of a VMS observability surface with
  --discover-metrics, and disciplined interpretation of the evidence file it
  produces. Use when a metric's availability, scope or semantics is unproven.
---

# run-vms-discovery

`vast_discovery.py` surveys what a cluster actually exposes: the OpenAPI
surface, the metric catalog, the Prometheus exporter, REST probe results and
counter-delta behavior. It exists because guessing what VMS publishes has been
wrong repeatedly in both directions.

## Preconditions

- **Authorized by the repository owner** for that specific cluster.
- Credentials from the environment (`VAST_TOKEN` / `VAST_PASSWORD`), never on
  the command line.
- **Load generators running throughout**, if anything about rates or counter
  behavior is to be concluded. On an idle cluster the deltas are zero — which is
  itself informative about cumulative-versus-instantaneous, but proves nothing
  about rates.
- Read `.claude/rules/vast-api-safety.md` first.

## Workflow

1. **Run it, and keep the file.**

   ```bash
   ./opstat --nfs --version=4.1 --vms <VMS_HOST> --user admin \
     --discover-metrics --no-color > nfs41-discovery.txt 2>&1
   ```

   Takes a few minutes and pauses ~10 s between two exporter scrapes so counter
   deltas can be computed. It also writes a full evidence file to
   `/tmp/opstat-nfs41-discovery-<vms>-<pid>.txt`.

2. **Read the evidence file, not the console summary.** This is the single most
   important step. A discovery report once contained `Nfs4Metrics` 944 times
   while the console printed only "1769 relevant metrics", and the finding was
   missed until the repository owner read the raw file. **A count is not
   evidence; names are.**

3. **Stay read-only.** Discovery issues `GET` only, plus temporary monitors it
   deletes. When it surfaces a mutating endpoint, recording it *is* the finding.
   Never call it — specifically, never the `nfs4_delegs` `DELETE` sibling.

4. **Interrogate rather than pattern-match.** Two matching defects have already
   been found in this tool itself:
   - "lock" matched 431 `BlockMetrics` names through the substring in
     "b-**lock**";
   - `nfs3_open_file_handle_cnt` was counted as an NFSv4 `OPEN` counter.

   Both were fixed with token-prefix matching and `nfs_`/`nfs4_` anchoring.
   Assume the next survey has a similar trap in it.

5. **Separate catalog presence from queryability.** A family appearing in the
   catalog does not mean a monitor will return it at that scope. Probe it. VAST
   OS 5.5.0.1 advertised `OPEN`/`CLOSE` counters no monitor would ever return.

6. **Separate the two data paths.** The monitor API and the Prometheus exporter
   have different shapes, semantics and costs. A metric absent from one may be
   present in the other — that is exactly how `Nfs4Metrics` was found after the
   monitor API returned zero matches for every NFSv4 protocol-state concept.

7. **State what was not proven.** An unmeasured semantic is unknown, not
   absent. Distinguish "the cluster does not publish this" from "this run did
   not observe it".

8. **If a semantic is proven, record it** as a decision under
   [`docs/decisions/`](../../../docs/decisions/) — with the evidence, not just
   the conclusion.

## Expected output

- The path to the evidence file, and confirmation you read it rather than the
  summary.
- Findings with the literal payload shapes observed — names, labels, values,
  error strings.
- An explicit list of what remains unproven.
- Any mutating endpoint discovered, recorded and **not** called.

## Stopping conditions

- Stop if the run was not authorized for that cluster.
- Stop before issuing any non-`GET` request outside the monitor lifecycle.
- Stop if a temporary monitor cannot be guaranteed deleted.
- Do not conclude a semantic from an idle window, a curated subset, or a name.
  An earlier claim that SEQUENCE ≈ the sum of other operations was withdrawn
  once it emerged the probe used a 14-operation list omitting `putfh`, `getfh`
  and `access`.
- Do not report a conclusion you cannot point at a line of the evidence file to
  support.
