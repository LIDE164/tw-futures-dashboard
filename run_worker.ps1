param(
    [switch]$Once,
    [int]$Interval = 30,
    [double]$MinEntryRR = 1.5,
    [ValidateSet("BUY_LONG", "SELL_SHORT", "CLOSE_LONG", "CLOSE_SHORT")]
    [string]$TestSignal = "",
    [ValidateSet("STOP", "TARGET")]
    [string]$TestExit = "STOP",
    [double]$TestPrice = 25000,
    [switch]$NoAutoPaperFill,
    [switch]$NoAdaptiveRisk,
    [switch]$AllowChoppy,
    [switch]$NoRequire60mAlignment,
    [double]$MinAdx = 20,
    [double]$MinVolumeRatio = 0.85,
    [double]$MaxChaseAtr = 1.4,
    [int]$ConfirmationBars = 2,
    [switch]$NoLong,
    [switch]$NoShort,
    [switch]$NoScoreExitRequiresProfit,
    [double]$MinScoreExitProfitPoints = 0
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

$ArgsList = @(
    "signal_worker.py",
    "--interval", "$Interval",
    "--min-entry-rr", "$MinEntryRR",
    "--min-adx", "$MinAdx",
    "--min-volume-ratio", "$MinVolumeRatio",
    "--max-chase-atr", "$MaxChaseAtr",
    "--confirmation-bars", "$ConfirmationBars",
    "--min-score-exit-profit-points", "$MinScoreExitProfitPoints"
)
if ($Once) {
    $ArgsList += "--once"
}
if ($TestSignal) {
    $ArgsList += @("--test-signal", $TestSignal, "--test-exit", $TestExit, "--test-price", "$TestPrice")
}
if ($NoAutoPaperFill) {
    $ArgsList += "--no-auto-paper-fill"
}
if ($NoAdaptiveRisk) {
    $ArgsList += "--no-adaptive-risk"
}
if ($AllowChoppy) {
    $ArgsList += "--no-reject-choppy"
}
if ($NoRequire60mAlignment) {
    $ArgsList += "--no-require-60m-alignment"
}
if ($NoLong) {
    $ArgsList += "--no-allow-long"
}
if ($NoShort) {
    $ArgsList += "--no-allow-short"
}
if ($NoScoreExitRequiresProfit) {
    $ArgsList += "--no-score-exit-requires-profit"
}

Write-Host "Starting signal worker..."
& $VenvPython @ArgsList
