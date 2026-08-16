  ids created         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      244
    POST /monitors/                                20
    DELETE /monitors/<id>/                         20
    GET /cnodes/                                   2
    GET /clusters/                                 1
    GET /vips/                                     1
    GET /blockhosts/                               1

======================================================================
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: 4657b108f941652226fb5cde88874b639aa089ca
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T15:47:01
End: 2026-08-16T16:09:21

nvme.startup.phases                PASS        all three in order, dashboard at 78.16s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          FAIL        no effect
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 119.66s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.vip.entry                     PASS        93 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.host.entry                    PASS        98 calls, 1 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 87.22s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL: nvme.cnode.manual_refresh
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)

wrote /tmp/opstat-var203-validation.txt
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$


  ids created         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      244
    POST /monitors/                                20
    DELETE /monitors/<id>/                         20
    GET /cnodes/                                   2
    GET /clusters/                                 1
    GET /vips/                                     1
    GET /blockhosts/                               1

======================================================================
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: 4657b108f941652226fb5cde88874b639aa089ca
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T15:47:01
End: 2026-08-16T16:09:21

nvme.startup.phases                PASS        all three in order, dashboard at 78.16s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          FAIL        no effect
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 119.66s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.vip.entry                     PASS        93 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.host.entry                    PASS        98 calls, 1 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 87.22s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL: nvme.cnode.manual_refresh
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)

wrote /tmp/opstat-var203-validation.txt
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
sed -n '/VAR203 AUTOMATED VALIDATION SUMMARY/,$p' \
  /tmp/opstat-var203-validation.txt
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: 4657b108f941652226fb5cde88874b639aa089ca
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T15:47:01
End: 2026-08-16T16:09:21

nvme.startup.phases                PASS        all three in order, dashboard at 78.16s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          FAIL        no effect
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 119.66s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.vip.entry                     PASS        93 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.host.entry                    PASS        98 calls, 1 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 87.22s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL: nvme.cnode.manual_refresh
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ grep -n "api log" /tmp/opstat-var203-validation.txt
ls -lt /tmp/opstat-api-nvme-tcp-*.log | head
108:  api log             : /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2266877.log
-rw------- 1 vastdata vastdata 1457580 Aug 16 16:09 /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2266877.log
-rw------- 1 vastdata vastdata  543300 Aug 15 22:37 /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3930933.log
-rw------- 1 vastdata vastdata  267104 Aug 15 17:41 /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1344505.log
-rw------- 1 vastdata vastdata  645062 Aug 15 06:07 /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3359875.log
-rw------- 1 vastdata vastdata  722186 Aug 14 20:51 /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2044111.log
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$  cat /tmp/opstat-var203-validation.txt
ROUND-5 MODE: NVMe only - re-validating the bounded dead-scope probe, nav, shutdown and the session monitor budget. Everything else is already established.
opstat var203 automated validation
started 2026-08-16T15:47:01
=== prerequisites ===
host              : kevin-mcdonald-ubu-01
python            : 3.12.3
opstat            : /home/vastdata/git/opstat/opstat
branch            : refactor/tui-performance-local-continuation-wip
HEAD              : 4657b108f941652226fb5cde88874b639aa089ca
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

=== A. NVMe startup + dashboard ===
  started nvme pid=2266877
  Connecting to              0.50s
  Preparing metrics          32.04s
  Gathering initial metrics  64.64s
  dashboard                  78.16s
  startup call durations:
RESULT:nvme.startup.phases          PASS       all three in order, dashboard at 78.16s
RESULT:nvme.footer                  PASS       footer present in dashboard

--- F. Fabric / workload panel (verbatim frame excerpt) ---
  |   Cluster selab-var-203   VMS var203.selab.vastdata.com:443   Refresh 5s   vast-os-release-5.4.6.0
  | ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  | ┌─ BLOCK HEALTH & WORKLOAD ────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Scope  All Volumes                                                                                                   │
  | │ [ IDLE ]   - ops/s   •  ● - ms   ► 0.228 GB/s                                                                        │
  | │ Workload  fabric-overhead dominant / idle data workload                                                              │
  | │ Read      ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Write     ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Reclaim   ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Fabric    ██████████████████████  100.0%  of all activity                                                            │
  | │ Sample: 2026-08-16T15:48:03Z (warming up - need 2nd sample)   Mode: latest   Frame: 10m                              │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ┌─ PERFORMANCE INSIGHTS ───────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Top Contributor  FABRIC REQ HANDLE  50.0% of ops                                                                     │
  | │ Highest Latency  FABRIC REQ HANDLE   ● 1.18 ms                                                                       │
  | │ Data Consumer    -                                                                                                   │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ┌─ OPERATIONS ─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Operation                         IOPS      Throughput      Avg Size         Latency                                 │
  | ├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  | │ READ                                 -     229.91 MB/s             -               -                                 │
  | │ WRITE                                -       3.10 MB/s             -               -                                 │
  | │ COMPARE & WRITE                      -               -             -               -                                 │
  | │ UNMAP (TRIM)                         -               -             -               -                                 │
  | │ WRITE ZEROES                         -               -             -               -                                 │
  | │ FABRIC DISCOVERY                     -               -             -               -                                 │
  | │ FABRIC REQ HANDLE              1,814.6               -             -         1.18 ms                                 │
  | │ FABRIC XPORT FREE              1,814.6               -             -           79 µs                                 │
  | │ ADMIN GET NS                         -               -             -               -                                 │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
RESULT:fabric.captured              PASS       31 panel lines captured for manual % verification

=== NVMe CNODE drill (key 'c') ===
  entry wall-clock    : 119.66s  (to panel render)
  entry API calls     : 13  (keypress -> panel rendered)
  monitors created    : 5
  layout              : batch
  rows observed       : ['cnode-3-7', 'cnode-3-8']
    GET /monitors/<id>/query/                      6
    POST /monitors/                                5
    GET /cnodes/                                   1
    DELETE /monitors/<id>/                         1
  in 45s idle          : 6 calls, 6 queries
RESULT:nvme.cnode.manual_refresh    FAIL       no effect
RESULT:nvme.cnode.exit_x            PASS       x returned to the dashboard
RESULT:nvme.cnode.entry             PASS       13 calls, batch layout, 2 rows, 119.66s

=== NVMe VIP drill (key 'i') ===
RESULT:nvme.vip.open                PASS       honest no-telemetry notice rendered (1 creates, 421s)
RESULT:nvme.vip.entry               PASS       93 calls, 1 creates - bounded probe, no fan-out

=== NVMe HOST drill (key 'h') ===
RESULT:nvme.host.open               PASS       honest no-telemetry notice rendered (1 creates, 421s)
RESULT:nvme.host.entry              PASS       98 calls, 1 creates - bounded probe, no fan-out

=== E. Navigation bindings ===
RESULT:nav.legend.i                 PASS       '[i] VIP' in footer
RESULT:nav.legend.x                 PASS       '[x] Exit drill' in footer
RESULT:nav.legend.space             PASS       '[space] Refresh' in footer
RESULT:nav.legend.no_v_vip          PASS       [v] VIP absent
RESULT:nav.legend.no_p_exit         PASS       [p] absent
RESULT:nav.p_does_not_exit          PASS       p left the cNode drill open
RESULT:nav.v_is_not_vip             PASS       v did not open VIP

=== G. NVMe shutdown ===
  shutdown wall-clock : 87.22s
  exit code           : 0
RESULT:nvme.shutdown.frame          PASS       'Cleaning up' shown before the drain
RESULT:nvme.shutdown.exit           PASS       exit=0 in 87.22s

=== nvme cleanup accounting ===
  api log             : /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2266877.log
  total API calls     : 289
  ids created         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      244
    POST /monitors/                                20
    DELETE /monitors/<id>/                         20
    GET /cnodes/                                   2
    GET /clusters/                                 1
    GET /vips/                                     1
    GET /blockhosts/                               1

======================================================================
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: 4657b108f941652226fb5cde88874b639aa089ca
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T15:47:01
End: 2026-08-16T16:09:21

nvme.startup.phases                PASS        all three in order, dashboard at 78.16s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          FAIL        no effect
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 119.66s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.vip.entry                     PASS        93 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.host.entry                    PASS        98 calls, 1 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 87.22s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL: nvme.cnode.manual_refresh
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$



for file in  "/tmp/opstat-var203-validation.txt" "/tmp/opstat-var203-probe.txt" "/tmp/opstat-api-*.log"
do
echo
echo "#############################################################################################"
echo "File $file:"
cat $file
echo "#############################################################################################"


> ^C
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
for file in  '/tmp/opstat-var203-validation.txt' '/tmp/opstat-var203-probe.txt' '/tmp/opstat-api-*.log'
do
echo
echo "#############################################################################################"
echo "File $file:"
cat $file
echo "#############################################################################################"
done


#############################################################################################
File /tmp/opstat-var203-validation.txt:
ROUND-5 MODE: NVMe only - re-validating the bounded dead-scope probe, nav, shutdown and the session monitor budget. Everything else is already established.
opstat var203 automated validation
started 2026-08-16T15:47:01
=== prerequisites ===
host              : kevin-mcdonald-ubu-01
python            : 3.12.3
opstat            : /home/vastdata/git/opstat/opstat
branch            : refactor/tui-performance-local-continuation-wip
HEAD              : 4657b108f941652226fb5cde88874b639aa089ca
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

=== A. NVMe startup + dashboard ===
  started nvme pid=2266877
  Connecting to              0.50s
  Preparing metrics          32.04s
  Gathering initial metrics  64.64s
  dashboard                  78.16s
  startup call durations:
RESULT:nvme.startup.phases          PASS       all three in order, dashboard at 78.16s
RESULT:nvme.footer                  PASS       footer present in dashboard

--- F. Fabric / workload panel (verbatim frame excerpt) ---
  |   Cluster selab-var-203   VMS var203.selab.vastdata.com:443   Refresh 5s   vast-os-release-5.4.6.0
  | ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  | ┌─ BLOCK HEALTH & WORKLOAD ────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Scope  All Volumes                                                                                                   │
  | │ [ IDLE ]   - ops/s   •  ● - ms   ► 0.228 GB/s                                                                        │
  | │ Workload  fabric-overhead dominant / idle data workload                                                              │
  | │ Read      ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Write     ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Reclaim   ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
  | │ Fabric    ██████████████████████  100.0%  of all activity                                                            │
  | │ Sample: 2026-08-16T15:48:03Z (warming up - need 2nd sample)   Mode: latest   Frame: 10m                              │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ┌─ PERFORMANCE INSIGHTS ───────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Top Contributor  FABRIC REQ HANDLE  50.0% of ops                                                                     │
  | │ Highest Latency  FABRIC REQ HANDLE   ● 1.18 ms                                                                       │
  | │ Data Consumer    -                                                                                                   │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ┌─ OPERATIONS ─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  | │ Operation                         IOPS      Throughput      Avg Size         Latency                                 │
  | ├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
  | │ READ                                 -     229.91 MB/s             -               -                                 │
  | │ WRITE                                -       3.10 MB/s             -               -                                 │
  | │ COMPARE & WRITE                      -               -             -               -                                 │
  | │ UNMAP (TRIM)                         -               -             -               -                                 │
  | │ WRITE ZEROES                         -               -             -               -                                 │
  | │ FABRIC DISCOVERY                     -               -             -               -                                 │
  | │ FABRIC REQ HANDLE              1,814.6               -             -         1.18 ms                                 │
  | │ FABRIC XPORT FREE              1,814.6               -             -           79 µs                                 │
  | │ ADMIN GET NS                         -               -             -               -                                 │
  | └──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
  | ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
RESULT:fabric.captured              PASS       31 panel lines captured for manual % verification

=== NVMe CNODE drill (key 'c') ===
  entry wall-clock    : 119.66s  (to panel render)
  entry API calls     : 13  (keypress -> panel rendered)
  monitors created    : 5
  layout              : batch
  rows observed       : ['cnode-3-7', 'cnode-3-8']
    GET /monitors/<id>/query/                      6
    POST /monitors/                                5
    GET /cnodes/                                   1
    DELETE /monitors/<id>/                         1
  in 45s idle          : 6 calls, 6 queries
RESULT:nvme.cnode.manual_refresh    FAIL       no effect
RESULT:nvme.cnode.exit_x            PASS       x returned to the dashboard
RESULT:nvme.cnode.entry             PASS       13 calls, batch layout, 2 rows, 119.66s

=== NVMe VIP drill (key 'i') ===
RESULT:nvme.vip.open                PASS       honest no-telemetry notice rendered (1 creates, 421s)
RESULT:nvme.vip.entry               PASS       93 calls, 1 creates - bounded probe, no fan-out

=== NVMe HOST drill (key 'h') ===
RESULT:nvme.host.open               PASS       honest no-telemetry notice rendered (1 creates, 421s)
RESULT:nvme.host.entry              PASS       98 calls, 1 creates - bounded probe, no fan-out

=== E. Navigation bindings ===
RESULT:nav.legend.i                 PASS       '[i] VIP' in footer
RESULT:nav.legend.x                 PASS       '[x] Exit drill' in footer
RESULT:nav.legend.space             PASS       '[space] Refresh' in footer
RESULT:nav.legend.no_v_vip          PASS       [v] VIP absent
RESULT:nav.legend.no_p_exit         PASS       [p] absent
RESULT:nav.p_does_not_exit          PASS       p left the cNode drill open
RESULT:nav.v_is_not_vip             PASS       v did not open VIP

=== G. NVMe shutdown ===
  shutdown wall-clock : 87.22s
  exit code           : 0
RESULT:nvme.shutdown.frame          PASS       'Cleaning up' shown before the drain
RESULT:nvme.shutdown.exit           PASS       exit=0 in 87.22s

=== nvme cleanup accounting ===
  api log             : /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2266877.log
  total API calls     : 289
  ids created         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2864, 2865, 2866, 2867, 2868, 2869, 2870, 2871, 2872, 2873, 2874, 2875, 2876, 2877, 2878, 2879, 2880, 2881, 2882, 2883]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      244
    POST /monitors/                                20
    DELETE /monitors/<id>/                         20
    GET /cnodes/                                   2
    GET /clusters/                                 1
    GET /vips/                                     1
    GET /blockhosts/                               1

======================================================================
VAR203 AUTOMATED VALIDATION SUMMARY
======================================================================
Host running validation: kevin-mcdonald-ubu-01
Branch: refactor/tui-performance-local-continuation-wip
HEAD: 4657b108f941652226fb5cde88874b639aa089ca
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T15:47:01
End: 2026-08-16T16:09:21

nvme.startup.phases                PASS        all three in order, dashboard at 78.16s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        31 panel lines captured for manual % verification
nvme.cnode.manual_refresh          FAIL        no effect
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 119.66s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.vip.entry                     PASS        93 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (1 creates, 421s)
nvme.host.entry                    PASS        98 calls, 1 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 87.22s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL: nvme.cnode.manual_refresh
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)
#############################################################################################

#############################################################################################
File /tmp/opstat-var203-probe.txt:
var203 continuation-pass probe - 2026-08-15T21:55:22Z
target var203.selab.vastdata.com:443 as admin; time_frame 10m
cluster: selab-var-203 (id 1)

=== batch monitor probe: object_type=cnode ===
  /cnodes/ -> 2 objects (using first 4)
  created monitor 2645 (adhoc_opstat_probe_batch_cnode1786830937)
PROBE:batch.cnode.create PASS ids=[4, 3]
PROBE:batch.cnode.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.cnode.splittable PASS rows_per_object={"4": 120, "3": 120}

=== batch monitor probe: object_type=vip ===
  /vips/ -> 378 objects (using first 4)
  created monitor 2646 (adhoc_opstat_probe_batch_vip_1786830957)
PROBE:batch.vip.create PASS ids=[745, 776, 778, 775]
PROBE:batch.vip.query PASS prop_list=['timestamp', 'object_id', 'TopNMetrics,read_req', 'TopNMetrics,read_latency__avg']
PROBE:batch.vip.splittable FAIL rows_per_object={"745": 0, "776": 0, "778": 0, "775": 0}

=== batch monitor probe: object_type=blockhost ===
  /blockhosts/ -> 6 objects (using first 4)
  created monitor 2647 (adhoc_opstat_probe_batch_blockhost_1786830988)
PROBE:batch.blockhost.create PASS ids=[1, 2, 3, 4]
PROBE:batch.blockhost.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.blockhost.splittable FAIL rows_per_object={"1": 0, "2": 0, "3": 0, "4": 0}

=== rank monitor probe: object_type=cnode ===
  /cnodes/ -> 2 objects (using first 8)
  created monitor 2648 (adhoc_opstat_probe_rank_cnode_1786830999)
PROBE:rank.cnode.accepted PASS scores(read_req d/s)={"4": 1066.056, "3": 0.0}
  NOTE: 2/2 objects yielded a delta; zeros on an idle cluster are expected (run the block loadgen for a rate signal)

=== startup merge-legality probes (cluster scope) ===
  created monitor 2649 (adhoc_opstat_probe_merge_data_pairs_1786831001)
PROBE:merge.data_pairs FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2649/query/ failed: HTTP 400: {"detail":"can't mix properties fro
  deleted monitor 2649
  created monitor 2650 (adhoc_opstat_probe_merge_data_plus_fabric_1786831025)
PROBE:merge.data_plus_fabric FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2650/query/ failed: HTTP 400: {"detail":"can't mix properties fro
  deleted monitor 2650

=== latency unit cross-checks ===
  created monitor 2651 (adhoc_opstat_probe_lat_ref_1786831040)
  reference NFS4Common read_latency__avg (PROVEN us): {'timestamp': '2026-08-15T21:57:23Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0} @ 2026-08-15T21:57:23Z
PROBE:latency.reference PASS values={'timestamp': '2026-08-15T21:57:23Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0}
  created monitor 2652 (adhoc_opstat_probe_lat_block_1786831045)
  BlockMetrics read_latency__avg (unit UNPROVEN): {'timestamp': '2026-08-15T21:57:53Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 825.9} @ 2026-08-15T21:57:53Z
PROBE:latency.blockmetrics PASS values={'timestamp': '2026-08-15T21:57:53Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 825.9}
  host_view latency series (unit UNPROVEN):
    vast_host_view_latency{alias="kmacs-block-ss",bucket="",cluster="selab-var-203",ip="172.200.14.198",path="/kmacs/block",protocol="BLOCK",share="",tenant="default"} 1.1608331664902898
    vast_host_view_latency{alias="",bucket="",cluster="selab-var-203",ip="172.200.14.198",path="/kmacs/smb/opstat",protocol="SMB2",share="opstattest",tenant="default"} 0.9199248732802318
    vast_host_view_latency{alias="",bucket="",cluster="selab-var-203",ip="172.200.14.253",path="/kmacs/smb/opstat",protocol="SMB2",share="opstattest",tenant="default"} 1.6104323834146907
    vast_host_view_latency{alias="",bucket="csnow-db-203",cluster="selab-var-203",ip="172.200.13.190",path="/csnow-db-203",protocol="NDB",share="",tenant="efault"} 0.9710016230002316
    vast_host_view_latency{alias="",bucket="csnow-db-203",cluster="selab-var-203",ip="172.200.13.191",path="/csnow-db-203",protocol="NDB",share="",tenant="default"} 2.5332060717571294
    vast_host_view_latency{alias="",bucket="csnow-db-203",cluster="selab-var-203",ip="172.200.13.192",path="/csnow-db-203",protocol="NDB",share="",tenant="default"} 2.4257868020304567
PROBE:latency.host_view PASS 6 latency series
  INTERPRETATION: same order of magnitude as the reference for the same traffic -> microseconds; ~1000x smaller -> ms; ~1000x larger -> ns.

=== cleanup ===
  deleted monitor 2645
  deleted monitor 2646
  deleted monitor 2647
  deleted monitor 2648
  deleted monitor 2649
  deleted monitor 2650
  deleted monitor 2651
  deleted monitor 2652
PROBE:cleanup.exact_ids PASS all 8 session ids confirmed gone by per-id GET

=== RESULT SUMMARY ===
PROBE:batch.cnode.create PASS ids=[4, 3]
PROBE:batch.cnode.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.cnode.splittable PASS rows_per_object={"4": 120, "3": 120}
PROBE:batch.vip.create PASS ids=[745, 776, 778, 775]
PROBE:batch.vip.query PASS prop_list=['timestamp', 'object_id', 'TopNMetrics,read_req', 'TopNMetrics,read_latency__avg']
PROBE:batch.vip.splittable FAIL rows_per_object={"745": 0, "776": 0, "778": 0, "775": 0}
PROBE:batch.blockhost.create PASS ids=[1, 2, 3, 4]
PROBE:batch.blockhost.query PASS prop_list=['timestamp', 'object_id', 'BlockMetrics,read_req', 'BlockMetrics,read_latency__avg']
PROBE:batch.blockhost.splittable FAIL rows_per_object={"1": 0, "2": 0, "3": 0, "4": 0}
PROBE:rank.cnode.accepted PASS scores(read_req d/s)={"4": 1066.056, "3": 0.0}
PROBE:merge.data_pairs FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2649/query/ failed: HTTP 400: {"detail":"can't mix properties fro
PROBE:merge.data_plus_fabric FAIL rejected: GET https://var203.selab.vastdata.com:443/api/monitors/2650/query/ failed: HTTP 400: {"detail":"can't mix properties fro
PROBE:latency.reference PASS values={'timestamp': '2026-08-15T21:57:23Z', 'object_id': 1, 'ProtoMetrics,proto_name=NFS4Common,read_latency__avg': 0}
PROBE:latency.blockmetrics PASS values={'timestamp': '2026-08-15T21:57:53Z', 'object_id': 1, 'BlockMetrics,read_latency__avg': 825.9}
PROBE:latency.host_view PASS 6 latency series
PROBE:cleanup.exact_ids PASS all 8 session ids confirmed gone by per-id GET
monitors created this run: [2645, 2646, 2647, 2648, 2649, 2650, 2651, 2652]
#############################################################################################

