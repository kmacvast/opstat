Approved to delete ONLY the temporary monitor created by your run:

2245  adhoc_opstat_smb_headline_cmds_1786661443

Do not delete or modify any of the other 38 pre-existing adhoc_opstat_* monitors yet.

After deleting 2245:

1. Confirm by GET that monitor 2245 no longer exists.
2. Confirm the count/list of the remaining pre-existing adhoc_opstat_* monitors.
3. Do not clean those up as part of this task.
4. Record the cleanup interruption you discovered as a separate potential defect:
   cleanup can be interrupted during a slow synchronous monitor drain, leaving
   an earlier-created monitor behind.
   Do not fix that defect during the SMB/S3 drill refactor unless it directly
   blocks safe testing.

Before any further real-cluster testing, fix your PTY driver so that:

- it sends q whenever possible
- SIGTERM is only a fallback
- after q or SIGTERM it waits for the opstat process to exit completely
- it does NOT close the PTY while opstat cleanup is still running
- it never uses SIGKILL
- it verifies the process has exited
- it checks the API log for cleanup completion
- it verifies no monitors created by that test session remain

Treat monitor cleanup verification as mandatory after every automated real-VMS
test run.

The correct S3 invocation is:

VAST_PASSWORD=<from environment> \
~/git/opstat/opstat \
  --s3 \
  --vms var203.selab.vastdata.com \
  --user admin

Do not put the password on the command line or in tracked files.

Continue using VAST_PASSWORD from the environment for all cluster access.

Now proceed read-only with the real-cluster BEFORE measurements for:

SMB:
- view drill
- tenant drill

S3:
- bucket drill
- tenant drill

Do not implement the port yet.

For each run capture:

- candidate object count
- ranking API sequence
- number of rank monitors created
- entry API call count
- wall-clock ranking time
- steady-state drill query cadence
- monitor cleanup result
- whether loading/status UI rendered before blocking work

For S3 also verify, but do not change:

- VIP ranking behavior
- 192.168.* filtering
- topn-only fallback behavior

Then give me a concise REAL-CLUSTER BEFORE BASELINE:

SMB view:
SMB tenant:
S3 bucket:
S3 tenant:
S3 VIP unchanged:
Cleanup verification:
Any differences from the mock:
Any newly discovered risks:

Do not change implementation files.
Do not commit.
Do not push.
Stop after the baseline report.