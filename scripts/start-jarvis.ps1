# ============================================================================
#  Start Jarvis  —  desktop launcher
#  1. Clears any stale Jarvis instance (so Discord voice can reconnect cleanly).
#  2. Checks the Claude API key is configured (.env).
#  3. Starts Jarvis (Discord bot + voice).
#  Leave this window open while using Jarvis. Ctrl+C or close it to stop.
#
#  The brain is Claude (Anthropic API) now — there is no local model to boot,
#  so this just needs the ANTHROPIC_API_KEY in .env and an internet connection.
# ============================================================================

$ErrorActionPreference = "Stop"

$Root      = "C:\Coding_Projects\jarvis"
$Jarvis    = Join-Path $Root ".venv\Scripts\jarvis.exe"
$EnvFile   = Join-Path $Root ".env"

Set-Location $Root

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

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

Write-Host "===============================================" -ForegroundColor DarkCyan
Write-Host "  Starting Jarvis" -ForegroundColor White
Write-Host "===============================================" -ForegroundColor DarkCyan

# --- Sanity checks --------------------------------------------------------
if (-not (Test-Path $Jarvis)) {
    Write-Warn "Could not find Jarvis at: $Jarvis"
    Write-Warn "Is the virtualenv set up? (.venv). Aborting."
    Read-Host "`nPress Enter to close"; exit 1
}

# --- 1. Claude API key ----------------------------------------------------
Write-Step "Checking the Claude API key..."
$hasKey = $false
if ($env:ANTHROPIC_API_KEY) { $hasKey = $true }
elseif (Test-Path $EnvFile) {
    if (Select-String -Path $EnvFile -Pattern '^\s*ANTHROPIC_API_KEY\s*=\s*\S' -Quiet) { $hasKey = $true }
}
if ($hasKey) {
    Write-Ok "API key found."
} else {
    Write-Warn "No ANTHROPIC_API_KEY found."
    Write-Warn "Add a line to $EnvFile :   ANTHROPIC_API_KEY=sk-ant-..."
    Write-Warn "(Get a key at https://console.anthropic.com — billing is pay-per-token.)"
    Read-Host "`nPress Enter to close"; exit 1
}

# --- 2. Jarvis ------------------------------------------------------------
Write-Step "Starting Jarvis (Discord bot + voice)..."
Write-Host "   Keep this window open. Press Ctrl+C to stop Jarvis." -ForegroundColor DarkGray
Write-Host ""
& $Jarvis discord

Write-Host "`nJarvis stopped." -ForegroundColor Yellow
Read-Host "Press Enter to close this window"
