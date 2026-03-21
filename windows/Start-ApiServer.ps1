# Start-ApiServer.ps1
# Starts the RAG API server on http://localhost:8000
# Keep this window open. Ctrl+C stops the server.

$ErrorActionPreference = "Stop"
$RepoRoot = Join-Path $PSScriptRoot ".."

Push-Location $RepoRoot
try {
    uvicorn web.api_server:app --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
