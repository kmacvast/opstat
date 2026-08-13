<#
.SYNOPSIS
    Continuous SMB load generator for vast-opstat TUI validation.

.DESCRIPTION
    Runs until Ctrl+C. Exercises SMB2 workloads that map to vast-opstat --smb panels:

      Authoritative section
        SMB2_READ / SMB2_WRITE     - diskspd or .NET random/seq I/O
        METADATA (total)           - create/close/list/setattr churn

      Inferred from System Context (derived section — needs measurable signals)
        SMB2_CHANGE_NOTIFY         - FileSystemWatcher on load directories
        SMB2_LOCK                  - byte-range FileStream.Lock() contention
        SMB2_QUERY_DIRECTORY       - recursive directory enumeration
        SMB2_QUERY_INFO            - tight file attribute/stat queries
        SMB2_SET_INFO              - timestamp/attribute/rename churn
        SMB2_CREATE / SMB2_CLOSE   - rapid open/create/delete bursts

      Classifier hint line (no per-opcode VMS counter on current builds)
        Same opcodes above — hints appear when metadata mix is high; table rows
        in the derived section require NOTIFY/LOCK proxies or --clients session API.

    Session/tree rows (SMB2_SESSION_SETUP, TREE_CONNECT) require opstat
    --clients <this-host-ip> because list_smb_client_connections is scoped.

    Compatible with Windows PowerShell 5.1+. Optional diskspd accelerates
    data-path load when present at -DiskspdPath.

.PARAMETER NasShare
    UNC SMB share root (default: \\172.200.203.6\opstattest).

.PARAMETER DiskspdPath
    Path to diskspd.exe. When missing, .NET file I/O loops are used instead.

    All workloads run **concurrently** from start — reads, writes, metadata, notify,
    and locks are never paused. Phase rotation is a status label only.

.PARAMETER PhaseSeconds
    Seconds between status label rotation (does not stop any workers).

.PARAMETER ReadWorkers
    Supplemental .NET read loops (always run alongside diskspd when present).

.PARAMETER WriteWorkers
    Supplemental .NET write/flush loops (always run alongside diskspd when present).

.EXAMPLE
    .\Invoke-SmbOpstatLoad.ps1

.EXAMPLE
    .\Invoke-SmbOpstatLoad.ps1 -NasShare '\\172.200.203.6\opstattest' -PhaseSeconds 120
#>

[CmdletBinding()]
param(
    [string] $NasShare       = '\\172.200.203.6\opstattest',
    [string] $DiskspdPath    = 'C:\Diskspd\amd64\diskspd.exe',
    [int]    $PhaseSeconds  = 90,
    [int]    $MetaWorkers    = 6,
    [int]    $DirWorkers     = 3,
    [int]    $QueryInfoWorkers = 2,
    [int]    $SetInfoWorkers = 2,
    [int]    $LockWorkers    = 3,
    [int]    $NotifyWorkers  = 2,
    [int]    $BurstWorkers   = 2,
    [int]    $ReadWorkers    = 3,
    [int]    $WriteWorkers   = 3,
    [int]    $DiskspdDurationSec = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# ── Paths ────────────────────────────────────────────────────────────────────
$TestRoot      = Join-Path $NasShare 'opstat_smb_load'
$DataDir       = Join-Path $TestRoot 'data'
$MetaDir       = Join-Path $TestRoot 'metadata'
$TraverseDir   = Join-Path $TestRoot 'traverse'
$NotifyDir     = Join-Path $TestRoot 'notify_watch'
$LocalTemp     = Join-Path $env:TEMP 'smb_opstat_load'
$CompressSrc   = Join-Path $LocalTemp 'compressible.txt'
$RandomDat     = Join-Path $DataDir 'stress_random.dat'
$SeqDat        = Join-Path $DataDir 'stress_seq.dat'
$ReadDat       = Join-Path $DataDir 'stress_read.dat'
$LockFile      = Join-Path $DataDir 'lock_stress.dat'

$script:WorkerJobs = @()
$script:PhaseIndex = 0
$script:DiskspdJobLabels = @('DiskspdRandom', 'DiskspdSeqWrite', 'DiskspdSeqRead')
$script:PhaseNames = @(
    'concurrent mixed (read + write + metadata)',
    'concurrent mixed (read + write + metadata)',
    'concurrent mixed (read + write + metadata)',
    'concurrent mixed (read + write + metadata)',
    'concurrent mixed (read + write + metadata)'
)

function Write-Status {
    param([string] $Message, [ConsoleColor] $Color = 'Gray')
    $ts = Get-Date -Format 'HH:mm:ss'
    Write-Host "[$ts] $Message" -ForegroundColor $Color
}

function Ensure-Directory {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Initialize-TestLayout {
    Ensure-Directory $TestRoot
    Ensure-Directory $DataDir
    Ensure-Directory $MetaDir
    Ensure-Directory $TraverseDir
    Ensure-Directory $NotifyDir
    Ensure-Directory $LocalTemp

    if (-not (Test-Path -LiteralPath $CompressSrc)) {
        Write-Status 'Creating compressible source payload (robocopy /compress)' 'Yellow'
        ('ABC' * 500000) | Out-File -FilePath $CompressSrc -Encoding ascii
    }

    foreach ($seed in @($RandomDat, $SeqDat, $ReadDat, $LockFile)) {
        if (-not (Test-Path -LiteralPath $seed)) {
            Write-Status "Seeding $(Split-Path $seed -Leaf) on share..." 'Yellow'
            $fs = [System.IO.File]::Open($seed, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
            try {
                $buf = New-Object byte[] 1048576
                (New-Object Random).NextBytes($buf)
                for ($i = 0; $i -lt 512; $i++) { [void]$fs.Write($buf, 0, $buf.Length) }
            }
            finally { $fs.Close() }
        }
    }

    1..12 | ForEach-Object {
        $d = Join-Path $TraverseDir ("branch_{0:D2}" -f $_)
        Ensure-Directory $d
        1..30 | ForEach-Object {
            $f = Join-Path $d ("leaf_{0:D3}.bin" -f $_)
            if (-not (Test-Path -LiteralPath $f)) {
                [System.IO.File]::WriteAllBytes($f, (New-Object byte[] 4096))
            }
        }
    }

    1..4 | ForEach-Object {
        Ensure-Directory (Join-Path $NotifyDir ("watch_{0:D2}" -f $_))
    }
}

function Test-ShareReachable {
    if (-not (Test-Path -LiteralPath $NasShare)) {
        throw "SMB share not reachable: $NasShare"
    }
}

function Stop-JobsByName {
    param([string[]] $Names)
    foreach ($job in @($script:WorkerJobs)) {
        if ($job -and ($Names -contains $job.Name)) {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
            $script:WorkerJobs = @($script:WorkerJobs | Where-Object { $_ -ne $job })
        }
    }
}

function Get-DiskspdArgLine {
    param([string] $FilePath)
    return "`"$FilePath`""
}

function Get-ConcurrentDiskspdProfiles {
    $rnd = Get-DiskspdArgLine $RandomDat
    $seq = Get-DiskspdArgLine $SeqDat
    $read = Get-DiskspdArgLine $ReadDat
    $d = $DiskspdDurationSec

    # All three run together: mixed random I/O + dedicated seq read + dedicated seq write.
    return @(
        @{ Label = 'DiskspdRandom';  Args = "-b8K -d$d -o16 -t16 -r -w40 -c4G -h -L $rnd" }
        @{ Label = 'DiskspdSeqRead'; Args = "-b64K -d$d -o8 -t8 -r -w0 -si -c4G -h -L $read" }
        @{ Label = 'DiskspdSeqWrite'; Args = "-b64K -d$d -o8 -t8 -w100 -c4G -h -L $seq" }
    )
}

function Start-ConcurrentDiskspdWorkers {
    if (-not (Test-Path -LiteralPath $DiskspdPath)) { return }

    foreach ($profile in (Get-ConcurrentDiskspdProfiles)) {
        if (@($script:WorkerJobs | Where-Object { $_.Name -eq $profile.Label }).Count -gt 0) {
            continue
        }
        $job = Start-DiskspdLoop -Label $profile.Label -Args $profile.Args -DurationSec $DiskspdDurationSec
        if ($job) { $script:WorkerJobs += $job }
    }
}

function Set-IoWorkerCount {
    param(
        [string] $NamePrefix,
        [ValidateSet('Read', 'Write', 'Flush')]
        [string] $Mode,
        [int]    $DesiredCount
    )

    $existing = @($script:WorkerJobs | Where-Object { $_.Name -like "${NamePrefix}_*" })
    $current = $existing.Count
    if ($DesiredCount -le 0) {
        Stop-JobsByName ($existing | ForEach-Object { $_.Name })
        return
    }
    if ($current -gt $DesiredCount) {
        $trim = $existing | Select-Object -First ($current - $DesiredCount)
        Stop-JobsByName ($trim | ForEach-Object { $_.Name })
    }
    elseif ($current -lt $DesiredCount) {
        ($current + 1)..$DesiredCount | ForEach-Object {
            $idx = $_
            switch ($Mode) {
                'Read' {
                    $target = if ($idx % 2 -eq 0) { $ReadDat } else { $RandomDat }
                    $block = if ($idx % 2 -eq 0) { 65536 } else { 8192 }
                }
                'Write' {
                    $target = if ($idx % 2 -eq 0) { $SeqDat } else { $RandomDat }
                    $block = if ($idx % 2 -eq 0) { 65536 } else { 8192 }
                }
                'Flush' {
                    $target = $SeqDat
                    $block = 16384
                }
            }
            $script:WorkerJobs += Start-DotNetIoLoop -Name "${NamePrefix}_$idx" -Mode $Mode -BlockSize $block -TargetFile $target
        }
    }
}

function Start-ConcurrentIoWorkers {
    Set-IoWorkerCount -NamePrefix 'NetRead'  -Mode 'Read'  -DesiredCount $ReadWorkers
    Set-IoWorkerCount -NamePrefix 'NetWrite' -Mode 'Write' -DesiredCount $WriteWorkers
    Set-IoWorkerCount -NamePrefix 'NetFlush' -Mode 'Flush' -DesiredCount 1
    if (-not (Test-Path -LiteralPath $DiskspdPath)) {
        if (-not (@($script:WorkerJobs | Where-Object { $_.Name -eq 'NetRandom' }).Count)) {
            $script:WorkerJobs += Start-DotNetIoLoop -Name 'NetRandom' -Mode 'Random' -BlockSize 8192 -TargetFile $RandomDat
        }
    }
}

function Stop-AllWorkers {
    Write-Status 'Stopping workers...' 'Yellow'
    foreach ($job in $script:WorkerJobs) {
        if ($job) {
            Stop-Job -Job $job -ErrorAction SilentlyContinue
            Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        }
    }
    $script:WorkerJobs = @()
}

function Invoke-PeriodicCleanup {
    Get-ChildItem -LiteralPath $MetaDir -Filter 'meta_*.txt' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime |
        Select-Object -SkipLast 800 |
        Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem -LiteralPath $NotifyDir -Recurse -Filter 'notify_*.tmp' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime |
        Select-Object -SkipLast 400 |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Start-DiskspdLoop {
    param(
        [string] $Label,
        [string] $Args,
        [int]    $DurationSec = 45
    )
    if (-not (Test-Path -LiteralPath $DiskspdPath)) { return $null }

    $block = {
        param($Exe, $ArgLine, $Dur)
        while ($true) {
            $p = Start-Process -FilePath $Exe -ArgumentList $ArgLine -PassThru -WindowStyle Hidden
            $deadline = (Get-Date).AddSeconds($Dur)
            while (-not $p.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
            if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 2
        }
    }

    return Start-Job -Name $Label -ScriptBlock $block -ArgumentList $DiskspdPath, $Args, $DurationSec
}

function Start-DotNetIoLoop {
    param(
        [string] $Name,
        [ValidateSet('Read', 'Write', 'Random', 'Flush')]
        [string] $Mode,
        [int]    $BlockSize = 65536,
        [string] $TargetFile
    )

    $block = {
        param($Mode, $BlockSize, $Target)
        $rng = New-Object Random
        $buf = New-Object byte[] $BlockSize
        while ($true) {
            try {
                switch ($Mode) {
                    'Read' {
                        $fs = [System.IO.File]::Open($Target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                        $offset = [int64]($rng.NextDouble() * [Math]::Max(1, ($fs.Length - $BlockSize)))
                        $fs.Seek($offset, [IO.SeekOrigin]::Begin) | Out-Null
                        [void]$fs.Read($buf, 0, $BlockSize)
                        $fs.Close()
                    }
                    'Write' {
                        $fs = [System.IO.File]::Open($Target, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
                        $offset = [int64]($rng.NextDouble() * [Math]::Max(1, ($fs.Length - $BlockSize)))
                        $fs.Seek($offset, [IO.SeekOrigin]::Begin) | Out-Null
                        $rng.NextBytes($buf)
                        [void]$fs.Write($buf, 0, $BlockSize)
                        $fs.Close()
                    }
                    'Flush' {
                        $fs = [System.IO.File]::Open($Target, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
                        $rng.NextBytes($buf)
                        [void]$fs.Write($buf, 0, 16384)
                        $fs.Flush($true)
                        $fs.Close()
                    }
                    'Random' {
                        if ($rng.NextDouble() -lt 0.65) {
                            $fs = [System.IO.File]::Open($Target, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
                            [void]$fs.Read($buf, 0, [Math]::Min($BlockSize, 8192))
                        }
                        else {
                            $fs = [System.IO.File]::Open($Target, [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
                            $rng.NextBytes($buf)
                            [void]$fs.Write($buf, 0, 8192)
                        }
                        $fs.Close()
                    }
                }
            }
            catch { Start-Sleep -Milliseconds 50 }
        }
    }

    return Start-Job -Name $Name -ScriptBlock $block -ArgumentList $Mode, $BlockSize, $TargetFile
}

function Start-MetadataWorker {
    param([int] $WorkerId, [string] $Folder)

    $block = {
        param($Folder, $WorkerId)
        $n = 0
        while ($true) {
            $n++
            $batch = Join-Path $Folder ("batch_{0}_{1}" -f $WorkerId, ($n % 30))
            New-Item -ItemType Directory -Path $batch -Force | Out-Null
            1..60 | ForEach-Object {
                $target = Join-Path $batch ("meta_{0}_{1}.txt" -f $WorkerId, $_)
                $sw = [System.IO.File]::CreateText($target)
                $sw.Write(('m' * 512))
                $sw.Close()
                if ($_ % 4 -eq 0) {
                    $renamed = Join-Path $batch ("ren_{0}_{1}.txt" -f $WorkerId, $_)
                    [System.IO.File]::Move($target, $renamed)
                    [System.IO.File]::Delete($renamed)
                }
                else {
                    [System.IO.File]::Delete($target)
                }
            }
            Get-ChildItem -LiteralPath $batch -Force -ErrorAction SilentlyContinue | Out-Null
            Remove-Item -LiteralPath $batch -Recurse -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds (50 + ($WorkerId * 15))
        }
    }

    return Start-Job -Name "Meta_$WorkerId" -ScriptBlock $block -ArgumentList $Folder, $WorkerId
}

function Start-CreateCloseBurstWorker {
    param([int] $WorkerId, [string] $Folder)

    $block = {
        param($Folder, $WorkerId)
        while ($true) {
            1..80 | ForEach-Object {
                $path = Join-Path $Folder ("burst_{0}_{1}.tmp" -f $WorkerId, ([guid]::NewGuid().ToString('N').Substring(0, 6)))
                try {
                    $fs = [System.IO.File]::Create($path)
                    $fs.WriteByte([byte]($_ % 255))
                    $fs.Close()
                    $rd = [System.IO.File]::OpenRead($path)
                    $rd.Close()
                }
                finally {
                    if (Test-Path -LiteralPath $path) {
                        Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
                    }
                }
            }
            Start-Sleep -Milliseconds 40
        }
    }

    return Start-Job -Name "Burst_$WorkerId" -ScriptBlock $block -ArgumentList $Folder, $WorkerId
}

function Start-DirectoryTraverseWorker {
    param([string] $Root, [int] $WorkerId = 1)

    $block = {
        param($Root, $WorkerId)
        while ($true) {
            Get-ChildItem -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue |
                ForEach-Object { $_.FullName } | Out-Null
            Get-ChildItem -LiteralPath $Root -Directory -Recurse -ErrorAction SilentlyContinue |
                ForEach-Object {
                    [void]$_.GetFiles('*', [IO.SearchOption]::TopDirectoryOnly)
                    [void]$_.GetDirectories()
                }
            # Explorer-style single-level refresh loops (QUERY_DIRECTORY)
            Get-ChildItem -LiteralPath $Root -ErrorAction SilentlyContinue | Out-Null
            Start-Sleep -Milliseconds (120 + ($WorkerId * 40))
        }
    }

    return Start-Job -Name ("DirTraverse_{0}" -f $WorkerId) -ScriptBlock $block -ArgumentList $Root, $WorkerId
}

function Start-QueryInfoWorker {
    param([string] $Root, [int] $WorkerId = 1)

    $block = {
        param($Root, $WorkerId)
        while ($true) {
            $files = Get-ChildItem -LiteralPath $Root -Recurse -File -ErrorAction SilentlyContinue |
                Select-Object -First 120
            foreach ($item in $files) {
                [void]$item.Length
                [void]$item.LastWriteTimeUtc
                [void]$item.CreationTimeUtc
                [void]$item.Attributes
                [void]$item.IsReadOnly
                try { [void][System.IO.File]::GetAttributes($item.FullName) } catch {}
            }
            Start-Sleep -Milliseconds (80 + ($WorkerId * 30))
        }
    }

    return Start-Job -Name ("QueryInfo_{0}" -f $WorkerId) -ScriptBlock $block -ArgumentList $Root, $WorkerId
}

function Start-SetInfoWorker {
    param([string] $Folder, [int] $WorkerId = 1)

    $block = {
        param($Folder, $WorkerId)
        while ($true) {
            $f = Join-Path $Folder ('setinfo_{0}_{1}.tmp' -f $WorkerId, ([guid]::NewGuid().ToString('N').Substring(0, 8)))
            [System.IO.File]::WriteAllText($f, 'setinfo churn')
            $item = Get-Item -LiteralPath $f -Force
            $now = Get-Date
            $item.LastWriteTime = $now
            $item.LastAccessTime = $now.AddSeconds(3)
            $item.CreationTime = $now.AddSeconds(-3)
            $item.IsReadOnly = $true
            $item.IsReadOnly = $false
            [System.IO.File]::SetAttributes($f, [IO.FileAttributes]::Hidden)
            [System.IO.File]::SetAttributes($f, [IO.FileAttributes]::Archive)
            $renamed = Join-Path $Folder ('setinfo_ren_{0}.tmp' -f ([guid]::NewGuid().ToString('N').Substring(0, 8)))
            [System.IO.File]::Move($f, $renamed)
            Remove-Item -LiteralPath $renamed -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds (60 + ($WorkerId * 20))
        }
    }

    return Start-Job -Name ("SetInfo_{0}" -f $WorkerId) -ScriptBlock $block -ArgumentList $Folder, $WorkerId
}

function Start-ByteRangeLockWorker {
    param([string] $FilePath, [int] $WorkerId = 1)

    $block = {
        param($FilePath, $WorkerId)
        $rng = New-Object Random
        while ($true) {
            try {
                $fs = [System.IO.File]::Open(
                    $FilePath,
                    [IO.FileMode]::OpenOrCreate,
                    [IO.FileAccess]::ReadWrite,
                    [IO.FileShare]::ReadWrite
                )
                $len = [Math]::Min(65536, [Math]::Max(4096, [int]$fs.Length))
                if ($len -lt 4096) { $len = 4096 }
                $offset = $rng.Next(0, [Math]::Max(1, $len - 4096))
                $fs.Lock($offset, 4096)
                Start-Sleep -Milliseconds ($rng.Next(80, 400) + ($WorkerId * 30))
                $fs.Unlock($offset, 4096)
                $fs.Close()
            }
            catch { Start-Sleep -Milliseconds 40 }
        }
    }

    return Start-Job -Name ("Lock_{0}" -f $WorkerId) -ScriptBlock $block -ArgumentList $FilePath, $WorkerId
}

function Start-ChangeNotifyWorker {
    param([string] $WatchRoot, [int] $WorkerId = 1)

    $block = {
        param($WatchRoot, $WorkerId)
        $watchDir = Join-Path $WatchRoot ("watch_{0:D2}" -f (($WorkerId % 4) + 1))
        if (-not (Test-Path -LiteralPath $watchDir)) {
            New-Item -ItemType Directory -Path $watchDir -Force | Out-Null
        }

        $watcher = New-Object System.IO.FileSystemWatcher
        $watcher.Path = $WatchRoot
        $watcher.IncludeSubdirectories = $true
        $watcher.NotifyFilter = [IO.NotifyFilters]::FileName -bor
            [IO.NotifyFilters]::DirectoryName -bor
            [IO.NotifyFilters]::LastWrite -bor
            [IO.NotifyFilters]::Size -bor
            [IO.NotifyFilters]::CreationTime
        $watcher.EnableRaisingEvents = $true

        $n = 0
        while ($true) {
            $n++
            $touch = Join-Path $watchDir ("notify_{0}_{1}.tmp" -f $WorkerId, ($n % 200))
            [System.IO.File]::WriteAllText($touch, ('n' * 128))
            if ($n % 3 -eq 0) {
                $ren = Join-Path $watchDir ("notify_ren_{0}_{1}.tmp" -f $WorkerId, ($n % 200))
                if (Test-Path -LiteralPath $touch) {
                    [System.IO.File]::Move($touch, $ren)
                    Remove-Item -LiteralPath $ren -Force -ErrorAction SilentlyContinue
                }
            }
            else {
                Remove-Item -LiteralPath $touch -Force -ErrorAction SilentlyContinue
            }
            Start-Sleep -Milliseconds (100 + ($WorkerId * 25))
        }
    }

    return Start-Job -Name ("Notify_{0}" -f $WorkerId) -ScriptBlock $block -ArgumentList $WatchRoot, $WorkerId
}

function Start-CompressionWorker {
    param([string] $Src, [string] $DstFolder)

    $block = {
        param($Src, $DstFolder)
        $leaf = Split-Path $Src -Leaf
        while ($true) {
            $destName = 'compressible_{0}.txt' -f ([guid]::NewGuid().ToString('N').Substring(0, 8))
            robocopy (Split-Path $Src -Parent) $DstFolder $leaf /compress /mt:4 /R:1 /W:1 /NFL /NDL /NJH /NJS |
                Out-Null
            $copied = Join-Path $DstFolder $leaf
            if (Test-Path -LiteralPath $copied) {
                Rename-Item -LiteralPath $copied -NewName $destName -Force -ErrorAction SilentlyContinue
                $final = Join-Path $DstFolder $destName
                if (Test-Path -LiteralPath $final) {
                    Remove-Item -LiteralPath $final -Force -ErrorAction SilentlyContinue
                }
            }
            Start-Sleep -Seconds 30
        }
    }

    return Start-Job -Name 'Compress' -ScriptBlock $block -ArgumentList $Src, $DstFolder
}

function Start-AllWorkloads {
    $useDiskspd = Test-Path -LiteralPath $DiskspdPath
    if ($useDiskspd) {
        Write-Status "Using diskspd at $DiskspdPath (concurrent read+write+random)" 'Green'
        Start-ConcurrentDiskspdWorkers
    }
    else {
        Write-Status 'diskspd not found — using .NET I/O loops only' 'Yellow'
    }

    Start-ConcurrentIoWorkers

    1..$MetaWorkers | ForEach-Object {
        $script:WorkerJobs += Start-MetadataWorker -WorkerId $_ -Folder $MetaDir
    }
    1..$BurstWorkers | ForEach-Object {
        $script:WorkerJobs += Start-CreateCloseBurstWorker -WorkerId $_ -Folder $MetaDir
    }
    1..$DirWorkers | ForEach-Object {
        $script:WorkerJobs += Start-DirectoryTraverseWorker -Root $TraverseDir -WorkerId $_
    }
    1..$QueryInfoWorkers | ForEach-Object {
        $script:WorkerJobs += Start-QueryInfoWorker -Root $TraverseDir -WorkerId $_
    }
    1..$SetInfoWorkers | ForEach-Object {
        $script:WorkerJobs += Start-SetInfoWorker -WorkerId $_ -Folder $MetaDir
    }
    1..$LockWorkers | ForEach-Object {
        $script:WorkerJobs += Start-ByteRangeLockWorker -FilePath $LockFile -WorkerId $_
    }
    1..$NotifyWorkers | ForEach-Object {
        $script:WorkerJobs += Start-ChangeNotifyWorker -WatchRoot $NotifyDir -WorkerId $_
    }

    $script:WorkerJobs += Start-CompressionWorker -Src $CompressSrc -DstFolder $DataDir

    Write-Status ("Started {0} background workers" -f $script:WorkerJobs.Count) 'Green'
}

# ── Main ─────────────────────────────────────────────────────────────────────
try {
    Write-Host ''
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host ' vast-opstat SMB Continuous Load Generator' -ForegroundColor Cyan
    Write-Host '======================================================================' -ForegroundColor Cyan
    Write-Host " Share:      $NasShare"
    Write-Host " Test root:  $TestRoot"
    Write-Host " Phase rot:  every ${PhaseSeconds}s (status label only — all workers stay up)"
    Write-Host " Data I/O:   diskspd random+seq R/W + NetRead($ReadWorkers) + NetWrite($WriteWorkers) + NetFlush(1)"
    Write-Host ''
    Write-Host ' Concurrent opcode coverage (vast-opstat --smb):' -ForegroundColor DarkCyan
    Write-Host '   SMB2_READ/WRITE  — diskspd + NetRead/NetWrite/NetFlush (always together)'
    Write-Host '   METADATA         — Meta/Burst/Dir/QueryInfo/SetInfo workers (always)'
    Write-Host '   CHANGE_NOTIFY    — FileSystemWatcher workers (always)'
    Write-Host '   LOCK             — byte-range lock workers (always)'
    Write-Host ''
    Write-Host ' Press Ctrl+C to stop.' -ForegroundColor Yellow
    Write-Host '======================================================================' -ForegroundColor Cyan

    Test-ShareReachable
    Initialize-TestLayout
    Start-AllWorkloads

    $phaseStarted = Get-Date
    while ($true) {
        if (((Get-Date) - $phaseStarted).TotalSeconds -ge $PhaseSeconds) {
            $script:PhaseIndex = ($script:PhaseIndex + 1) % $script:PhaseNames.Count
            $phaseStarted = Get-Date
            Write-Status ("Status tick: {0}" -f $script:PhaseNames[$script:PhaseIndex]) 'Cyan'
        }

        $alive = @($script:WorkerJobs | Where-Object { $_.State -eq 'Running' }).Count
        Write-Status ("Workers running: {0}/{1} | phase: {2}" -f $alive, $script:WorkerJobs.Count, $script:PhaseNames[$script:PhaseIndex]) 'DarkGray'
        Invoke-PeriodicCleanup
        Start-Sleep -Seconds 15
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    # Ctrl+C
}
catch {
    Write-Status $_.Exception.Message 'Red'
    exit 1
}
finally {
    Stop-AllWorkers
    Write-Status 'Stopped. Test files remain on share for inspection; re-run to continue load.' 'Green'
}
