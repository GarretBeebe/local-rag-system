# Install-Services.ps1
# Registers the RAG watcher and API server as Windows Task Scheduler tasks.
# Run once in an elevated (Administrator) PowerShell window.
#
# To remove the tasks:
#   Unregister-ScheduledTask -TaskName "RAG-Watcher" -Confirm:$false
#   Unregister-ScheduledTask -TaskName "RAG-ApiServer" -Confirm:$false

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

# --- RAG Watcher ---
$watcherAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -File `"$RepoRoot\windows\Start-Watcher.ps1`"" `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName "RAG-Watcher" `
    -Action $watcherAction `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "RAG filesystem watcher - auto-indexes document changes" `
    -Force

Write-Host "Registered: RAG-Watcher"

# --- RAG API Server ---
$apiAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NonInteractive -WindowStyle Hidden -File `"$RepoRoot\windows\Start-ApiServer.ps1`"" `
    -WorkingDirectory $RepoRoot

Register-ScheduledTask `
    -TaskName "RAG-ApiServer" `
    -Action $apiAction `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Description "RAG API server - OpenAI-compatible endpoint on :8000" `
    -Force

Write-Host "Registered: RAG-ApiServer"
Write-Host ""
Write-Host "Both tasks will start automatically at next logon."
Write-Host "To start them now:"
Write-Host "  Start-ScheduledTask -TaskName 'RAG-Watcher'"
Write-Host "  Start-ScheduledTask -TaskName 'RAG-ApiServer'"
