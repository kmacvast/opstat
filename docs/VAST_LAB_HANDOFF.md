OPSTAT RETURN

#############
It took only 8 seconds this time to start up the app and get a result screen from the SMB path.  Must have been an anomoly last night in the lab environment. 

  VAST SMB opstat v0.1.2   VMS var203.selab.vastdata.com:443   cluster selab-var-203   refresh 5s
  sample 2026-08-17T16:11:24Z   frame 10m   source SMBCommon   vast-os-release-5.4.6.0

┌─ SMB HEALTH & WORKLOAD ──────────────────────────────────────────────────────────────────────────────────────────────┐
│ [ HEALTHY ]   1,776.00 ops/s   ● Lat 944 µs   BW 117.20 MB/s                                                         │
│ Workload  metadata-elevated mixed workload                                                                           │
│ Metadata  ████████████░░░░░░░░░░  56.4%                                                                              │
│ Read      █████░░░░░░░░░░░░░░░░░  21.7%                                                                              │
│ Write     █████░░░░░░░░░░░░░░░░░  21.9%                                                                              │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ PERFORMANCE INSIGHTS ───────────────────────────────────────────────────────────────────────────────────────────────┐
│ Top Contributor  METADATA  50.0% of ops                                                                              │
│ Highest Latency  WRITE   ● 1333 µs                                                                                   │
│ Data Consumer    READ  107.56 MB/s                                                                                   │
│ Metadata Load    1,002.0 ops/s  (56.4% of total)                                                                     │
│ Top Client       172.200.14.253 [default]  md 721.9 ops/s                                                            │
│ Top Share        /kmacs/smb/opstat (opstattest) [default]  md 890.0 ops/s                                            │
│ Observation      metadata-elevated mixed workload                                                                    │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ SMB2 OPCODE WORKFLOW ───────────────────────────────────────────────────────────────────────────────────────────────┐
│ No active SMB opcodes this refresh                                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

│ [q] Quit |[c] cNode |[v] View |[t] Tenant |[x] Exit drill |[space] Refresh                                           │
Cleaning up 1 temporary monitor, please stand by...

real	0m8.686s
user	0m0.134s
sys	0m0.036s
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$


Block only took 12 seconds, both acceptable really. 

  VAST NVMe-oTCP opstat v0.1.2
  Cluster selab-var-203   VMS var203.selab.vastdata.com:443   Refresh 5s   vast-os-release-5.4.6.0
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
┌─ BLOCK HEALTH & WORKLOAD ────────────────────────────────────────────────────────────────────────────────────────────┐
│ Scope  All Volumes                                                                                                   │
│ [ IDLE ]   - ops/s   •  ● - ms   ► 0.380 GB/s                                                                        │
│ Workload  fabric-overhead dominant / idle data workload                                                              │
│ Read      ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
│ Write     ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
│ Reclaim   ░░░░░░░░░░░░░░░░░░░░░░   0.0%                                                                              │
│ Fabric    ██████████████████████  100.0%  of all activity                                                            │
│ Sample: 2026-08-17T16:14:04Z (warming up - need 2nd sample)   Mode: latest   Frame: 10m                              │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ PERFORMANCE INSIGHTS ───────────────────────────────────────────────────────────────────────────────────────────────┐
│ Top Contributor  FABRIC REQ HANDLE  50.0% of ops                                                                     │
│ Highest Latency  FABRIC REQ HANDLE   ● 939 µs                                                                        │
│ Data Consumer    -                                                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ OPERATIONS ─────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ Operation                         IOPS      Throughput      Avg Size         Latency                                 │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ READ                                 -     384.44 MB/s             -               -                                 │
│ WRITE                                -       4.80 MB/s             -               -                                 │
│ COMPARE & WRITE                      -               -             -               -                                 │
│ UNMAP (TRIM)                         -               -             -               -                                 │
│ WRITE ZEROES                         -               -             -               -                                 │
│ FABRIC DISCOVERY                     -               -             -               -                                 │
│ FABRIC REQ HANDLE              1,244.1               -             -          939 µs                                 │
│ FABRIC XPORT FREE              1,244.1               -             -           25 µs                                 │
│ ADMIN GET NS                         -               -             -               -                                 │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  [q] Quit |[c] cNode |[i] VIP |[x] Exit drill |[space] Refresh |[h] Host |[r] Reset stats
Cleaning up 8 temporary monitors, please stand by...

real	0m12.178s
user	0m0.109s
sys	0m0.030s
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$


############################

  VAST SMB opstat v0.1.2   VMS var203.selab.vastdata.com:443   cluster selab-var-203   refresh 5s
  sample 2026-08-17T16:15:44Z   frame 10m   source SMBCommon   vast-os-release-5.4.6.0

┌─ SMB HEALTH & WORKLOAD ──────────────────────────────────────────────────────────────────────────────────────────────┐
│ [ MODERATE LATENCY ]   1,754.00 ops/s   ● Lat 1.32 ms   BW 152.03 MB/s                                               │
│ Workload  metadata-elevated mixed workload                                                                           │
│ Metadata  ████████████░░░░░░░░░░  56.4%                                                                              │
│ Read      █████░░░░░░░░░░░░░░░░░  23.1%                                                                              │
│ Write     █████░░░░░░░░░░░░░░░░░  20.5%                                                                              │
│ Δ  ▼ -21.00 ops/s   ▲ +25.37 MB/s   ▲ Lat +530.4 µs [WRITE]                                                          │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ PERFORMANCE INSIGHTS ───────────────────────────────────────────────────────────────────────────────────────────────┐
│ Top Contributor  METADATA  50.0% of ops                                                                              │
│ Highest Latency  WRITE   ● 2113 µs                                                                                   │
│ Data Consumer    READ  143.02 MB/s                                                                                   │
│ Metadata Load    989.0 ops/s  (56.4% of total)                                                                       │
│ Top Client       172.200.14.253 [default]  md 729.3 ops/s                                                            │
│ Top Share        /kmacs/smb/opstat (opstattest) [default]  md 888.4 ops/s                                            │
│ Top Δ            READ   ▲ +77.00/s                                                                                   │
│ Observation      metadata-elevated mixed workload                                                                    │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

┌─ SMB2 OPCODE WORKFLOW ───────────────────────────────────────────────────────────────────────────────────────────────┐
│ No active SMB opcodes this refresh                                                                                   │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

│ [q] Quit |[c] cNode |[v] View |[t] Tenant |[x] Exit drill |[space] Refresh                                           │
Cleaning up 1 temporary monitor, please stand by...

real	0m35.541s
user	0m0.242s
sys	0m0.057s
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ cd ~/kjmtmp/opstat/
vastdata@kevin-mcdonald-ubu-01:~/kjmtmp/opstat$ ls
opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1344505.log  opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2266877.log  opstat-var203-validation.txt
opstat-api-nvme-tcp-var203.selab.vastdata.com-443-1924558.log  opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3359875.log
opstat-api-nvme-tcp-var203.selab.vastdata.com-443-2044111.log  opstat-api-nvme-tcp-var203.selab.vastdata.com-443-3930933.log
vastdata@kevin-mcdonald-ubu-01:~/kjmtmp/opstat$
LOG=$(ls -t /tmp/opstat-api-smb-*.log | head -1)
echo "LOG=$LOG"
grep -oE "^[0-9-]+ [0-9:]+ (GET|POST|DELETE) [^ ]+ [0-9]+ms" "$LOG" |
sed -E 's#https://[^ ]+/api##'
grep -oE " [0-9]+ms" "$LOG" |
tr -dc '0-9\n' |
awk '{t+=$1} END {printf "Total API time: %.1fs\n", t/1000}'
LOG=/tmp/opstat-api-smb-var203.selab.vastdata.com-443-2340242.log
2026-08-17 16:15:16 GET /clusters/ 497ms
2026-08-17 16:15:16 POST /monitors/ 207ms
2026-08-17 16:15:16 GET /monitors/2943/query/ 159ms
2026-08-17 16:15:16 GET /monitors/2943/query/ 161ms
2026-08-17 16:15:17 GET /monitors/topn/?object_type=view&prop_list=ViewMetrics,read_iops__rate&time_frame=10m&limit=10 298ms
2026-08-17 16:15:17 GET /openfilehandles/?protocol=SMB&page_size=8 123ms
2026-08-17 16:15:22 GET /monitors/2943/query/ 166ms
2026-08-17 16:15:27 GET /monitors/2943/query/ 326ms
2026-08-17 16:15:33 GET /monitors/2943/query/ 157ms
2026-08-17 16:15:38 GET /monitors/2943/query/ 265ms
2026-08-17 16:15:43 GET /monitors/2943/query/ 165ms
2026-08-17 16:15:48 GET /monitors/2943/query/ 254ms
2026-08-17 16:15:49 GET /monitors/topn/?object_type=view&prop_list=ViewMetrics,read_iops__rate&time_frame=10m&limit=10 244ms
2026-08-17 16:15:49 GET /openfilehandles/?protocol=SMB&page_size=8 207ms
2026-08-17 16:15:51 DELETE /monitors/2943/ 354ms
Total API time: 3.6s
vastdata@kevin-mcdonald-ubu-01:~/kjmtmp/opstat$





























