$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidPath = Join-Path $ProjectRoot "data\signal_worker.pid"
$OutLog = Join-Path $ProjectRoot "logs\signal_worker.out.log"
$ErrLog = Join-Path $ProjectRoot "logs\signal_worker.err.log"
$HeartbeatText = Join-Path $ProjectRoot "data\signal_worker_heartbeat.txt"

if (Test-Path $PidPath) {
    $WorkerPid = (Get-Content -Path $PidPath -ErrorAction SilentlyContinue | Select-Object -First 1)
    $Process = if ($WorkerPid) { Get-Process -Id $WorkerPid -ErrorAction SilentlyContinue } else { $null }
    if ($Process) {
        Write-Host "signal_worker is running. PID: $WorkerPid"
    } else {
        Write-Host "signal_worker is not running. Stale pid file: $PidPath"
    }
} else {
    Write-Host "signal_worker is not running."
}

if (Test-Path $HeartbeatText) {
    Write-Host ""
    Write-Host "Last worker heartbeat:"
    Get-Content -Path $HeartbeatText
} else {
    Write-Host ""
    Write-Host "Last worker heartbeat:"
    Write-Host "No text heartbeat yet. Restart the worker after updating, then check again after one interval."
}

if (Test-Path $OutLog) {
    Write-Host ""
    Write-Host "Last output log lines:"
    Get-Content -Path $OutLog -Tail 10
}

if (Test-Path $ErrLog) {
    Write-Host ""
    Write-Host "Last error log lines:"
    Get-Content -Path $ErrLog -Tail 10
}
