
#########################################################################################################################
Output from work macbook August 16th 9PM

vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ export VAST_PASSWORD=123456
Last login: Sun Aug 16 17:24:42 on ttys000
(venv) kmac@macbook:~$ lab
Welcome to Ubuntu 24.04.4 LTS (GNU/Linux 6.8.0-137-generic x86_64)

 * Documentation:  https://help.ubuntu.com
 * Management:     https://landscape.canonical.com
 * Support:        https://ubuntu.com/pro

 System information as of Thu Aug 13 15:23:56 UTC 2026

  System load:    12.89              Processes:               462
  Usage of /home: 13.8% of 97.87GB   Users logged in:         1
  Memory usage:   5%                 IPv4 address for ens192: 10.143.2.169
  Swap usage:     0%

  => There is 1 zombie process.

 * Strictly confined Kubernetes makes edge and IoT secure. Learn how MicroK8s
   just raised the bar for easy, resilient and secure K8s cluster deployment.

   https://ubuntu.com/engage/secure-kubernetes-at-the-edge
  total API calls     : 140
  ids created         : [2884, 2885, 2886, 2887, 2888, 2889, 2890, 2891, 2892, 2893, 2894, 2895, 2896, 2897, 2898, 2899, 2900, 2901, 2902, 2903]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2884, 2885, 2886, 2887, 2888, 2889, 2890, 2891, 2892, 2893, 2894, 2895, 2896, 2897, 2898, 2899, 2900, 2901, 2902, 2903]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      95
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
Branch: main
HEAD: 1aaa35965e0f5bc458298fddfd1def1a35927b8a
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T23:31:09
End: 2026-08-16T23:39:18

nvme.startup.phases                PASS        all three in order, dashboard at 58.58s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        17 panel lines captured for manual % verification
nvme.cnode.manual_refresh          PASS        forced: query issued -0.9s after the keypress and 1.2s after the previous headline burst began - inside the 15s cadence/throttle window no scheduled poll can enter
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 42.55s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 21s)
nvme.vip.entry                     PASS        4 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (0 creates, 29s)
nvme.host.entry                    PASS        2 calls, 0 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 28.34s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.manual_refresh, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL:
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)

wrote /tmp/opstat-var203-validation.txt
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ f
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ tail -60 /tmp/opstat-var203-validation.txt
RESULT:nvme.shutdown.frame          PASS       'Cleaning up' shown before the drain
RESULT:nvme.shutdown.exit           PASS       exit=0 in 28.34s

=== nvme cleanup accounting ===
  api log             : /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1924558.log
  total API calls     : 140
  ids created         : [2884, 2885, 2886, 2887, 2888, 2889, 2890, 2891, 2892, 2893, 2894, 2895, 2896, 2897, 2898, 2899, 2900, 2901, 2902, 2903]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2884, 2885, 2886, 2887, 2888, 2889, 2890, 2891, 2892, 2893, 2894, 2895, 2896, 2897, 2898, 2899, 2900, 2901, 2902, 2903]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      95
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
Branch: main
HEAD: 1aaa35965e0f5bc458298fddfd1def1a35927b8a
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T23:31:09
End: 2026-08-16T23:39:18

nvme.startup.phases                PASS        all three in order, dashboard at 58.58s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        17 panel lines captured for manual % verification
nvme.cnode.manual_refresh          PASS        forced: query issued -0.9s after the keypress and 1.2s after the previous headline burst began - inside the 15s cadence/throttle window no scheduled poll can enter
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 42.55s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 21s)
nvme.vip.entry                     PASS        4 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (0 creates, 29s)
nvme.host.entry                    PASS        2 calls, 0 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 28.34s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.manual_refresh, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL:
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$

vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ ls -t /tmp/opstat-api-nvme-tcp-*.log | head -1
/tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1924558.log
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$



vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ tail -60 /tmp/opstat-var203-validation.txt

ls -t /tmp/opstat-api-nvme-tcp-*.log | head -1

rm -rf ~/kjmtmp/opstat

mkdir -p ~/kjmtmp/opstat

cp -rp /tmp/opstat-api-nvme-tcp-*.log ~/kjmtmp/opstat
cp /tmp/opstat-var203-validation.txt ~/kjmtmp/opstat

zip -j ~/opstat-files-202608151840.zip ~/kjmtmp/opstat/*
RESULT:nvme.shutdown.frame          PASS       'Cleaning up' shown before the drain
RESULT:nvme.shutdown.exit           PASS       exit=0 in 28.34s

=== nvme cleanup accounting ===
  api log             : /tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1924558.log
  total API calls     : 140
  ids created         : [2884, 2885, 2886, 2887, 2888, 2889, 2890, 2891, 2892, 2893, 2894, 2895, 2896, 2897, 2898, 2899, 2900, 2901, 2902, 2903]
  session creates     : 20  (round 4 measured 206 on this cluster; the bounded design predicts ~20, but measure, don't assume)
  ids deleted         : [2884, 2885, 2886, 2887, 2888, 2889, 2890, 2891, 2892, 2893, 2894, 2895, 2896, 2897, 2898, 2899, 2900, 2901, 2902, 2903]
  ids still present   : NONE
RESULT:nvme.cleanup                 PASS       all 20 session monitors deleted (per-id GET, 404=gone)
  whole-session call breakdown:
    GET /monitors/<id>/query/                      95
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
Branch: main
HEAD: 1aaa35965e0f5bc458298fddfd1def1a35927b8a
Target VMS: var203.selab.vastdata.com
Start: 2026-08-16T23:31:09
End: 2026-08-16T23:39:18

nvme.startup.phases                PASS        all three in order, dashboard at 58.58s
nvme.footer                        PASS        footer present in dashboard
fabric.captured                    PASS        17 panel lines captured for manual % verification
nvme.cnode.manual_refresh          PASS        forced: query issued -0.9s after the keypress and 1.2s after the previous headline burst began - inside the 15s cadence/throttle window no scheduled poll can enter
nvme.cnode.exit_x                  PASS        x returned to the dashboard
nvme.cnode.entry                   PASS        13 calls, batch layout, 2 rows, 42.55s
nvme.vip.open                      PASS        honest no-telemetry notice rendered (1 creates, 21s)
nvme.vip.entry                     PASS        4 calls, 1 creates - bounded probe, no fan-out
nvme.host.open                     PASS        honest no-telemetry notice rendered (0 creates, 29s)
nvme.host.entry                    PASS        2 calls, 0 creates - bounded probe, no fan-out
nav.legend.i                       PASS        '[i] VIP' in footer
nav.legend.x                       PASS        '[x] Exit drill' in footer
nav.legend.space                   PASS        '[space] Refresh' in footer
nav.legend.no_v_vip                PASS        [v] VIP absent
nav.legend.no_p_exit               PASS        [p] absent
nav.p_does_not_exit                PASS        p left the cNode drill open
nav.v_is_not_vip                   PASS        v did not open VIP
nvme.shutdown.frame                PASS        'Cleaning up' shown before the drain
nvme.shutdown.exit                 PASS        exit=0 in 28.34s
nvme.cleanup                       PASS        all 20 session monitors deleted (per-id GET, 404=gone)

PASS: nvme.startup.phases, nvme.footer, fabric.captured, nvme.cnode.manual_refresh, nvme.cnode.exit_x, nvme.cnode.entry, nvme.vip.open, nvme.vip.entry, nvme.host.open, nvme.host.entry, nav.legend.i, nav.legend.x, nav.legend.space, nav.legend.no_v_vip, nav.legend.no_p_exit, nav.p_does_not_exit, nav.v_is_not_vip, nvme.shutdown.frame, nvme.shutdown.exit, nvme.cleanup
FAIL:
UNVERIFIED:

Wall-clock is only meaningful when this ran near the cluster.
FILES TO RETURN:
  /tmp/opstat-var203-validation.txt
  /tmp/opstat-var203-probe.txt
  /tmp/opstat-api-*.log  (the pid-scoped logs referenced above)
/tmp/opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1924558.log
  adding: opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1344505.log (deflated 84%)
  adding: opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1924558.log (deflated 86%)
  adding: opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2044111.log (deflated 85%)
  adding: opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2266877.log (deflated 83%)
  adding: opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3359875.log (deflated 88%)
  adding: opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3930933.log (deflated 88%)
  adding: opstat-var203-validation.txt (deflated 74%)
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$

