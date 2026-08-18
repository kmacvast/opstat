vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ git fetch origin
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ bash scripts/opstat-lab-fr2-delegation-discovery.sh

== 1. repository state ==================================================
Already on 'main'
Your branch is up to date with 'origin/main'.
Already up to date.
[19:45:12] PASS    : main @ fbcc5839dd8c4acac9502f3e5e8008f116d8dd12 (matches origin/main)

== 2. working tree ==================================================
[19:45:12] PASS    : working tree clean

== 3. credentials and NFSv4.1 environment ==================================================
[19:45:12] PASS    : credential: VAST_PASSWORD present
[19:45:12] PASS    : nfs41-loadgen active
[19:45:12] PASS    : mount at /mnt/nfs41test is NFS vers=4.1
[19:45:13] PASS    : mount is readable

== 4. mount derivation and real file candidates (client-side truth) ==================================================
[19:45:13] PASS    : mount: server 172.200.204.6 export /kmacs/nfstest on /mnt/nfs41test (API target var204.selab.vastdata.com)
/mnt/nfs41test/delegation_test_file.txt
/mnt/nfs41test/nfs41_loadgen/attr_stress.txt
/mnt/nfs41test/nfs41_loadgen/fio_bw.bin
/mnt/nfs41test/nfs41_loadgen/fio_iops.bin
/mnt/nfs41test/nfs41_loadgen/fio_locks.bin
/mnt/nfs41test/nfs41_loadgen/lock_stress.dat
/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_3/file_19
/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_3/file_2
export path     : /kmacs/nfstest
candidate rc    : 0
client files    : /mnt/nfs41test/delegation_test_file.txt,/mnt/nfs41test/nfs41_loadgen/attr_stress.txt,/mnt/nfs41test/nfs41_loadgen/fio_bw.bin,/mnt/nfs41test/nfs41_loadgen/fio_iops.bin,/mnt/nfs41test/nfs41_loadgen/fio_locks.bin,/mnt/nfs41test/nfs41_loadgen/lock_stress.dat,/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_3/file_19,/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_3/file_2
hostname : kevin-mcdonald-ubu-01
collected: 2026-08-18 19:45:49 UTC
HEAD     : fbcc5839dd8c4acac9502f3e5e8008f116d8dd12
python   : Python 3.12.3
target   : var204.selab.vastdata.com
run dir  : /home/vastdata/kjmtmp/opstat/fr2-var204-20260818-194511
PROBE-START 2026-08-18 19:45:49

== 5. delegation endpoint discovery (GET-only) ==================================================
api log: /home/vastdata/kjmtmp/opstat/fr2-var204-20260818-194511/raw/opstat-api-fr2-delegations-var204.selab.vastdata.com-443-962435.log
fr2 delegation discovery - 2026-08-18T19:45:49Z
cluster: selab-var-204 (5.5.0.1.12648440953753313426)
PROBE:preflight.mount_matches_vms PASS mount server 172.200.204.6 is a VIP of var204.selab.vastdata.com
  evidence: view-candidates.json (289 bytes)
PROBE:correlation.views PASS 2 candidate view(s) for /kmacs/nfstest/delegation_test_file.txt; top: [(755, '/kmacs/nfstest', 'prefix'), (1, '/', 'prefix')]
PROBE:correlation.tenant PASS namespace tenant candidates (derived, ordered): [(1, 'default', 'prefix view id 755 path /kmacs/nfstest')]
  evidence: file-mapping.txt (863 bytes)
  file mapping:
    client /mnt/nfs41test/delegation_test_file.txt -> server /kmacs/nfstest/delegation_test_file.txt
    client /mnt/nfs41test/nfs41_loadgen/attr_stress.txt -> server /kmacs/nfstest/nfs41_loadgen/attr_stress.txt
    client /mnt/nfs41test/nfs41_loadgen/fio_bw.bin -> server /kmacs/nfstest/nfs41_loadgen/fio_bw.bin
    client /mnt/nfs41test/nfs41_loadgen/fio_iops.bin -> server /kmacs/nfstest/nfs41_loadgen/fio_iops.bin
    client /mnt/nfs41test/nfs41_loadgen/fio_locks.bin -> server /kmacs/nfstest/nfs41_loadgen/fio_locks.bin
    client /mnt/nfs41test/nfs41_loadgen/lock_stress.dat -> server /kmacs/nfstest/nfs41_loadgen/lock_stress.dat
    client /mnt/nfs41test/nfs41_loadgen/meta_stress/dir_3/file_19 -> server /kmacs/nfstest/nfs41_loadgen/meta_stress/dir_3/file_19
    client /mnt/nfs41test/nfs41_loadgen/meta_stress/dir_3/file_2 -> server /kmacs/nfstest/nfs41_loadgen/meta_stress/dir_3/file_2
  evidence: deleg-availability-t1.txt (139 bytes)
PROBE:deleg.availability PASS tenant default (no file_path) [1788ms] -> GET https://var204.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/ failed: HTTP 400: {"detail":"['__root__->file_path: field required']"}
  evidence: deleg-d008_viewpath-t1.json (143 bytes)
PROBE:deleg.d008_viewpath PASS tenant default /kmacs/nfstest [257ms] -> 0 record(s), count_total=0, record fields=none, wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
  path representations to try (derived, bounded): ['/kmacs/nfstest/delegation_test_file.txt', '/delegation_test_file.txt']
  evidence: deleg-try0-t1.json (143 bytes)
PROBE:deleg.try0 PASS tenant default /kmacs/nfstest/delegation_test_file.txt [261ms] -> 0 record(s), count_total=0, record fields=none, wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:correlation.winner PASS tenant 1 (default) accepts full-namespace syntax: /kmacs/nfstest/delegation_test_file.txt
  evidence: deleg-file1-t1.json (367 bytes)
PROBE:deleg.file1 PASS tenant default /kmacs/nfstest/nfs41_loadgen/attr_stress.txt [223ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
    RECORD: {"client_id": 107889578475529, "delegation_client_ip": "172.200.14.198", "delegation_stateid": 107923938216949, "delegation_type": "WRITE", "revoke_in_progress": false, "vip_addr": "172.200.204.6"}
  evidence: deleg-file2-t1.json (367 bytes)
PROBE:deleg.file2 PASS tenant default /kmacs/nfstest/nfs41_loadgen/fio_bw.bin [709ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
    RECORD: {"client_id": 107889578475529, "delegation_client_ip": "172.200.14.198", "delegation_stateid": 107923938217152, "delegation_type": "WRITE", "revoke_in_progress": false, "vip_addr": "172.200.204.6"}
  evidence: deleg-file3-t1.json (367 bytes)
PROBE:deleg.file3 PASS tenant default /kmacs/nfstest/nfs41_loadgen/fio_iops.bin [220ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
    RECORD: {"client_id": 107889578475529, "delegation_client_ip": "172.200.14.198", "delegation_stateid": 107923938217032, "delegation_type": "WRITE", "revoke_in_progress": false, "vip_addr": "172.200.204.6"}
  evidence: deleg-file4-t1.json (367 bytes)
PROBE:deleg.file4 PASS tenant default /kmacs/nfstest/nfs41_loadgen/fio_locks.bin [220ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
    RECORD: {"client_id": 107889578475529, "delegation_client_ip": "172.200.14.198", "delegation_stateid": 107923938216963, "delegation_type": "WRITE", "revoke_in_progress": false, "vip_addr": "172.200.204.6"}
  evidence: deleg-file5-t1.json (367 bytes)
PROBE:deleg.file5 PASS tenant default /kmacs/nfstest/nfs41_loadgen/lock_stress.dat [217ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
    RECORD: {"client_id": 107889578475529, "delegation_client_ip": "172.200.14.198", "delegation_stateid": 107923938216948, "delegation_type": "WRITE", "revoke_in_progress": false, "vip_addr": "172.200.204.6"}
  evidence: deleg-dir-t1.json (143 bytes)
PROBE:deleg.dir PASS tenant default /kmacs/nfstest/nfs41_loadgen [224ms] -> 0 record(s), count_total=0, record fields=none, wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
  evidence: deleg-missing-t1.txt (228 bytes)
PROBE:deleg.missing FAIL tenant default /kmacs/nfstest/does-not-exist-opstat-fr2 [221ms] -> GET https://var204.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fdoes-not-exist-opstat-fr2 failed: HTTP 4

OBSERVED RECORD FIELDS (union): {
 "client_id": "int",
 "delegation_client_ip": "str",
 "delegation_stateid": "int",
 "delegation_type": "str",
 "revoke_in_progress": "bool",
 "vip_addr": "str"
}
  evidence: record-fields.json (163 bytes)

=== RESULT SUMMARY ===
PROBE:preflight.mount_matches_vms PASS mount server 172.200.204.6 is a VIP of var204.selab.vastdata.com
PROBE:correlation.views PASS 2 candidate view(s) for /kmacs/nfstest/delegation_test_file.txt; top: [(755, '/kmacs/nfstest', 'prefix'), (1, '/', 'prefix')]
PROBE:correlation.tenant PASS namespace tenant candidates (derived, ordered): [(1, 'default', 'prefix view id 755 path /kmacs/nfstest')]
PROBE:deleg.availability PASS tenant default (no file_path) [1788ms] -> GET https://var204.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/ failed: HTTP 400: {"detail":"['__root__->file_path: field required']"}
PROBE:deleg.d008_viewpath PASS tenant default /kmacs/nfstest [257ms] -> 0 record(s), count_total=0, record fields=none, wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.try0 PASS tenant default /kmacs/nfstest/delegation_test_file.txt [261ms] -> 0 record(s), count_total=0, record fields=none, wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:correlation.winner PASS tenant 1 (default) accepts full-namespace syntax: /kmacs/nfstest/delegation_test_file.txt
PROBE:deleg.file1 PASS tenant default /kmacs/nfstest/nfs41_loadgen/attr_stress.txt [223ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.file2 PASS tenant default /kmacs/nfstest/nfs41_loadgen/fio_bw.bin [709ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.file3 PASS tenant default /kmacs/nfstest/nfs41_loadgen/fio_iops.bin [220ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.file4 PASS tenant default /kmacs/nfstest/nfs41_loadgen/fio_locks.bin [220ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.file5 PASS tenant default /kmacs/nfstest/nfs41_loadgen/lock_stress.dat [217ms] -> 1 record(s), count_total=1, record fields=['client_id', 'delegation_client_ip', 'delegation_stateid', 'delegation_type', 'revoke_in_progress', 'vip_addr'], wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.dir PASS tenant default /kmacs/nfstest/nfs41_loadgen [224ms] -> 0 record(s), count_total=0, record fields=none, wrapper extras=['xeystore_pagination', 'xeystore_pagination_next_client_id']
PROBE:deleg.missing FAIL tenant default /kmacs/nfstest/does-not-exist-opstat-fr2 [221ms] -> GET https://var204.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fdoes-not-exist-opstat-fr2 failed: HTTP 4
evidence directory: /home/vastdata/kjmtmp/opstat/fr2-var204-20260818-194511/raw
SAFETY: this probe issues GET requests only; the API log must contain zero non-GET lines.
PROBE-RC 0
PROBE-END 2026-08-18 19:45:55
[19:45:55] PASS    : probe rc=0

== 6. read-only verification (API log) ==================================================
[19:45:55] PASS    : API log inside the run tree: /home/vastdata/kjmtmp/opstat/fr2-var204-20260818-194511/raw/opstat-api-fr2-delegations-var204.selab.vastdata.com-443-962435.log
[19:45:55] PASS    : API log contains ZERO non-GET requests (D-008 honored)

== 7. post-run state and /tmp policy ==================================================
[19:45:55] PASS    : no new opstat artifacts in /tmp

== 8. minimum success check ==================================================
[19:45:55] PASS    : minimum success: a real existing file returned an HTTP-success nfs4_delegs response

== final packaging and verdict ==================================================
opstat FR2 delegation discovery - 20260818-194511
HEAD fbcc5839dd8c4acac9502f3e5e8008f116d8dd12  target var204.selab.vastdata.com  probe rc 0  script failures 0

candidates.txt      : candidate discovery outcome
candidate-files.txt : raw candidate list (helper output)
probe-output.txt    : full PROBE: verdicts incl. observed record fields
raw/                : verbatim endpoint responses + GET-only API log
logs/               : mounts, mountstats, loadgen status, tmp diff

file inventory:
  MANIFEST.txt
  candidate-files.txt
  candidates.txt
  git-final-state.txt
  logs/mount-listing.txt
  logs/mounts.txt
  logs/mountstats-after.txt
  logs/mountstats-before.txt
  logs/nfs41-loadgen-status.txt
  logs/tmp-after.txt
  logs/tmp-before.txt
  prereqs.txt
  probe-output.txt
  raw/deleg-availability-t1.txt
  raw/deleg-d008_viewpath-t1.json
  raw/deleg-dir-t1.json
  raw/deleg-file1-t1.json
  raw/deleg-file2-t1.json
  raw/deleg-file3-t1.json
  raw/deleg-file4-t1.json
  raw/deleg-file5-t1.json
  raw/deleg-missing-t1.txt
  raw/deleg-try0-t1.json
  raw/file-mapping.txt
  raw/opstat-api-fr2-delegations-var204.selab.vastdata.com-443-962435.log
  raw/record-fields.json
  raw/view-candidates.json
  timestamps.txt
[19:45:55] PASS    : ZIP integrity verified

      906  2026-08-18 19:45   fr2-var204-20260818-194511/logs/mount-listing.txt
      862  2026-08-18 19:45   fr2-var204-20260818-194511/logs/mounts.txt
        0  2026-08-18 19:45   fr2-var204-20260818-194511/logs/tmp-before.txt
        0  2026-08-18 19:45   fr2-var204-20260818-194511/logs/tmp-after.txt
      435  2026-08-18 19:45   fr2-var204-20260818-194511/candidates.txt
      249  2026-08-18 19:45   fr2-var204-20260818-194511/prereqs.txt
---------                     -------
   115578                     32 files

-rw-rw-r-- 1 vastdata vastdata 32K Aug 18 19:45 /home/vastdata/opstat-fr2-delegation-discovery-var204-20260818-194511.zip
3d8e2288261a32c6138b95ec4fb97c9c954ce0c2170527b786499e841cb01335  /home/vastdata/opstat-fr2-delegation-discovery-var204-20260818-194511.zip

======================================================================
RESULT: RUN VALID - return this ONE file:

    /home/vastdata/opstat-fr2-delegation-discovery-var204-20260818-194511.zip
======================================================================
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$
