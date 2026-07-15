# opstat Setup Guide

Step-by-step instructions for running **opstat** on a client machine with no prior
Python experience. opstat is a terminal dashboard that queries your VAST VMS for
live NFS, SMB, or NVMe-oTCP performance statistics.

**Start here if you are new:** this guide covers macOS, Linux, and Windows before you
read the protocol-specific references in [README.md](README.md).

---

## What You Need

| Item | Details |
|------|---------|
| **Python** | Version 3.8 or newer |
| **Network** | HTTPS access to your VMS (default port 443) |
| **Credentials** | VMS username and password (typically `admin`) |
| **Git** | Optional but recommended for cloning the repository |

No third-party Python packages are required to **run** opstat - it uses the
standard library only. See [requirements.txt](requirements.txt).

---

## 1. Install Python

### macOS

**Option A - Official installer (simplest)**

1. Download Python from [python.org/downloads](https://www.python.org/downloads/).
2. Run the installer and complete the wizard.
3. Open **Terminal** and verify:

```bash
python3 --version
```

**Option B - Homebrew**

```bash
brew install python
python3 --version
```

Optional: add Homebrew Python to your shell profile if `python3` is not found:

```bash
echo 'export PATH="/opt/homebrew/bin:$PATH"' >> ~/.zshrc   # Apple Silicon
# or
echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.zshrc      # Intel Mac
source ~/.zshrc
```

### Linux (Ubuntu lab / jump host)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
python3 --version
```

### Windows

1. Download Python from [python.org/downloads](https://www.python.org/downloads/).
2. Run the installer.
3. **Important:** Check **"Add python.exe to PATH"** on the first installer screen.
4. Open **PowerShell** or **Command Prompt** and verify:

```powershell
python --version
```

If `python` is not found, reopen the terminal or sign out/in after installation.

---

## 2. Get the Code

```bash
git clone https://github.com/kmacvast/opstat.git
cd opstat
```

If you received a zip archive, extract it and open a terminal in
`opstat` (repository root).

**Path reference:**

| Platform | Project directory |
|----------|-------------------|
| macOS / Linux | `opstat` (repository root) |
| Windows | `opstat` |

---

## 3. Create a Virtual Environment

A virtual environment keeps opstat isolated from other Python projects.

### macOS / Linux

```bash
cd opstat
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt should show `(.venv)`.

### Windows (PowerShell)

```powershell
cd opstat
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell blocks the script, run once (as Administrator or CurrentUser):

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Then activate again.

### Windows (Command Prompt)

```cmd
cd opstat
python -m venv .venv
.venv\Scripts\activate.bat
```

To deactivate any platform: `deactivate`

---

## 4. Install Dependencies

With the virtual environment **activated**:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

For opstat **runtime**, this confirms the environment is ready (no external runtime
packages are required). For **development tests**, also install:

```bash
pip install pytest pytest-mock
```

---

## 5. Run opstat

Replace `var203.selab.vastdata.com` with your VMS hostname. Omit `--password` to be
prompted securely, or set `VAST_PASSWORD` in the environment.

### Easiest: interactive wizard (no flags)

Run with no arguments and opstat walks you through every choice, then starts:

```bash
./opstat          # macOS / Linux
python opstat     # Windows
```

Use `--menu` to force the wizard, or `--no-menu` to skip it. The wizard collects your
password/token securely (never on the command line). Everything below shows the equivalent
explicit-flag commands.

### NFS v3 (macOS / Linux)

```bash
./opstat --nfs --version=3.0 \
  --vms var203.selab.vastdata.com --user admin
```

### NFS v4.1 (macOS / Linux)

```bash
./opstat --nfs --version=4.1 \
  --vms var203.selab.vastdata.com --user admin
```

### NVMe-oTCP block - cluster-wide

**macOS / Linux:**

```bash
./opstat --block --nvme-over-tcp \
  --vms var203.selab.vastdata.com --user admin
```

**Windows (use `python` instead of `./`):**

```powershell
python opstat --block --nvme-over-tcp `
  --vms var203.selab.vastdata.com `
  --user admin
```

### NVMe-oTCP - multi-volume scoping

```bash
./opstat --block --nvme-over-tcp \
  --vms var203.selab.vastdata.com \
  --volumes kmacs-block-vol1,kmacs-block-vol2 \
  --user admin
```

Single-volume alias:

```bash
./opstat --block --nvme-over-tcp \
  --vms var203.selab.vastdata.com --volume my-vol --user admin
```

### SMB (macOS / Linux)

```bash
./opstat --smb \
  --vms var203.selab.vastdata.com --user admin
```

**Windows client load generator** (run against the SMB share under test):

```powershell
.\scripts\Invoke-SmbOpstatLoad.ps1 -NasShare '\\172.200.203.6\opstattest'
```

### Remote cluster via SSH tunnel (Teleport / zero-trust)

```bash
# Terminal 1 - forward local port 8443 to remote VMS HTTPS (443)
ssh -L 8443:var203.selab.vastdata.com:443 user@jump-host

# Terminal 2 - any protocol through the tunnel
./opstat --nfs --version=3.0 --vms localhost --vms-port 8443 --user admin
./opstat --block --nvme-over-tcp --vms localhost --vms-port 8443 --user admin
./opstat --smb --vms localhost --vms-port 8443 --user admin
```

Default port is `443` when `--vms-port` is omitted.

### Discover available metrics (no live dashboard)

```bash
./opstat --block --nvme-over-tcp \
  --vms var203.selab.vastdata.com --discover-metrics

./opstat --nfs --version=4.1 \
  --vms var203.selab.vastdata.com --discover-metrics

./opstat --smb \
  --vms var203.selab.vastdata.com --discover-metrics
```

### Debug API calls

```bash
./opstat --block --nvme-over-tcp \
  --vms var203.selab.vastdata.com --log-api-calls --discover-metrics
```

Log file path is printed on startup: `/tmp/opstat-api-*.log` (macOS/Linux).
On Windows, `/tmp` resolves via the system temp directory.

---

## 6. Using the Dashboard

Once running, the terminal shows live statistics.

### NVMe-oTCP keys

| Key | Action |
|-----|--------|
| `h` | Host / initiator drill-down |
| `v` | **VIP** path drill-down (not NFS View) |
| `c` | cNode path drill-down |
| `p` | Return to main view |
| `r` | Reset session stats |
| `q` | Quit |

Full reference: [NVMe_TCP_README.md](NVMe_TCP_README.md)

### NFS v3 / v4.1 keys

| Key | Action |
|-----|--------|
| `c` | cNode drill-down |
| `v` | View drill-down |
| `t` | Tenant drill-down |
| `x` | Exit drill-down |
| `q` | Quit |

References: [NFSv3_README.md](NFSv3_README.md), [NFSv41_README.md](NFSv41_README.md)

### SMB keys

| Key | Action |
|-----|--------|
| `c` | cNode drill-down |
| `v` | View / **share** drill-down (not NVMe VIP) |
| `t` | Tenant drill-down |
| `x` | Exit drill-down |
| `Space` | Force refresh |
| `q` | Quit |

**Panels:** SMB HEALTH & WORKLOAD · PERFORMANCE INSIGHTS · SMB2 OPCODE WORKFLOW
(only opcodes with live data; metadata shown as `METADATA (total)` aggregate).

**Client scoping:** `--clients 10.1.1.5,10.1.1.6` filters topn insights and session APIs.

Reference: [SMB_README.md](SMB_README.md)

---

## 7. Running Tests (Optional)

From the **repository root**:

```bash
cd opstat
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install pytest pytest-mock
pytest -v  # add tests/ when present
```

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `python3: command not found` (Mac/Linux) | Install Python or use `python` instead of `python3` |
| `python: command not found` (Windows) | Re-run installer with **Add to PATH** checked |
| `Permission denied` running `./opstat` | Run `python opstat ...` instead |
| PowerShell cannot run activate script | `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| SSL / certificate warnings | Expected in lab environments; opstat disables cert verification for internal VMS |
| Blank or warming-up stats (block) | Wait one refresh cycle (~5 s) for counter delta baselines |
| Volume not found | Verify name with `--discover-metrics` or `GET /api/volumes/` |
| Wrong drill key on block (`v`) | On block, `v` = VIP; NFS View drill is NFS-only |

---

## Next Steps

- [README.md](README.md) - protocol matrix and shared CLI options
- [NFSv3_README.md](NFSv3_README.md) - NFS v3 monitoring reference
- [NFSv41_README.md](NFSv41_README.md) - NFS v4.1 proxy architecture
- [SMB_README.md](SMB_README.md) - SMB monitoring reference
- [NVMe_TCP_README.md](NVMe_TCP_README.md) - block monitoring deep dive
