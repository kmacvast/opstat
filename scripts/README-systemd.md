# Protocol load generators (systemd)

Lab traffic generators that exercise the same NFS, SMB, NVMe-oTCP, and S3
paths `opstat` displays. Each protocol is a standalone bash script. On Linux
they can also run as **separate systemd services** so load survives SSH
disconnects.

Windows SMB load is a PowerShell script: [Invoke-SmbOpstatLoad.ps1](Invoke-SmbOpstatLoad.ps1).

These are **destructive lab tools**. They create and delete files, issue
NVMe admin commands, and put real I/O on the target. Do not point them at
production data.

---

## Git workflow (MacBook is source of truth)

Do not `scp` or edit these files directly on the lab host. Keep the clones
in sync with git:

1. Change files in the MacBook clone (`~/git/kmactools` or `~/git/opstat`).
2. Commit and push to origin.
3. On the lab server: `cd ~/kmactools && git pull` (or the matching repo).

Install or refresh systemd units from the pulled tree:

```bash
sudo ./scripts/systemd/install-lab-loadgen-units.sh
sudo systemctl restart nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen
```

---

## Scripts

| Script | systemd unit | Default target | What it exercises |
|--------|--------------|----------------|-------------------|
| [nfs3-loadgen.sh](nfs3-loadgen.sh) | `nfs3-loadgen.service` | `/mnt/kmacs-root/nfstest` | NFSv3 RPCs (GETATTR/SETATTR, namespace churn, COMMIT, NLM locks, fio READ/WRITE) |
| [nfs41-loadgen.sh](nfs41-loadgen.sh) | `nfs41-loadgen.service` | `/mnt/nfs41test` | NFSv4.1 compounding, OPEN/CLOSE, byte-locks, GETATTR/SETATTR, fio |
| [smb-loadgen.sh](smb-loadgen.sh) | `smb-loadgen.service` | `//172.200.203.6/opstattest` at `/mnt/smbtest` | SMB2 READ/WRITE, CREATE/CLOSE, QUERY_DIRECTORY, QUERY_INFO/SET_INFO, LOCK, CHANGE_NOTIFY |
| [block-loadgen.sh](block-loadgen.sh) | `block-loadgen.service` | NVMe-oTCP from `/etc/nvme/discovery.conf` | write-zeroes, compare, identify, discovery, TRIM, fio (filesystem if healthy, else raw namespace) |
| [s3-loadgen.sh](s3-loadgen.sh) | `s3-loadgen.service` | bucket `kmacs-elbencho-test` | GET, PUT, DELETE, HEAD, LIST, multipart; elbencho PUT RPS when installed |
| [Invoke-SmbOpstatLoad.ps1](Invoke-SmbOpstatLoad.ps1) | (run on Windows) | `\\172.200.203.6\opstattest` | Same SMB opcode mix from a Windows client |

Unit files: [systemd/](systemd/). Installer: [systemd/install-lab-loadgen-units.sh](systemd/install-lab-loadgen-units.sh).
Health snapshot: [systemd/loadgen-status.sh](systemd/loadgen-status.sh).

Watch the matching dashboard:

```bash
./opstat --nfs --version=3.0 --vms <VMS_HOST> --user admin
./opstat --nfs --version=4.1 --vms <VMS_HOST> --user admin
./opstat --smb --vms <VMS_HOST> --user admin
./opstat --block --nvme-over-tcp --vms <VMS_HOST> --user admin
./opstat --s3 --vms <VMS_HOST> --user admin
```

---

## Prerequisites (Linux client)

| Loadgen | Needs |
|---------|--------|
| All NFS / SMB / block | `fio`, root (`sudo`) |
| nfs3 / nfs41 | The path already mounted as NFSv3 or NFSv4.1 |
| smb | `cifs-utils`, credentials file (see below) |
| block | `nvme-cli`, `nvme_tcp` kernel module (`linux-modules-extra-$(uname -r)` on Ubuntu), discovery entries in `/etc/nvme/discovery.conf` |
| s3 | AWS CLI v2; optional `elbencho`. Profile credentials in `~/.aws/` |

---

## Run a script by hand

From a clone of this repo, on the **Linux** client that holds the mounts:

```bash
sudo ./scripts/nfs3-loadgen.sh /mnt/kmacs-root/nfstest
sudo ./scripts/nfs41-loadgen.sh /mnt/nfs41test
sudo ./scripts/smb-loadgen.sh /mnt/smbtest
sudo ./scripts/block-loadgen.sh
./scripts/s3-loadgen.sh          # does not require root
```

Each script runs until Ctrl+C. The trap kills worker loops and removes its
workdir (NFS/SMB) or S3 prefix objects.

Pass a different mount as the first argument for NFS and SMB. Other knobs are
environment variables:

| Variable | Used by | Default |
|----------|---------|---------|
| `SMB_SHARE` | smb | `//172.200.203.6/opstattest` |
| `SMB_CREDFILE` | smb | `/etc/vast-loadgen/smb.cred` |
| `NVME_DISCOVERY_CONF` | block | `/etc/nvme/discovery.conf` |
| `NVME_DISCOVERY_PORT` | block | `4420` |
| `BLOCK_MNT1` / `BLOCK_MNT2` | block | `/mnt/blockhead1`, `/mnt/blockhead2` |
| `AWS_PROFILE` | s3 | `elbencho` |
| `S3_ENDPOINT` | s3 | `http://172.200.202.2` |
| `S3_BUCKET` | s3 | `kmacs-elbencho-test` |
| `S3_PREFIX` | s3 | `opstat-s3-loadgen` |

A hand-started script dies when the SSH session closes unless you wrap it in
`systemd-run`, `nohup`, or a unit (below).

---

## Install as systemd services

On the Linux lab host, from a clone of this repo:

```bash
sudo ./scripts/systemd/install-lab-loadgen-units.sh
```

That copies the five `.sh` files to `DEST_SCRIPTS` (default
`/home/vastdata/kmactools/scripts`), installs the unit files under
`/etc/systemd/system/`, rewrites `ExecStart` to match `DEST_SCRIPTS`, and runs
`systemctl daemon-reload`.

Override the install path if this host does not use `kmactools`:

```bash
sudo DEST_SCRIPTS=/usr/local/lib/opstat-loadgen ./scripts/systemd/install-lab-loadgen-units.sh
```

Enable and start (all five, or pick the ones you need):

```bash
sudo systemctl enable --now nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen
```

Units are `Restart=on-failure` and `WantedBy=multi-user.target`, so they come
back after a reboot if enabled. `systemctl stop` stays stopped.

### Manual unit install

If you prefer not to use the installer:

```bash
sudo cp scripts/*.sh /home/vastdata/kmactools/scripts/
sudo chmod 755 /home/vastdata/kmactools/scripts/*-loadgen.sh
sudo cp scripts/systemd/*.service /etc/systemd/system/
# edit ExecStart paths in the .service files if your script dir differs
sudo systemctl daemon-reload
```

The shipped `ExecStart` paths assume `/home/vastdata/kmactools/scripts/` and
the selab mount points. Edit the unit (or drop a file in
`/etc/systemd/system/<unit>.d/override.conf`) before enabling on another host.

---

## systemctl cheat sheet

Replace `<unit>` with `nfs3-loadgen`, `nfs41-loadgen`, `smb-loadgen`,
`block-loadgen`, or `s3-loadgen`.

```bash
sudo systemctl start <unit>
sudo systemctl stop <unit>
sudo systemctl restart <unit>
sudo systemctl status <unit>
systemctl is-active <unit>
systemctl is-enabled <unit>

sudo systemctl enable <unit>      # start at boot
sudo systemctl disable <unit>     # do not start at boot
sudo systemctl disable --now <unit>   # disable and stop

journalctl -u <unit> -f           # live logs
journalctl -u <unit> -n 50        # last 50 lines
```

`KillMode=mixed` sends SIGTERM to the main script first so its cleanup trap can
stop workers. If something hangs, systemd SIGKILLs the remaining cgroup after
`TimeoutStopSec` (30s, 45s for S3).

Check everything at once:

```bash
systemctl is-active nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen
systemctl is-enabled nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen
```

---

## Are they running and healthy?

`systemctl status` only tells you the unit is up. A healthy loadgen also has
workers (fio, nvme, aws, elbencho), live mounts, and files whose mtimes are
moving. Run the snapshot script from a clone on the lab host:

```bash
./scripts/systemd/loadgen-status.sh
```

What "healthy" looks like:

| Check | Healthy | Unhealthy |
|-------|---------|-----------|
| Unit `ACTIVE` | `active` | `failed`, `inactive`, `activating` looping |
| `RESTARTS` | 0, or a small number after a crash you already fixed | climbing every few seconds |
| nfs3 / nfs41 / smb `fio` count | >= 1 | 0 after the unit has been up > 15s |
| block `nvme` and/or `fio` | write-zeroes/compare and a fio job | unit active but both 0 |
| s3 `aws` and/or `elbencho` | aws CLI loops; elbencho during its 60s PUT window | neither process ever appears |
| Mounts | NFS/CIFS lines present | `DOWN` |
| Workdirs | `nfs3_loadgen`, `nfs41_loadgen`, `smb_loadgen` exist; listing changes | missing while the unit is active |
| Journal errors | empty for the last 3 minutes | repeating mount, fio, or 404 storms |

One-shot commands if you do not want the script:

```bash
# Unit state, PIDs, restart counters, CPU/memory
systemctl show -p Id,ActiveState,SubState,MainPID,NRestarts,ActiveEnterTimestamp,MemoryCurrent,CPUUsageNSec \
  nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen

# Processes actually doing I/O (expect fio; block also nvme; s3 also aws/elbencho)
ps -eo pid,ppid,etime,pcpu,pmem,args | grep -E '[f]io |[n]vme |[e]lbencho|[a]ws s3' | grep -v grep

# Per-unit cgroup (lists every worker systemd is tracking)
systemctl status --no-pager nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen | \
  sed -n '/Loaded:/p;/Active:/p;/CGroup/,/^$/p'

# Mounts the loadgens require
findmnt -n /mnt/kmacs-root /mnt/nfs41test /mnt/smbtest /mnt/blockhead1 2>/dev/null
sudo nvme list

# Workdirs: files should exist and timestamps should move if you re-run ls
sudo ls -lt /mnt/kmacs-root/nfstest/nfs3_loadgen | head
sudo ls -lt /mnt/nfs41test/nfs41_loadgen | head
sudo ls -lt /mnt/smbtest/smb_loadgen | head

# S3 PUT engine (elbencho logs throughput here)
tail -20 /tmp/s3-loadgen-elbencho.log

# Recent failures only
journalctl -u nfs3-loadgen -u nfs41-loadgen -u smb-loadgen -u block-loadgen -u s3-loadgen \
  --since '10 min ago' -p err..alert --no-pager
```

fio jobs layout large files on first start, so IOPS can lag for a minute or two
while `Laying out IO file` is in progress. Re-run the snapshot after that.

---

## Per-service notes

### nfs3-loadgen / nfs41-loadgen

Must already be mounted. The scripts refuse to start if `findmnt` does not show
the expected NFS version. Work files go under `<mount>/nfs3_loadgen` or
`<mount>/nfs41_loadgen` and are removed on stop.

fio uses `io_uring` when the binary supports it, otherwise `libaio`. POSIX
`lockmode=` is not used (fio 3.36 on Ubuntu noble does not accept it);
`--lockfile=exclusive` is the equivalent.

### smb-loadgen

If `/mnt/smbtest` is not already a CIFS mount, the script mounts `SMB_SHARE`
using `/etc/vast-loadgen/smb.cred` (mode `600`, owned by root):

```
username=user@realm.example
password=...
```

Do not commit this file. The installer will seed it from `~/.vastcreds` on the
lab host when that file exists and the cred file does not.

Linux `vers=3.0` is required for the selab share (`vers=3.1.1` is rejected).

On Windows, use the PowerShell generator instead of CIFS:

```powershell
.\scripts\Invoke-SmbOpstatLoad.ps1 -NasShare '\\172.200.203.6\opstattest'
```

### block-loadgen

1. Loads `nvme_tcp`.
2. Runs `nvme discover` / `nvme connect-all` for every `-t tcp` line in
   `/etc/nvme/discovery.conf`.
3. Mounts `/mnt/blockhead1` and `/mnt/blockhead2` from fstab when those UUIDs
   appear.
4. Issues write-zeroes / compare / identify against an **unmounted** namespace
   so a live XFS volume is not punched with zeros.
5. Runs fio on a writable filesystem mount if one is healthy; otherwise fio
   goes to the raw unmounted namespace.

If `modprobe nvme_tcp` fails on Ubuntu:

```bash
sudo apt-get install -y linux-modules-extra-$(uname -r)
sudo modprobe nvme_tcp
```

### s3-loadgen

Runs as user `vastdata` in the shipped unit (AWS config lives in that home
directory). Override `User=`, `AWS_PROFILE`, `S3_ENDPOINT`, and `S3_BUCKET` in
the unit or with `systemctl edit s3-loadgen`.

elbencho is optional. The AWS CLI loops still generate LIST/HEAD/PUT/GET/DELETE
and multipart. elbencho uses env `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
(not argv) and writes `/tmp/s3-loadgen-elbencho.log`.

---

## Change a unit without editing the shipped file

```bash
sudo systemctl edit nfs41-loadgen
```

Example override to point at a different mount:

```ini
[Service]
ExecStart=
ExecStart=/home/vastdata/kmactools/scripts/nfs41-loadgen.sh /mnt/other-nfs41
```

The empty `ExecStart=` clears the original. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart nfs41-loadgen
```

---

## Uninstall

```bash
sudo systemctl disable --now nfs3-loadgen nfs41-loadgen smb-loadgen block-loadgen s3-loadgen
sudo rm -f /etc/systemd/system/{nfs3,nfs41,smb,block,s3}-loadgen.service
sudo systemctl daemon-reload
```

This does not delete `/etc/vast-loadgen/smb.cred` or AWS profiles.
