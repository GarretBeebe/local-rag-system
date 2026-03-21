# Start-Watcher.ps1
# Starts the RAG filesystem watcher in the foreground.
# Keep this window open. Ctrl+C stops the watcher.

$ErrorActionPreference = "Stop"
$RepoRoot = Join-Path $PSScriptRoot ".."

Push-Location $RepoRoot
try {
    python indexer\watcher.py
} finally {
    Pop-Location
}
