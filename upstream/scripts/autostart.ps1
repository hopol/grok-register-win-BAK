param(
    [int]$HealthTimeoutSeconds = 30
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$logDir = Join-Path $root "data\logs"
$logPath = Join-Path $logDir "autostart.log"
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $root "launcher.py"
$healthUrl = "http://127.0.0.1:8787/health"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-AutostartLog([string]$Message) {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Encoding UTF8 -Value "[$timestamp] $Message"
}

try {
    $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
    if ($response.StatusCode -eq 200) {
        Write-AutostartLog "Panel already running; startup skipped."
        exit 0
    }
} catch {
    Write-AutostartLog "Panel is not running; starting it now."
}

if (-not (Test-Path -LiteralPath $pythonw)) {
    Write-AutostartLog "Missing $pythonw. Run setup.bat before enabling autostart."
    exit 1
}

$env:GROK_AUTOSTART = "1"
$env:GROK_OPEN_BROWSER = "0"
$process = Start-Process `
    -FilePath $pythonw `
    -ArgumentList @($launcher) `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -PassThru
Write-AutostartLog "Launcher started with PID $($process.Id)."

$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-AutostartLog "Panel health check passed."
            exit 0
        }
    } catch {
        # Keep waiting until the deadline; launcher details are in panel_boot.log.
    }
}

Write-AutostartLog "Panel health check timed out. See data\logs\panel_boot.log."
exit 1
