# Quick helpers for Windows PowerShell
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$env:PYTHONPATH = $Root

Write-Host "PYTHONPATH=$env:PYTHONPATH"
Write-Host "Commands:"
Write-Host "  docker compose up -d"
Write-Host "  python -m ingestion.cli discover"
Write-Host "  python -m ingestion.cli fetch --limit 30"
Write-Host "  cd backend; uvicorn app.main:app --reload --port 8000"
Write-Host "  cd frontend; npm run dev"
