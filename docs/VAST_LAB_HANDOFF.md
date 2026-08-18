If you want to make scripts/opstat-lab-fr2-delegation-discovery.sh fail immediately with a useful message, open the script and update Section 3 where it checks for nfs41-loadgen:

# Locate the loadgen check in Section 3 and replace the warning block with:
if ! pgrep -f "nfs41-loadgen" >/dev/null 2>&1; then
    echo "[!] ERROR: nfs41-loadgen is not running. Active NFSv4.1 state is required."
    echo "[!] Run 'sudo systemctl start nfs41-loadgen' before running this probe."
    exit 1
fi

This stops the probe on line one instead of making you guess why the VAST API rejected the zero-state queries.

vastdata@kevin-mcdonald-ubu-01:~/git/opstat$ bash scripts/opstat-lab-fr2-delegation-discovery.sh

== 1. repository state ==================================================
Already on 'main'
Your branch is up to date with 'origin/main'.
Already up to date.
[19:27:50] PASS    : main @ 4463499a90e15a799961cbeb1ff17d49a289a7c7 (matches origin/main)

== 2. working tree ==================================================
[19:27:50] PASS    : working tree clean

== 3. credentials and NFSv4.1 environment ==================================================
[19:27:50] PASS    : credential: VAST_PASSWORD present
[19:27:50] PASS    : nfs41-loadgen active (open files give the best delegation odds)
[19:27:50] PASS    : NFSv4.1 mount present: /mnt/nfs41test

== 4. mount derivation and real file candidates (client-side truth) ==================================================
[19:27:50] PASS    : mount: server export /kmacs/nfstest on /mnt/nfs41test
/mnt/nfs41test/nfs41_loadgen/attr_stress.txt
/mnt/nfs41test/delegation_test_file.txt
/mnt/nfs41test/nfs41_loadgen/fio_bw.bin
/mnt/nfs41test/nfs41_loadgen/fio_iops.bin
/mnt/nfs41test/nfs41_loadgen/fio_locks.bin
/mnt/nfs41test/nfs41_loadgen/lock_stress.dat
/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_1/file_11
/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_1/file_14
export path     : /kmacs/nfstest
candidate rc    : 0
client files    : /mnt/nfs41test/nfs41_loadgen/attr_stress.txt,/mnt/nfs41test/delegation_test_file.txt,/mnt/nfs41test/nfs41_loadgen/fio_bw.bin,/mnt/nfs41test/nfs41_loadgen/fio_iops.bin,/mnt/nfs41test/nfs41_loadgen/fio_locks.bin,/mnt/nfs41test/nfs41_loadgen/lock_stress.dat,/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_1/file_11,/mnt/nfs41test/nfs41_loadgen/meta_stress/dir_1/file_14
hostname : kevin-mcdonald-ubu-01
collected: 2026-08-18 19:28:32 UTC
HEAD     : 4463499a90e15a799961cbeb1ff17d49a289a7c7
python   : Python 3.12.3
target   : var203.selab.vastdata.com
run dir  : /home/vastdata/kjmtmp/opstat/fr2-20260818-192749
PROBE-START 2026-08-18 19:28:32

== 5. delegation endpoint discovery (GET-only) ==================================================
api log: /home/vastdata/kjmtmp/opstat/fr2-20260818-192749/raw/opstat-api-fr2-delegations-var203.selab.vastdata.com-443-710499.log
fr2 delegation discovery - 2026-08-18T19:28:32Z
cluster: selab-var-203 (5.4.6.0.17320657730101426841)
  evidence: view-candidates.json (430 bytes)
PROBE:correlation.views PASS 3 candidate view(s) for /kmacs/nfstest/nfs41_loadgen/attr_stress.txt; top: [(320, '/kmacs/nfstest', 'prefix'), (1, '/', 'prefix'), (217, '/', 'prefix')]
PROBE:correlation.tenant PASS namespace tenant candidates (derived, ordered): [(1, 'default', 'prefix view id 320 path /kmacs/nfstest'), (7, 'nireny', 'prefix view id 217 path /')]
  evidence: file-mapping.txt (865 bytes)
  file mapping:
    client /mnt/nfs41test/nfs41_loadgen/attr_stress.txt -> server /kmacs/nfstest/nfs41_loadgen/attr_stress.txt
    client /mnt/nfs41test/delegation_test_file.txt -> server /kmacs/nfstest/delegation_test_file.txt
    client /mnt/nfs41test/nfs41_loadgen/fio_bw.bin -> server /kmacs/nfstest/nfs41_loadgen/fio_bw.bin
    client /mnt/nfs41test/nfs41_loadgen/fio_iops.bin -> server /kmacs/nfstest/nfs41_loadgen/fio_iops.bin
    client /mnt/nfs41test/nfs41_loadgen/fio_locks.bin -> server /kmacs/nfstest/nfs41_loadgen/fio_locks.bin
    client /mnt/nfs41test/nfs41_loadgen/lock_stress.dat -> server /kmacs/nfstest/nfs41_loadgen/lock_stress.dat
    client /mnt/nfs41test/nfs41_loadgen/meta_stress/dir_1/file_11 -> server /kmacs/nfstest/nfs41_loadgen/meta_stress/dir_1/file_11
    client /mnt/nfs41test/nfs41_loadgen/meta_stress/dir_1/file_14 -> server /kmacs/nfstest/nfs41_loadgen/meta_stress/dir_1/file_14
  evidence: deleg-availability-t1.txt (139 bytes)
PROBE:deleg.availability PASS tenant default (no file_path) [111ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/ failed: HTTP 400: {"detail":"['__root__->file_path: field required']"}
  path representations to try (derived, bounded): ['/kmacs/nfstest/nfs41_loadgen/attr_stress.txt', '/nfs41_loadgen/attr_stress.txt']
  evidence: deleg-try0-t1.txt (234 bytes)
PROBE:deleg.try0 FAIL tenant default /kmacs/nfstest/nfs41_loadgen/attr_stress.txt [215ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fnfs41_loadgen%2Fattr_stress.txt failed:
  evidence: deleg-try1-t1.txt (216 bytes)
PROBE:deleg.try1 FAIL tenant default /nfs41_loadgen/attr_stress.txt [209ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/?file_path=%2Fnfs41_loadgen%2Fattr_stress.txt failed: HTTP 400: {"detail
  evidence: deleg-try2-t7.txt (234 bytes)
PROBE:deleg.try2 FAIL tenant nireny /kmacs/nfstest/nfs41_loadgen/attr_stress.txt [214ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/7/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fnfs41_loadgen%2Fattr_stress.txt failed:
  evidence: deleg-try3-t7.txt (216 bytes)
PROBE:deleg.try3 FAIL tenant nireny /nfs41_loadgen/attr_stress.txt [434ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/7/nfs4_delegs/?file_path=%2Fnfs41_loadgen%2Fattr_stress.txt failed: HTTP 400: {"detail
PROBE:correlation.winner FAIL no (tenant, syntax) pair produced an HTTP success for a real existing file - every attempt recorded verbatim

No delegation records observed; field names remain unproven by this run (shape/empty evidence still captured).

=== RESULT SUMMARY ===
PROBE:correlation.views PASS 3 candidate view(s) for /kmacs/nfstest/nfs41_loadgen/attr_stress.txt; top: [(320, '/kmacs/nfstest', 'prefix'), (1, '/', 'prefix'), (217, '/', 'prefix')]
PROBE:correlation.tenant PASS namespace tenant candidates (derived, ordered): [(1, 'default', 'prefix view id 320 path /kmacs/nfstest'), (7, 'nireny', 'prefix view id 217 path /')]
PROBE:deleg.availability PASS tenant default (no file_path) [111ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/ failed: HTTP 400: {"detail":"['__root__->file_path: field required']"}
PROBE:deleg.try0 FAIL tenant default /kmacs/nfstest/nfs41_loadgen/attr_stress.txt [215ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fnfs41_loadgen%2Fattr_stress.txt failed:
PROBE:deleg.try1 FAIL tenant default /nfs41_loadgen/attr_stress.txt [209ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/1/nfs4_delegs/?file_path=%2Fnfs41_loadgen%2Fattr_stress.txt failed: HTTP 400: {"detail
PROBE:deleg.try2 FAIL tenant nireny /kmacs/nfstest/nfs41_loadgen/attr_stress.txt [214ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/7/nfs4_delegs/?file_path=%2Fkmacs%2Fnfstest%2Fnfs41_loadgen%2Fattr_stress.txt failed:
PROBE:deleg.try3 FAIL tenant nireny /nfs41_loadgen/attr_stress.txt [434ms] -> GET https://var203.selab.vastdata.com:443/api/tenants/7/nfs4_delegs/?file_path=%2Fnfs41_loadgen%2Fattr_stress.txt failed: HTTP 400: {"detail
PROBE:correlation.winner FAIL no (tenant, syntax) pair produced an HTTP success for a real existing file - every attempt recorded verbatim
evidence directory: /home/vastdata/kjmtmp/opstat/fr2-20260818-192749/raw
SAFETY: this probe issues GET requests only; the API log must contain zero non-GET lines.
PROBE-RC 0
PROBE-END 2026-08-18 19:28:34
[19:28:34] PASS    : probe rc=0

== 6. read-only verification (API log) ==================================================
[19:28:34] PASS    : API log inside the run tree: /home/vastdata/kjmtmp/opstat/fr2-20260818-192749/raw/opstat-api-fr2-delegations-var203.selab.vastdata.com-443-710499.log
[19:28:34] PASS    : API log contains ZERO non-GET requests (D-008 honored)

== 7. post-run state and /tmp policy ==================================================
[19:28:34] PASS    : no new opstat artifacts in /tmp

== 8. minimum success check ==================================================
[19:28:35] ERROR   : minimum success NOT met: no (tenant, syntax) pair produced an HTTP success for a real file

== final packaging and verdict ==================================================
opstat FR2 delegation discovery - 20260818-192749
HEAD 4463499a90e15a799961cbeb1ff17d49a289a7c7  target var203.selab.vastdata.com  probe rc 0  script failures 1

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
  raw/deleg-try0-t1.txt
  raw/deleg-try1-t1.txt
  raw/deleg-try2-t7.txt
  raw/deleg-try3-t7.txt
  raw/file-mapping.txt
  raw/opstat-api-fr2-delegations-var203.selab.vastdata.com-443-710499.log
  raw/view-candidates.json
  timestamps.txt
[19:28:35] PASS    : ZIP integrity verified

      906  2026-08-18 19:28   fr2-20260818-192749/logs/mount-listing.txt
      862  2026-08-18 19:27   fr2-20260818-192749/logs/mounts.txt
        0  2026-08-18 19:28   fr2-20260818-192749/logs/tmp-before.txt
        0  2026-08-18 19:28   fr2-20260818-192749/logs/tmp-after.txt
      436  2026-08-18 19:28   fr2-20260818-192749/candidates.txt
      242  2026-08-18 19:28   fr2-20260818-192749/prereqs.txt
---------                     -------
    91967                     26 files

-rw-rw-r-- 1 vastdata vastdata 27K Aug 18 19:28 /home/vastdata/opstat-fr2-delegation-discovery-20260818-192749.zip
47505d0acfb2e66df2fa81446bdc53b55d0c6a761881674be431fe30952ecb47  /home/vastdata/opstat-fr2-delegation-discovery-20260818-192749.zip

======================================================================
RESULT: RUN FAILED (1 failure(s) - see ERROR lines above).
The archive still contains the failure evidence; return it anyway:

    /home/vastdata/opstat-fr2-delegation-discovery-20260818-192749.zip
======================================================================
vastdata@kevin-mcdonald-ubu-01:~/git/opstat$


