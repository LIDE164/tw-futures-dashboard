param(
    [int]$Interval = 30,
    [int]$LongEntryScore = 62,
    [int]$ShortEntryScore = 35,
    [double]$MinEntryRR = 1.5,
    [double]$RewardRiskRatio = 2.2,
    [switch]$NoAutoPaperFill,
    [switch]$NoAdaptiveRisk,
    [switch]$AllowChoppy,
    [switch]$NoRequire60mAlignment,
    [double]$MinAdx = 22,
    [double]$MinVolumeRatio = 1.0,
    [double]$MaxChaseAtr = 1.0,
    [int]$ConfirmationBars = 2,
    [switch]$Require5mConfirmation,
    [int]$FiveMinuteLongScore = 50,
    [int]$FiveMinuteShortScore = 50,
    [int]$CooldownBars = 2,
    [double]$BreakevenTriggerR = 1.0,
    [double]$BreakevenBufferPoints = 0,
    [int]$MaxHoldingBars = 24,
    [switch]$NoLong,
    [switch]$AllowShort,
    [switch]$NoShort,
    [switch]$NoScoreExitRequiresProfit,
    [double]$MinScoreExitProfitPoints = 0
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

$ArgsList = @(
    "signal_worker.py",
    "--interval", "$Interval",
    "--long-entry-score", "$LongEntryScore",
    "--short-entry-score", "$ShortEntryScore",
    "--min-entry-rr", "$MinEntryRR",
    "--reward-risk-ratio", "$RewardRiskRatio",
    "--min-adx", "$MinAdx",
    "--min-volume-ratio", "$MinVolumeRatio",
    "--max-chase-atr", "$MaxChaseAtr",
    "--confirmation-bars", "$ConfirmationBars",
    "--five-minute-long-score", "$FiveMinuteLongScore",
    "--five-minute-short-score", "$FiveMinuteShortScore",
    "--cooldown-bars", "$CooldownBars",
    "--breakeven-trigger-r", "$BreakevenTriggerR",
    "--breakeven-buffer-points", "$BreakevenBufferPoints",
    "--max-holding-bars", "$MaxHoldingBars",
    "--min-score-exit-profit-points", "$MinScoreExitProfitPoints"
)
if ($Require5mConfirmation) {
    $ArgsList += "--require-5m-confirmation"
} else {
    $ArgsList += "--no-require-5m-confirmation"
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
if ($NoShort -or -not $AllowShort) {
    $ArgsList += "--no-allow-short"
}
if ($NoScoreExitRequiresProfit) {
    $ArgsList += "--no-score-exit-requires-profit"
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
