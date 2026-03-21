# Start-Qdrant.ps1
# Starts the Qdrant vector database via Docker Desktop.
# Run this before starting the watcher or API server.

$ErrorActionPreference = "Stop"
$QdrantDir = Join-Path $PSScriptRoot "..\vector-db\qdrant"

Write-Host "Starting Qdrant..."
Push-Location $QdrantDir
try {
    docker compose up -d
    Write-Host "Qdrant running on http://localhost:6333"
} finally {
    Pop-Location
}
