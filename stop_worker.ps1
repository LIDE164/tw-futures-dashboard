$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidPath = Join-Path $ProjectRoot "data\signal_worker.pid"

if (-not (Test-Path $PidPath)) {
    Write-Host "signal_worker pid file was not found. It may already be stopped."
    exit 0
}

$WorkerPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $WorkerPid) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Host "signal_worker pid file was empty and has been removed."
    exit 0
}

$Process = Get-Process -Id $WorkerPid -ErrorAction SilentlyContinue
if (-not $Process) {
    Remove-Item -LiteralPath $PidPath -Force
    Write-Host "signal_worker was not running. Removed stale pid file."
    exit 0
}

Stop-Process -Id $WorkerPid
Remove-Item -LiteralPath $PidPath -Force
Write-Host "signal_worker stopped. PID: $WorkerPid"
