# ============================================================================
#  Start Jarvis  —  desktop launcher
#  1. Makes sure LM Studio's local server is running (starts it if not).
#  2. Makes sure the brain model (qwen/qwen3-8b) is loaded.
#  3. Starts Jarvis (Discord bot + voice).
#  Leave this window open while using Jarvis. Ctrl+C or close it to stop.
# ============================================================================

$ErrorActionPreference = "Stop"

$Root      = "C:\Coding_Projects\jarvis"
$Jarvis    = Join-Path $Root ".venv\Scripts\jarvis.exe"
$Lms       = Join-Path $env:USERPROFILE ".lmstudio\bin\lms.exe"
$ApiBase   = "http://localhost:1234/v1"
$ModelKey  = "qwen/qwen3-8b"     # matches JARVIS_LLM_MODEL in config.py

Set-Location $Root

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }

# --- 0. Kill any stale Jarvis instance --------------------------------------
# Two bots on the same token fight over the voice session and Discord rejects
# the new voice connection with WebSocket 4006 ("session no longer valid"). A
# window closed with the X (instead of Ctrl+C) can leave the process alive, so
# always clear old instances before starting a fresh one.
$mine = $PID
$stale = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='jarvis.exe'" |
    Where-Object { $_.CommandLine -match 'jarvis' -and $_.CommandLine -match 'discord' -and $_.ProcessId -ne $mine }
if ($stale) {
    Write-Host ">> Stopping a previous Jarvis instance so voice can reconnect cleanly..." -ForegroundColor Yellow
    foreach ($p in $stale) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
    }
    Start-Sleep -Seconds 2  # give Discord a moment to release the old voice session
}

function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

function Test-Server {
    try {
        $r = Invoke-WebRequest -Uri "$ApiBase/models" -TimeoutSec 3 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch { return $false }
}

function Test-ModelLoaded {
    # NOTE: /v1/models lists *downloaded* models, not loaded ones, so it can't
    # be trusted here. `lms ps --json` lists what is actually loaded in memory
    # (returns "[]" when nothing is loaded).
    try {
        $json = & $Lms ps --json 2>$null
        if (-not $json) { return $false }
        $loaded = $json | ConvertFrom-Json
        foreach ($m in $loaded) {
            if ($m.modelKey -like "$ModelKey*" -or $m.identifier -like "$ModelKey*" -or $m.path -like "*$ModelKey*") {
                return $true
            }
        }
        return $false
    } catch { return $false }
}

Write-Host "===============================================" -ForegroundColor DarkCyan
Write-Host "  Starting Jarvis" -ForegroundColor White
Write-Host "===============================================" -ForegroundColor DarkCyan

# --- Sanity checks --------------------------------------------------------
if (-not (Test-Path $Jarvis)) {
    Write-Warn "Could not find Jarvis at: $Jarvis"
    Write-Warn "Is the virtualenv set up? (.venv). Aborting."
    Read-Host "`nPress Enter to close"; exit 1
}
if (-not (Test-Path $Lms)) {
    Write-Warn "Could not find the LM Studio CLI at: $Lms"
    Write-Warn "Open the LM Studio app once so it installs its CLI, then retry."
    Read-Host "`nPress Enter to close"; exit 1
}

# --- 1. LM Studio server --------------------------------------------------
Write-Step "Checking the LM Studio server..."
if (Test-Server) {
    Write-Ok "Server already running."
} else {
    Write-Warn "Server is down. Starting it..."
    & $Lms server start | Out-Host
    $deadline = (Get-Date).AddSeconds(45)
    while (-not (Test-Server)) {
        if ((Get-Date) -gt $deadline) {
            Write-Warn "Server did not come up within 45s. Aborting."
            Read-Host "`nPress Enter to close"; exit 1
        }
        Start-Sleep -Milliseconds 800
    }
    Write-Ok "Server is up."
}

# --- 2. Brain model -------------------------------------------------------
Write-Step "Checking the brain model ($ModelKey)..."
if (Test-ModelLoaded) {
    Write-Ok "Model already loaded."
} else {
    Write-Warn "Model not loaded. Loading (this can take ~30-60s)..."
    & $Lms load $ModelKey -y | Out-Host
    if (Test-ModelLoaded) {
        Write-Ok "Model loaded."
    } else {
        Write-Warn "Model didn't report as loaded, but continuing anyway."
        Write-Warn "If Jarvis can't think, check LM Studio."
    }
}

# --- 3. Jarvis ------------------------------------------------------------
Write-Step "Starting Jarvis (Discord bot + voice)..."
Write-Host "   Keep this window open. Press Ctrl+C to stop Jarvis." -ForegroundColor DarkGray
Write-Host ""
& $Jarvis discord

Write-Host "`nJarvis stopped." -ForegroundColor Yellow
Read-Host "Press Enter to close this window"
