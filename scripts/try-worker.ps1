# Quick hands-on test of the Jarvis background worker.
# Run it with:   .\scripts\try-worker.ps1
# It adds a task that reminds you in 1 minute, then starts the worker loop.
# A Windows toast should pop up within ~a minute. Press Ctrl+C to stop.

$ErrorActionPreference = "Stop"
$j = "C:\Coding_Projects\jarvis\.venv\Scripts\jarvis.exe"

Write-Host "Adding a task that reminds you in 1 minute..." -ForegroundColor Cyan
& $j add "Grab a glass of water" --remind "1m" --priority high

Write-Host ""
Write-Host "Starting the worker (ticks every 15s). Watch for a toast pop-up." -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""
& $j worker --interval 15
