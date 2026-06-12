param()
# Start Aurum: cage broker (port 8900) + Hermes gateway.
#
# Prerequisites:
#   1. Copy .env.example to .env and fill in at minimum:
#        ANTHROPIC_AUTH_TOKEN=<openrouter key>
#        ANTHROPIC_BASE_URL=https://openrouter.ai/api/v1
#        ANTHROPIC_MODEL=<model slug>
#        TELEGRAM_BOT_TOKEN=<bot token from @BotFather>
#        TELEGRAM_ALLOWED_USERS=8712784438
#   2. Build the cage image once:
#        docker build -f container/aurum/Dockerfile -t aurum-agent:latest .
#   3. Drop a shortcut to scripts\aurum-startup.vbs into:
#        %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
#
# Logs: %TEMP%\aurum-logs\  |  Stop: scripts\stop-aurum.ps1

$ErrorActionPreference = 'Continue'
$PROJECT = Split-Path $PSScriptRoot -Parent
$LOGS    = Join-Path $env:TEMP 'aurum-logs'
$PYTHONW = Join-Path $PROJECT '.venv\Scripts\pythonw.exe'

New-Item -ItemType Directory -Force $LOGS | Out-Null

# --- Load .env ----------------------------------------------------------------
$dotenv = Join-Path $PROJECT '.env'
if (Test-Path $dotenv) {
    foreach ($line in (Get-Content $dotenv)) {
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        $idx = $trimmed.IndexOf('=')
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $val = $trimmed.Substring($idx + 1).Trim()
        # Strip optional surrounding quotes
        if ($val.Length -ge 2) {
            $first = $val.Substring(0, 1)
            $last  = $val.Substring($val.Length - 1, 1)
            if (($first -eq '"'  -and $last -eq '"') -or
                ($first -eq "'" -and $last -eq "'")) {
                $val = $val.Substring(1, $val.Length - 2)
            }
        }
        [System.Environment]::SetEnvironmentVariable($key, $val, 'Process')
    }
} else {
    Write-Warning "[aurum] No .env at $dotenv -- copy .env.example and fill in secrets."
}

# --- Wire the cage into the gateway -------------------------------------------
$env:GATEWAY_PROXY_URL = 'http://127.0.0.1:8900'
$env:AURUM_PROJECT_DIR = $PROJECT
if (-not $env:AURUM_GROUPS_ROOT) {
    $env:AURUM_GROUPS_ROOT = Join-Path $env:USERPROFILE '.aurum\groups'
}
New-Item -ItemType Directory -Force $env:AURUM_GROUPS_ROOT | Out-Null
# Use a persistent container (one docker run reused across turns) instead of
# spawning --rm per message. Removes the ~10-20s Docker cold-start cost.
# Set to '0' to revert to ephemeral-per-turn mode.
$env:AURUM_PERSISTENT_CONTAINER = '1'

# --- Port check ---------------------------------------------------------------
function Test-Port([int]$port) {
    $c = [System.Net.Sockets.TcpClient]::new()
    try { $c.Connect('127.0.0.1', $port); $c.Close(); return $true }
    catch { return $false }
}

# --- Cage broker --------------------------------------------------------------
if (Test-Port 8900) {
    Write-Host '[aurum] cage broker:8900 already up'
} else {
    Write-Host '[aurum] Starting cage broker...'
    Start-Process -FilePath $PYTHONW `
        -ArgumentList '-u', '-m', 'aurum.aurum.cage.broker' `
        -WorkingDirectory $PROJECT `
        -RedirectStandardOutput (Join-Path $LOGS 'broker.log') `
        -RedirectStandardError  (Join-Path $LOGS 'broker-err.log') `
        -WindowStyle Hidden

    $waited = 0
    while (-not (Test-Port 8900) -and $waited -lt 10) {
        Start-Sleep -Seconds 1
        $waited++
    }
    if (Test-Port 8900) {
        Write-Host '[aurum] Cage broker up.'
    } else {
        Write-Warning ('[aurum] Cage broker did not bind on :8900 after ' + $waited + 's -- check ' + $LOGS + '\broker-err.log')
    }
}

# --- Hermes gateway -----------------------------------------------------------
Write-Host '[aurum] Starting gateway...'
Start-Process -FilePath $PYTHONW `
    -ArgumentList '-u', '-m', 'hermes_cli.main', 'gateway', 'run' `
    -WorkingDirectory $PROJECT `
    -RedirectStandardOutput (Join-Path $LOGS 'gateway.log') `
    -RedirectStandardError  (Join-Path $LOGS 'gateway-err.log') `
    -WindowStyle Hidden

Write-Host ('[aurum] Started. Logs: ' + $LOGS)
