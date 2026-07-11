param(
    [int]$Interval = 30,
    [double]$MinEntryRR = 1.5,
    [switch]$NoAutoPaperFill,
    [switch]$NoAdaptiveRisk
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $ProjectRoot "data"
$LogDir = Join-Path $ProjectRoot "logs"
$PidPath = Join-Path $DataDir "signal_worker.pid"
$OutLog = Join-Path $LogDir "signal_worker.out.log"
$ErrLog = Join-Path $LogDir "signal_worker.err.log"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvPip = Join-Path $ProjectRoot ".venv\Scripts\pip.exe"

New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Set-Location $ProjectRoot

if (Test-Path $PidPath) {
    $ExistingPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($ExistingPid) {
        $ExistingProcess = Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
        if ($ExistingProcess) {
            Write-Host "signal_worker is already running. PID: $ExistingPid"
            exit 0
        }
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating local virtual environment..."
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PythonLauncher) {
        py -m venv .venv
    } else {
        python -m venv .venv
    }
}

Write-Host "Installing / updating requirements..."
& $VenvPython -m pip install --upgrade pip
& $VenvPip install -r requirements.txt

$ArgsList = @("signal_worker.py", "--interval", "$Interval", "--min-entry-rr", "$MinEntryRR")
if ($NoAutoPaperFill) {
    $ArgsList += "--no-auto-paper-fill"
}
if ($NoAdaptiveRisk) {
    $ArgsList += "--no-adaptive-risk"
}

Write-Host "Starting signal_worker in background..."
$Process = Start-Process `
    -FilePath $VenvPython `
    -ArgumentList $ArgsList `
    -WorkingDirectory $ProjectRoot `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -WindowStyle Hidden `
    -PassThru

Set-Content -Path $PidPath -Value $Process.Id -Encoding ASCII
Write-Host "signal_worker started. PID: $($Process.Id)"
Write-Host "Output log: $OutLog"
Write-Host "Error log:  $ErrLog"
