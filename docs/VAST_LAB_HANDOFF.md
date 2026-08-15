
When it finishes:

sed -n '/VAR203 AUTOMATED VALIDATION SUMMARY/,$p' \
  /tmp/opstat-var203-validation.txt

That second run is the one we care about now. It should tell us whether the state-aware driver fixes the bogus cNode/VIP/Host failures, and the new merge-legality probes should finally tell us whether there’s a safe path to reduce that brutally slow NVMe startup.


#############################################################################################################

vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ systemctl is-active block-loadgen.service nfs41-loadgen.service
active
active
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
export VAST_PASSWORD='123456'
test -n "$VAST_PASSWORD" && echo "VAST_PASSWORD present" || echo "NOT SET"
VAST_PASSWORD present
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ python3 scripts/var203_validation/run_var203_validation.py
opstat var203 automated validation
started 2026-08-15T05:17:25
=== prerequisites ===
host              : kevin-mcdonald-ubu-01
python            : 3.12.3
opstat            : /home/vastdata/git/opstat/opstat
branch            : refactor/tui-performance-local-continuation-wip
HEAD              : a60fecb9c498ce2a93d5fe1846ad49f1fb1fcdd1
credential        : VAST_PASSWORD present
dns               : var203.selab.vastdata.com -> 10.143.11.203
tcp               : var203.selab.vastdata.com:443 reachable

=== load generators ===
  block-loadgen.service                    active
  nfs3-loadgen.service                     active
  nfs41-loadgen.service                    active
  s3-loadgen.service                       active
  smb-loadgen.service                      active
NVMe/block figures need block load running. This script does not
start units (privileged, changes machine state). If block load is
inactive, run the documented installer/start yourself first:
  scripts/systemd/install-lab-loadgen-units.sh   (see scripts/README-systemd.md)

=== read-only probes (probe_var203.py) ===
  probe output        : /tmp/opstat-var203-probe.txt (rc=0)
  PROBE:batch.cnode.create PASS ids=[4, 3]
  PROBE:batch.cnode.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
  PROBE:batch.cnode.splittable PASS rows_per_object={"4": 120, "3": 120}
  PROBE:batch.vip.create PASS ids=[780, 57, 55, 683]
  PROBE:batch.vip.query PASS prop_list=['timestamp', 'object_id', 'TopNMetrics,read_req', 'TopNMetrics,read_latency__avg']
  PROBE:batch.vip.splittable FAIL rows_per_object={"780": 0, "57": 0, "55": 0, "683": 0}
  PROBE:batch.blockhost.create PASS ids=[1, 2, 3, 4]
  PROBE:batch.blockhost.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
  PROBE:batch.blockhost.splittable FAIL rows_per_object={"1": 0, "2": 0, "3": 0, "4": 0}
  PROBE:rank.cnode.accepted PASS scores(read_req d/s)={"4": 1118.461, "3": 0.0}
  PROBE:merge.data_pairs FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2409/query/ failed: HTTP 400: {"detail":"can't mix properties fro
  PROBE:merge.data_plus_fabric FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2410/query/ failed: HTTP 400: {"detail":"can't mix properties fro
  PROBE:latency.reference PASS values={'timestamp': '2026-08-15T05:19:43Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0}
  PROBE:latency.blockmetrics PASS values={'timestamp': '2026-08-15T05:19:43Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 546.5}
  PROBE:latency.host_view PASS 6 latency series
  PROBE:cleanup.exact_ids PASS all 8 session ids confirmed gone by per-id GET
  PROBE:batch.cnode.create PASS ids=[4, 3]
  PROBE:batch.cnode.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
  PROBE:batch.cnode.splittable PASS rows_per_object={"4": 120, "3": 120}
  PROBE:batch.vip.create PASS ids=[780, 57, 55, 683]
  PROBE:batch.vip.query PASS prop_list=['timestamp', 'object_id', 'TopNMetrics,read_req', 'TopNMetrics,read_latency__avg']
  PROBE:batch.vip.splittable FAIL rows_per_object={"780": 0, "57": 0, "55": 0, "683": 0}
  PROBE:batch.blockhost.create PASS ids=[1, 2, 3, 4]
  PROBE:batch.blockhost.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
  PROBE:batch.blockhost.splittable FAIL rows_per_object={"1": 0, "2": 0, "3": 0, "4": 0}
  PROBE:rank.cnode.accepted PASS scores(read_req d/s)={"4": 1118.461, "3": 0.0}
  PROBE:merge.data_pairs FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2409/query/ failed: HTTP 400: {"detail":"can't mix properties fro
  PROBE:merge.data_plus_fabric FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2410/query/ failed: HTTP 400: {"detail":"can't mix properties fro
  PROBE:latency.reference PASS values={'timestamp': '2026-08-15T05:19:43Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0}
  PROBE:latency.blockmetrics PASS values={'timestamp': '2026-08-15T05:19:43Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 546.5}
  PROBE:latency.host_view PASS 6 latency series
  PROBE:cleanup.exact_ids PASS all 8 session ids confirmed gone by per-id GET
RESULT:probe.run                    PASS       see /tmp/opstat-var203-probe.txt

=== A. NVMe startup + dashboard ===
  started nvme pid=3359875
  Connecting to              0.51s
  Preparing metrics          8.02s
  Gathering initial metrics  86.66s
  dashboard                  166.27s
  startup call durations:
RESULT:nvme.startup.phases          PASS       all three in order, dashboard at 166.27s
RESULT:nvme.footer                  PASS       footer present in dashboard

--- F. Fabric / workload panel (verbatim frame excerpt) ---
  |   Cluster selab-var-203   VMS var203.selab.vastdata.com:443   Refresh 5s   vast-os-release-5.4.6.0
  | ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  | ┌─ BLOCK HEALTH & WORKLOAD ────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Scope  All Volumes                                                                                                   │
  | │ [ IDLE ]   - ops/s   •  ● - ms   ► 0.161 GB/s                                                                        │
  | │ Workload  fabric-overhead dominant / idle data workload                                                              │
  | │ Read      ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Write     ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Reclaim   ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Fabric    ██████████████████████  100.0%  of all activity                                                            │
  | │ Sample: 2026-08-15T05:22:13Z (warming up - need 2nd sample)   Mode: latest   Frame: 10m                              │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ┌─ PERFORMANCE INSIGHTS ───────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Top Contributor  FABRIC REQ HANDLE  50.0% of ops                                                                     │
  | │ Highest Latency  FABRIC REQ HANDLE   ● 841 µs                                                                        │
  | │ Data Consumer    -                                                                                                   │
  | └───────────────────────────────────────────────────────────────────────────────────────────────────────────��──────────┘
  | ┌─ OPERATIONS ─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Operation                         IOPS      Throughput      Avg Size         Latency                                 │
  | ├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  | │ READ                                 -     157.94 MB/s             -               -                                 │
  | │ WRITE                                -       6.71 MB/s             -               -                                 │
  | │ COMPARE & WRITE                      -               -             -               -                                 │
  | │ UNMAP (TRIM)                         -               -             -               -                                 │
  | │ WRITE ZEROES                         -               -             -               -                                 │
  | │ FABRIC DISCOVERY                     -               -             -               -                                 │
  | │ FABRIC REQ HANDLE              2,486.8               -             -          841 µs                                 │
  | │ FABRIC XPORT FREE              2,486.8               -             -           17 µs                                 │
  | │ ADMIN GET NS                         -               -             -               -                                 │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
RESULT:fabric.captured              PASS       31 panel lines captured for manual % verification

=== NVMe CNODE drill (key 'c') ===
  entry wall-clock    : 107.63s  (to panel render)
  entry API calls     : 21  (keypress -> panel rendered)
  monitors created    : 9
  layout              : batch
  rows observed       : ['cnode-3-7', 'cnode-3-8']
    GET /monitors/<id>/query/                      10
    POST /monitors/                                9
    GET /cnodes/                                   1
    DELETE /monitors/<id>/                         1
  in 45s idle          : 8 calls, 8 queries
RESULT:nvme.cnode.manual_refresh    PASS       space forced 1 queries (1 calls)
RESULT:nvme.cnode.exit_x            FAIL       still in drill after x (waited 150s)
RESULT:nvme.cnode.entry             PASS       21 calls, batch layout, 2 rows, 107.63s

=== NVMe VIP drill (key 'i') ===
RESULT:nvme.vip.open                FAIL       panel 'VIP PATHS' never rendered within 420s
  entry wall-clock    : 464.27s  (to panel render)
  entry API calls     : 137  (keypress -> panel rendered)
  monitors created    : 43
  layout              : per-object
  rows observed       : none parsed
    DELETE /monitors/<id>/                         50
    POST /monitors/                                43
    GET /monitors/<id>/query/                      43
    GET /vips/                                     1
  in 45s idle          : 10 calls, 3 queries
RESULT:nvme.vip.manual_refresh      FAIL       no effect
RESULT:nvme.vip.exit_x              PASS       x returned to the dashboard
RESULT:nvme.vip.entry               FAIL       137 calls, per-object layout, 0 rows, 464.27s

=== NVMe HOST drill (key 'h') ===
  WARN: no loading frame within 150s of 'h'
RESULT:nvme.host.open               FAIL       panel 'HOST INITIATORS' never rendered within 420s
  entry wall-clock    : 720.40s  (to panel render)
  entry API calls     : 122  (keypress -> panel rendered)
  monitors created    : 41
  layout              : per-object
  rows observed       : none parsed
    POST /monitors/                                41
    GET /monitors/<id>/query/                      41
    DELETE /monitors/<id>/                         40
  in 45s idle          : 12 calls, 4 queries
RESULT:nvme.host.manual_refresh     FAIL       no effect
RESULT:nvme.host.exit_x             PASS       x returned to the dashboard
RESULT:nvme.host.entry              FAIL       122 calls, per-object layout, 0 rows, 720.40s

=== E. Navigation bindings ===
RESULT:nav.legend.i                 PASS       '[i] VIP' in footer
RESULT:nav.legend.x                 PASS       '[x] Exit drill' in footer
RESULT:nav.legend.space             PASS       '[space] Refresh' in footer
RESULT:nav.legend.no_v_vip          PASS       [v] VIP absent
RESULT:nav.legend.no_p_exit         PASS       [p] absent
RESULT:nav.p_does_not_exit          FAIL       p exited the drill - retired binding is still live
RESULT:nav.v_is_not_vip             PASS       v did not open VIP

=== G. NVMe shutdown ===
  nvme did not exit on q; sending SIGTERM (never SIGKILL)
  shutdown wall-clock : 362.44s
  exit code           : None
RESULT:nvme.shutdown.frame          PASS       'Cleaning up' shown before the drain
RESULT:nvme.shutdown.exit           FAIL       exit=None in 362.44s

=== nvme cleanup accounting ===
  api log             : /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3359875.log
  total API calls     : 644
  ids created         : [2413, 2414, 2415, 2416, 2417, 2418, 2419, 2420, 2421, 2422, 2423, 2424, 2425, 2426, 2427, 2428, 2429, 2430, 2431, 2432, 2433, 2434, 2435, 2436, 2437, 2438, 2439, 2440, 2441, 2442, 2443, 2444, 2445, 2446, 2447, 2448, 2449, 2450, 2451, 2452, 2453, 2454, 2455, 2456, 2457, 2458, 2459, 2460, 2461, 2462, 2463, 2464, 2465, 2466, 2467, 2468, 2469, 2470, 2471, 2472, 2473, 2474, 2475, 2476, 2477, 2478, 2479, 2480, 2481, 2482, 2483, 2484, 2485, 2486, 2487, 2488, 2489, 2490, 2491, 2492, 2493, 2494, 2495, 2496, 2497, 2498, 2499, 2500, 2501, 2502, 2503, 2504, 2505, 2506, 2507, 2508, 2509, 2510, 2511, 2512, 2513, 2514, 2515, 2516, 2517, 2518, 2519, 2520, 2521, 2522, 2523, 2524, 2525, 2526, 2527, 2528, 2529, 2530, 2531, 2532, 2533, 2534, 2535, 2536, 2537, 2538, 2539, 2540, 2541, 2542, 2543, 2544, 2545, 2546, 2547, 2548, 2549, 2550, 2551, 2552, 2553, 2554, 2555, 2556, 2557, 2558, 2559, 2560, 2561, 2562, 2563, 2564, 2565, 2566, 2567, 2568, 2569, 2570, 2571, 2572, 2573, 2574, 2575, 2576, 2577, 2578, 2579, 2580, 2581, 2582, 2583, 2584, 2585, 2586, 2587, 2588, 2589, 2590, 2591, 2592, 2593, 2594, 2595, 2596, 2597, 2598, 2599, 2600, 2601, 2602, 2603, 2604, 2605, 2606, 2607, 2608, 2609, 2610, 2611, 2612, 2613, 2614, 2615, 2616, 2617, 2618]
  ids deleted         : [2421, 2422, 2423, 2424, 2425, 2426, 2427, 2428, 2429, 2430, 2431, 2432, 2433, 2434, 2435, 2436, 2437, 2438, 2439, 2440, 2441, 2442, 2443, 2444, 2445, 2446, 2447, 2448, 2449, 2450, 2451, 2452, 2453, 2454, 2455, 2456, 2457, 2458, 2459, 2460, 2461, 2462, 2463, 2464, 2465, 2466, 2467, 2468, 2469, 2470, 2471, 2472, 2473, 2474, 2475, 2476, 2477, 2478, 2479, 2480, 2481, 2482, 2483, 2484, 2485, 2486, 2487, 2488, 2489, 2490, 2491, 2492, 2493, 2494, 2495, 2496, 2497, 2498, 2499, 2500, 2501, 2502, 2503, 2504, 2505, 2506, 2507, 2508, 2509, 2510, 2511, 2512, 2513, 2514, 2515, 2516, 2517, 2518, 2519, 2520, 2521, 2522, 2523, 2524, 2525, 2526, 2527, 2528, 2529, 2530, 2531, 2532, 2533, 2534, 2535, 2536, 2537, 2538, 2539, 2540, 2541, 2542, 2543, 2544, 2545, 2546, 2547, 2548, 2549, 2550, 2551, 2552, 2553, 2554, 2555, 2556, 2557, 2558, 2559, 2560, 2561, 2562, 2563, 2564, 2565, 2566, 2567, 2568, 2569, 2570, 2571, 2572, 2573, 2574, 2575, 2576, 2577, 2578, 2579, 2580, 2581, 2582, 2583, 2584, 2585, 2586, 2587, 2588, 2589, 2590, 2591, 2592, 2593, 2594, 2595, 2596, 2597, 2598, 2599, 2600, 2601, 2602, 2603, 2604, 2605, 2606, 2607, 2608, 2609, 2610, 2611, 2612, 2613, 2614, 2615, 2616, 2617]
  ids still present   : [2413, 2414, 2415, 2416, 2417, 2418, 2419, 2420, 2618]
RESULT:nvme.cleanup                 FAIL       STILL PRESENT: [2413, 2414, 2415, 2416, 2417, 2418, 2419, 2420, 2618]
  whole-session call breakdown:
    GET /monitors/<id>/query/                      238
    POST /monitors/                                206
    DELETE /monitors/<id>/                         197
    GET /clusters/                                 1
    GET /cnodes/                                   1
    GET /vips/                                     1

=== other protocol: smb ===
  started smb pid=500653
RESULT:smb.startup.phases           PASS       3/3 phases seen
RESULT:smb.footer                   PASS       footer present
  footer: │ [q] Quit |[c] cNode |[v] View |[t] Tenant |[x] Exit drill |[space] Refresh                                           │
RESULT:smb.exit                     PASS       exit=0

=== smb cleanup accounting ===
  api log             : /tmp/opstat-api-smb-var203.selab.vastdata.com-443-500653.log
  total API calls     : 7
  ids created         : [2619]
  ids deleted         : [2619]
  ids still present   : NONE
RESULT:smb.cleanup                  PASS       all 1 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      2
    GET /clusters/                                 1
    POST /monitors/                                1
    GET /monitors/topn/                            1
    GET /openfilehandles/                          1
    DELETE /monitors/<id>/                         1

=== other protocol: s3 ===
  started s3 pid=525320
RESULT:s3.startup.phases            PASS       3/3 phases seen
RESULT:s3.footer                    PASS       footer present
  footer: │ [q] Quit |[c] cNode |[t] Tenant |[i] VIP |[x] Exit drill |[space] Refresh |[b] Bucket                                │
RESULT:s3.exit                      PASS       exit=0

=== s3 cleanup accounting ===
  api log             : /tmp/opstat-api-s3-var203.selab.vastdata.com-443-525320.log
  total API calls     : 9
  ids created         : [2620, 2621]
  ids deleted         : [2620, 2621]
  ids still present   : NONE
RESULT:s3.cleanup                   PASS       all 2 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    POST /monitors/                                3
    GET /monitors/<id>/query/                      3
    DELETE /monitors/<id>/                         2
    GET /clusters/                                 1

=== other protocol: nfs_v3 ===
  started nfs_v3 pid=577977
RESULT:nfs_v3.startup.phases        PASS       3/3 phases seen
RESULT:nfs_v3.footer                PASS       footer present
  footer: [q] Quit |[o] Ops |[l] Lat |[c] cNode |[v] View |[t] Tenant |[x] Exit drill |[space] Refresh |[r] RPC |[w] Work
RESULT:nfs_v3.exit                  PASS       exit=0

=== nfs_v3 cleanup accounting ===
  api log             : /tmp/opstat-api-nfs-v3-var203.selab.vastdata.com-443-577977.log
  total API calls     : 5
  ids created         : [2622]
  ids deleted         : [2622]
  ids still present   : NONE
RESULT:nfs_v3.cleanup               PASS       all 1 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      2
    GET /clusters/                                 1
    POST /monitors/                                1
    DELETE /monitors/<id>/                         1

=== other protocol: nfs_v41 ===
  started nfs_v41 pid=607982
RESULT:nfs_v41.startup.phases       PASS       3/3 phases seen
RESULT:nfs_v41.footer               PASS       footer present
  footer: │ [q] Quit |[o] Ops |[l] Lat |[n] Name |[c] cNode |[v] View |[t] Tenant |[x] Exit drill |[space] Refresh |[4] Native v4 |[h] v4 hosts      │
RESULT:nfs_v41.exit                 PASS       exit=0

=== nfs_v41 cleanup accounting ===
  api log             : /tmp/opstat-api-nfs-v41-var203.selab.vastdata.com-443-607982.log
  total API calls     : 6
  ids created         : [2623]
  ids deleted         : [2623]
  ids still present   : NONE
RESULT:nfs_v41.cleanup              PASS       all 1 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      2
    GET /clusters/                                 1
    GET /metrics/                                  1
    POST /monitors/                                1
    DELETE /monitors/<id>/                         1

======================================================================
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: a60fecb9c498ce2a93d5fe1846ad49f1fb1fcdd1
Target VMS: var203.selab.vastdata.com
Start: 2026-08-15T05:17:25
End: 2026-08-15T06:15:59

probe.run                          PASS        see /tmp/opstat-var203-probe.txt
nvme.startup.phases                PASS        all three in order, dashboard at 166.27s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          PASS        space forced 1 queries (1 calls)
nvme.cnode.exit_x                  FAIL        still in drill after x (waited 150s)
nvme.cnode.entry                   PASS        21 calls, batch layout, 2 rows, 107.63s
nvme.vip.open                      FAIL        panel 'VIP PATHS' never rendered within 420s
nvme.vip.manual_refresh            FAIL        no effect
nvme.vip.exit_x                    PASS        x returned to the dashboard
nvme.vip.entry                     FAIL        137 calls, per-object layout, 0 rows, 464.27s
nvme.host.open                     FAIL        panel 'HOST INITIATORS' never rendered within 420s
nvme.host.manual_refresh           FAIL        no effect
nvme.host.exit_x                   PASS        x returned to the dashboard
nvme.host.entry                    FAIL        122 calls, per-object layout, 0 rows, 720.40s
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                FAIL        p exited the drill - retired binding is still live
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 FAIL        exit=None in 362.44s
nvme.cleanup                       FAIL        STILL PRESENT: [2413, 2414, 2415, 2416, 2417, 2418, 2419, 2420, 2618]
smb.startup.phases                 PASS        3/3 phases seen
smb.footer                         PASS        footer present
smb.exit                           PASS        exit=0
smb.cleanup                        PASS        all 1 session monitors deleted (per-id GET, 404=gone)
s3.startup.phases                  PASS        3/3 phases seen
s3.footer                          PASS        footer present
s3.exit                            PASS        exit=0
s3.cleanup                         PASS        all 2 session monitors deleted (per-id GET, 404=gone)
nfs_v3.startup.phases              PASS        3/3 phases seen
nfs_v3.footer                      PASS        footer present
nfs_v3.exit                        PASS        exit=0
nfs_v3.cleanup                     PASS        all 1 session monitors deleted (per-id GET, 404=gone)
nfs_v41.startup.phases             PASS        3/3 phases seen
nfs_v41.footer                     PASS        footer present
nfs_v41.exit                       PASS        exit=0
nfs_v41.cleanup                    PASS        all 1 session monitors deleted (per-id GET, 404=gone)

PASS: probe.run, nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.manual_refresh, nvme.cnode.entry, nvme.vip.exit_x, nvme.host.exit_x, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.v_is_not_vip, nvme.shutdown.frame, smb.startup.phases, smb.footer, smb.exit, smb.cleanup, s3.startup.phases, s3.footer, s3.exit, s3.cleanup, nfs_v3.startup.phases, nfs_v3.footer, nfs_v3.exit, nfs_v3.cleanup, nfs_v41.startup.phases, nfs_v41.footer, nfs_v41.exit, nfs_v41.cleanup
FAIL: nvme.cnode.exit_x, nvme.vip.open, nvme.vip.manual_refresh, nvme.vip.entry, nvme.host.open, nvme.host.manual_refresh, nvme.host.entry, nav.p_does_not_exit, nvme.shutdown.exit, nvme.cleanup
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)

wrote /tmp/opstat-var203-validation.txt
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$

vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
sed -n '/VAR203 AUTOMATED VALIDATION SUMMARY/,$p' \
  /tmp/opstat-var203-validation.txt
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: a60fecb9c498ce2a93d5fe1846ad49f1fb1fcdd1
Target VMS: var203.selab.vastdata.com
Start: 2026-08-15T05:17:25
End: 2026-08-15T06:15:59

probe.run                          PASS        see /tmp/opstat-var203-probe.txt
nvme.startup.phases                PASS        all three in order, dashboard at 166.27s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          PASS        space forced 1 queries (1 calls)
nvme.cnode.exit_x                  FAIL        still in drill after x (waited 150s)
nvme.cnode.entry                   PASS        21 calls, batch layout, 2 rows, 107.63s
nvme.vip.open                      FAIL        panel 'VIP PATHS' never rendered within 420s
nvme.vip.manual_refresh            FAIL        no effect
nvme.vip.exit_x                    PASS        x returned to the dashboard
nvme.vip.entry                     FAIL        137 calls, per-object layout, 0 rows, 464.27s
nvme.host.open                     FAIL        panel 'HOST INITIATORS' never rendered within 420s
nvme.host.manual_refresh           FAIL        no effect
nvme.host.exit_x                   PASS        x returned to the dashboard
nvme.host.entry                    FAIL        122 calls, per-object layout, 0 rows, 720.40s
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                FAIL        p exited the drill - retired binding is still live
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 FAIL        exit=None in 362.44s
nvme.cleanup                       FAIL        STILL PRESENT: [2413, 2414, 2415, 2416, 2417, 2418, 2419, 2420, 2618]
smb.startup.phases                 PASS        3/3 phases seen
smb.footer                         PASS        footer present
smb.exit                           PASS        exit=0
smb.cleanup                        PASS        all 1 session monitors deleted (per-id GET, 404=gone)
s3.startup.phases                  PASS        3/3 phases seen
s3.footer                          PASS        footer present
s3.exit                            PASS        exit=0
s3.cleanup                         PASS        all 2 session monitors deleted (per-id GET, 404=gone)
nfs_v3.startup.phases              PASS        3/3 phases seen
nfs_v3.footer                      PASS        footer present
nfs_v3.exit                        PASS        exit=0
nfs_v3.cleanup                     PASS        all 1 session monitors deleted (per-id GET, 404=gone)
nfs_v41.startup.phases             PASS        3/3 phases seen
nfs_v41.footer                     PASS        footer present
nfs_v41.exit                       PASS        exit=0
nfs_v41.cleanup                    PASS        all 1 session monitors deleted (per-id GET, 404=gone)

PASS: probe.run, nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.manual_refresh, nvme.cnode.entry, nvme.vip.exit_x, nvme.host.exit_x, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.v_is_not_vip, nvme.shutdown.frame, smb.startup.phases, smb.footer, smb.exit, smb.cleanup, s3.startup.phases, s3.footer, s3.exit, s3.cleanup, nfs_v3.startup.phases, nfs_v3.footer, nfs_v3.exit, nfs_v3.cleanup, nfs_v41.startup.phases, nfs_v41.footer, nfs_v41.exit, nfs_v41.cleanup
FAIL: nvme.cnode.exit_x, nvme.vip.open, nvme.vip.manual_refresh, nvme.vip.entry, nvme.host.open, nvme.host.manual_refresh, nvme.host.entry, nav.p_does_not_exit, nvme.shutdown.exit, nvme.cleanup
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$


