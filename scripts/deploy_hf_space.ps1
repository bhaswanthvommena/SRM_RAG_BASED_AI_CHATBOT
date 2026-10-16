# Deploy FastAPI backend to Hugging Face Spaces (Docker).
# Prerequisites:
#   1) hf auth login   (write token)  OR  $env:HF_TOKEN = "hf_..."
#   2) Supabase DATABASE_URL in .env (not localhost)
#
# Usage (from repo root):
#   .\scripts\deploy_hf_space.ps1
#   .\scripts\deploy_hf_space.ps1 -SpaceName srm-rag-api -Private

param(
    [string]$SpaceName = "srm-rag-api",
    [string]$Namespace = "bhaswanthv",
    [switch]$Private
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$venvHf = Join-Path $Root ".venv\Scripts\hf.exe"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvHf)) {
    & (Join-Path $Root ".venv\Scripts\pip.exe") install "huggingface-hub>=1.3.0,<2.0.0" -q
}

function Get-DotEnvValue([string]$Key) {
    $line = Get-Content (Join-Path $Root ".env") | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return ($line -split "=", 2)[1].Trim()
}

$db = Get-DotEnvValue "DATABASE_URL"
$chatKey = Get-DotEnvValue "GEMINI_CHAT_API_KEY"
if (-not $chatKey) { $chatKey = Get-DotEnvValue "GEMINI_API_KEY" }
$cors = Get-DotEnvValue "CORS_ORIGINS"
if (-not $cors) { $cors = "http://localhost:3000" }

if ($db -match "localhost|127\.0\.0\.1") {
    Write-Host "ERROR: DATABASE_URL still points at localhost. HF cannot reach your laptop." -ForegroundColor Red
    Write-Host "Set DATABASE_URL to your Supabase URI in .env, then re-run." -ForegroundColor Yellow
    exit 1
}
if (-not $chatKey) {
    Write-Host "ERROR: GEMINI_CHAT_API_KEY (or GEMINI_API_KEY) missing in .env" -ForegroundColor Red
    exit 1
}

$repoId = "$Namespace/$SpaceName"
Write-Host "Creating/updating Space: $repoId (docker, cpu-basic)"

$visibility = if ($Private) { "--private" } else { "--public" }

& $venvHf repos create $repoId --type space --space-sdk docker --exist-ok $visibility
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# Hardware: cpu-basic (free)
& $venvHf spaces hardware $repoId cpu-basic 2>$null

Write-Host "Setting secrets..."
& $venvHf spaces secret set $repoId DATABASE_URL $db
& $venvHf spaces secret set $repoId GEMINI_CHAT_API_KEY $chatKey

Write-Host "Setting variables..."
& $venvHf spaces variable set $repoId EMBEDDING_PROVIDER local
& $venvHf spaces variable set $repoId EMBEDDING_DIMS 384
& $venvHf spaces variable set $repoId LOCAL_EMBEDDING_MODEL "BAAI/bge-small-en-v1.5"
& $venvHf spaces variable set $repoId GEMINI_CHAT_MODEL "gemini-3.5-flash-lite"
& $venvHf spaces variable set $repoId CORS_ORIGINS $cors

Write-Host "Uploading files..."
$files = @(
    "Dockerfile",
    ".dockerignore",
    "README.md",
    "backend"
)
foreach ($f in $files) {
    & $venvHf upload $repoId (Join-Path $Root $f) --repo-type space
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host ""
Write-Host "Done. Space: https://huggingface.co/spaces/$repoId" -ForegroundColor Green
Write-Host "API base (after build): https://$Namespace-$SpaceName.hf.space"
Write-Host "Set Vercel NEXT_PUBLIC_API_URL to that URL when RUNNING."
Write-Host "First Docker build can take 10-20+ minutes."
