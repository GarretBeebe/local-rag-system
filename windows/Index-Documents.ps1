# Index-Documents.ps1
# Batch-indexes the documents/ directory into Qdrant.
# Run after adding new files manually to documents/.

$ErrorActionPreference = "Stop"
$RepoRoot = Join-Path $PSScriptRoot ".."

Push-Location $RepoRoot
try {
    python ingest\index_documents.py
} finally {
    Pop-Location
}
