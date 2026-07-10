param(
    [switch]$Once,
    [int]$Interval = 30
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

Write-Host "Starting signal worker..."
& $VenvPython @ArgsList
