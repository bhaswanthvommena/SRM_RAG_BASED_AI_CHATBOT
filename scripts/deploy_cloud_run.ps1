# Deploy the FastAPI + BGE backend to Cloud Run.
# Requires: gcloud CLI, an authenticated GCP project, and .env with
# DATABASE_URL (Supabase transaction pooler) and GEMINI_CHAT_API_KEY.
#
#   .\scripts\deploy_cloud_run.ps1 -Project YOUR_GCP_PROJECT

param(
    [string]$Project = "",
    [string]$Region = "asia-south1",
    [string]$Service = "srmap-rag-api",
    [string]$CorsOrigin = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Host "gcloud is not installed. Install the Google Cloud SDK, then run: gcloud auth login" -ForegroundColor Red
    exit 1
}

if (-not $Project) {
    $Project = (gcloud config get-value project 2>$null).Trim()
}
if (-not $Project -or $Project -eq "(unset)") {
    Write-Host "Pass -Project YOUR_GCP_PROJECT or run: gcloud config set project YOUR_GCP_PROJECT" -ForegroundColor Red
    exit 1
}

function Get-DotEnv([string]$Key) {
    $line = Get-Content (Join-Path $Root ".env") | Where-Object { $_ -match "^$Key=" } | Select-Object -First 1
    if (-not $line) { return "" }
    return ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
}

$db = Get-DotEnv "DATABASE_URL"
$chat = Get-DotEnv "GEMINI_CHAT_API_KEY"
if (-not $chat) { $chat = Get-DotEnv "GEMINI_API_KEY" }
$cors = if ($CorsOrigin) { $CorsOrigin } else { Get-DotEnv "CORS_ORIGINS" }
if (-not $cors) { $cors = "http://localhost:3000" }

if (-not $db -or $db -match "localhost|127\.0\.0\.1") {
    Write-Host "DATABASE_URL must be the Supabase pooler URI, not localhost." -ForegroundColor Red
    exit 1
}
if ($db -match ":5432" -and $db -match "pooler\.supabase\.com") {
    Write-Host "DATABASE_URL looks like the session pooler (port 5432)." -ForegroundColor Yellow
    Write-Host "Cloud Run should use the transaction pooler (port 6543)." -ForegroundColor Yellow
}
if ($db -match "db\.lunscpeozscmgpwcprgs\.supabase\.co") {
    Write-Host "DATABASE_URL is the direct host. Cloud Run often cannot reach it (IPv6)." -ForegroundColor Yellow
    Write-Host "Switch to the transaction pooler URI before deploying." -ForegroundColor Yellow
}
if (-not $chat) {
    Write-Host "GEMINI_CHAT_API_KEY is missing in .env" -ForegroundColor Red
    exit 1
}

$yaml = Join-Path $env:TEMP "srmap-cloud-run-env.yaml"
function YamlQuote([string]$Value) {
    return "'" + ($Value -replace "'", "''") + "'"
}
@(
    "DATABASE_URL: $(YamlQuote $db)",
    "GEMINI_CHAT_API_KEY: $(YamlQuote $chat)",
    "EMBEDDING_PROVIDER: 'local'",
    "EMBEDDING_DIMS: '384'",
    "LOCAL_EMBEDDING_MODEL: 'BAAI/bge-small-en-v1.5'",
    "GEMINI_CHAT_MODEL: 'gemini-3.5-flash-lite'",
    "CORS_ORIGINS: $(YamlQuote $cors)",
    "TRUSTED_PROXY_HOPS: '1'"
) | Set-Content -Path $yaml -Encoding ascii

Write-Host "Deploying $Service to $Region in project $Project"
Write-Host "DB host only: $(([uri]$db).Host)"
Write-Host "Instance caps are initial safety settings, not a billing guarantee."
Write-Host "Create a Cloud Billing budget alert separately. This script does not create one."

try {
    gcloud run deploy $Service `
        --source . `
        --project $Project `
        --region $Region `
        --allow-unauthenticated `
        --memory 2Gi `
        --cpu 1 `
        --timeout 120 `
        --concurrency 4 `
        --max-instances 2 `
        --min-instances 0 `
        --cpu-boost `
        --port 8080 `
        --quiet `
        --env-vars-file $yaml
} finally {
    Remove-Item $yaml -Force -ErrorAction SilentlyContinue
}
