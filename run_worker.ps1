param(
    [switch]$Once,
    [int]$Interval = 30,
    [ValidateSet("BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT")]
    [string]$TestSignal = "",
    [ValidateSet("STOP", "TARGET")]
    [string]$TestExit = "STOP",
    [double]$TestPrice = 25000,
    [switch]$NoAutoPaperFill
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvPip = Join-Path $ProjectRoot ".venv\Scripts\pip.exe"

Set-Location $ProjectRoot

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

$ArgsList = @("signal_worker.py", "--interval", "$Interval")
if ($Once) {
    $ArgsList += "--once"
}
if ($TestSignal) {
    $ArgsList += @("--test-signal", $TestSignal, "--test-exit", $TestExit, "--test-price", "$TestPrice")
}
if ($NoAutoPaperFill) {
    $ArgsList += "--no-auto-paper-fill"
}

Write-Host "Starting signal worker..."
& $VenvPython @ArgsList
