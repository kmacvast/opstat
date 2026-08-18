  VAST NFSv41 opstat v0.1.2   VMS var204.selab.vastdata.com:443   cluster selab-var-204   refresh 5s   | DELEGATION
  sample 2026-08-18T20:51:51Z   frame 10m   source NFS4Common + NfsMetrics   sort default

┌─ NFSv4.1 DELEGATION LOOKUP ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ /kmacs/nfstest                                                                                                                                       │
│ No active NFSv4.1 delegation exists for this path.                                                                                                   │
│ The path is valid; no client currently holds a delegation on it.                                                                                     │
│ If this path is a directory, delegations held on files inside it are not reported; query the file itself.                                            │
│ queried 20:52:01  ·  [space] Re-query   [d] New path   [x] Back                                                                                      │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────┘

│ [q] Quit |[o] Ops |[l] Lat |[n] Name |[c] cNode |[v] View |[t] Tenant |[x] Exit drill |[space] Refresh |[4] Native v4 |[h] v4 hosts |[d] Delegation  │
CHECK:cost.space_requeries_once PASS space issued 1 delegation GET(s), 0 other call(s)
CHECK:deleg.x_exits PASS x dismissed the result
CHECK:cost.refresh_zero_deleg PASS 0 delegation call(s) across 3 poll ticks (expected 0)
CHECK:deleg.exit.returns PASS dashboard restored after exit
CHECK:safety.get_only PASS every nfs4_delegs call in the API log is a GET (5 total)
Cleaning up 1 temporary monitor, please stand by...
CHECK:cleanup.exact_ids PASS all 1 session monitors confirmed gone by per-id GET
frames saved: /home/vastdata/kjmtmp/opstat/fr2val-var204-20260818-205116/frames.txt

=== RESULT SUMMARY ===
CHECK:preflight.mount_vms_consistent   PASS  mount server 172.200.204.6 found in var204.selab.vastdata.com VIP inventory
CHECK:nfs41.dashboard.footer           PASS  footer present on the dashboard
CHECK:nfs41.footer.advertises_d        PASS  the [d] Delegation control is discoverable
CHECK:prompt.opens                     PASS  [d] opened the path prompt
CHECK:prompt.frame.footer              PASS  footer survives the prompt
CHECK:prompt.q_is_text                 PASS  typing q inside the prompt neither quits nor navigates
CHECK:prompt.cancel                    PASS  backspace-on-empty cancelled the prompt
CHECK:deleg.live.records               PASS  live delegation records for /kmacs/nfstest/nfs41_loadgen/attr_stress.txt
CHECK:deleg.live.six_fields            PASS  all proven fields populated
CHECK:deleg.live.tenant_recorded       PASS  answered by tenant 'default'
CHECK:deleg.live.frame                 PASS  primary fields plus the dim id line rendered
CHECK:deleg.live.frame.footer          PASS  footer survives the live result
CHECK:cost.lookup_bounded              PASS  1 /views/ fetch(es), 1 delegation GET(s) across 1 lookup(s) (budget: 1 views + 2 GETs per lookup)
CHECK:deleg.valid_root.answers         PASS  state='empty' for the export root (valid path)
CHECK:deleg.empty.honest               PASS  empty is information, not an error
CHECK:deleg.invalid.state              PASS  state='invalid' for a nonexistent path
CHECK:deleg.invalid.frame.footer       PASS  footer survives the invalid state
CHECK:cost.views_cached                PASS  repeat lookup fetched /views/ 0 time(s) (expected 0)
CHECK:cost.space_requeries_once        PASS  space issued 1 delegation GET(s), 0 other call(s)
CHECK:deleg.x_exits                    PASS  x dismissed the result
CHECK:cost.refresh_zero_deleg          PASS  0 delegation call(s) across 3 poll ticks (expected 0)
CHECK:deleg.exit.returns               PASS  dashboard restored after exit
CHECK:safety.get_only                  PASS  every nfs4_delegs call in the API log is a GET (5 total)
CHECK:cleanup.exact_ids                PASS  all 1 session monitors confirmed gone by per-id GET
RESULT: PASS
NOTE: keystroke capture is unit-test covered; every key above flowed through the production _dispatch_key path in-process.
VALIDATOR-RC 0
VALIDATION-END 2026-08-18 20:52:03
[20:52:03] PASS    : validator rc=0

== 6. read-only verification (API log) ==================================================
[20:52:03] PASS    : API log inside the run tree: /home/vastdata/kjmtmp/opstat/fr2val-var204-20260818-205116/raw/opstat-api-nfs-v41-var204.selab.vastdata.com-443-1896935.log
[20:52:03] PASS    : API log contains ZERO non-GET delegation requests (D-008 honored)

== 7. post-run state and /tmp policy ==================================================
[20:52:03] PASS    : no new opstat artifacts in /tmp

== 8. minimum success check ==================================================
[20:52:03] PASS    : minimum success: a REAL workload file returned live delegation records and every production check passed

== final packaging and verdict ==================================================
opstat FR2 delegation-diagnostic validation - 20260818-205116
HEAD 5fd69092ff0e91883f37e54f8a27b59d5d972333  target var204.selab.vastdata.com  validator rc 0  script failures 0

candidates.txt      : candidate discovery outcome
candidate-files.txt : raw candidate list (helper output)
validator-output.txt: full CHECK: verdicts from the production run
frames.txt          : captured production frames (dashboard, prompt,
                      live, empty, invalid, after-exit)
raw/                : GET-only API log (verbatim requests/responses)
logs/               : mounts, mountstats, loadgen status, tmp diff

file inventory:
  MANIFEST.txt
  candidate-files.txt
  candidates.txt
  frames.txt
  git-final-state.txt
  logs/mount-listing.txt
  logs/mounts.txt
  logs/mountstats-after.txt
  logs/mountstats-before.txt
  logs/nfs41-loadgen-status.txt
  logs/tmp-after.txt
  logs/tmp-before.txt
  prereqs.txt
  raw/opstat-api-nfs-v41-var204.selab.vastdata.com-443-1896935.log
  timestamps.txt
  validator-output.txt
[20:52:03] PASS    : ZIP integrity verified

      906  2026-08-18 20:51   fr2val-var204-20260818-205116/logs/mount-listing.txt
      862  2026-08-18 20:51   fr2val-var204-20260818-205116/logs/mounts.txt
        0  2026-08-18 20:51   fr2val-var204-20260818-205116/logs/tmp-before.txt
        0  2026-08-18 20:52   fr2val-var204-20260818-205116/logs/tmp-after.txt
      436  2026-08-18 20:51   fr2val-var204-20260818-205116/candidates.txt
      252  2026-08-18 20:51   fr2val-var204-20260818-205116/prereqs.txt
---------                     -------
   348407                     20 files

-rw-rw-r-- 1 vastdata vastdata 56K Aug 18 20:52 /home/vastdata/opstat-fr2-delegation-validation-var204-20260818-205116.zip
8b8aa1ec9fc25e518ff97ce2197e47c9fc89614d879d6e970dc7918dd45737db  /home/vastdata/opstat-fr2-delegation-validation-var204-20260818-205116.zip

======================================================================
RESULT: RUN VALID - return this ONE file:

    /home/vastdata/opstat-fr2-delegation-validation-var204-20260818-205116.zip
======================================================================
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
