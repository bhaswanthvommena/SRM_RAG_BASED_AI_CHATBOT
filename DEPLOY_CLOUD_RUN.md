# Cloud Run backend

Frontend stays on Vercel. Database stays on Supabase. This image runs FastAPI plus local BGE-small.

## Before deploy

1. Install the [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) and sign in:

```bat
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

2. In Supabase, copy the **Transaction pooler** URI (port `6543`). Put that in `.env` as `DATABASE_URL` for this deploy. Cloud Run cannot use the IPv6 direct host. Session pooler works for your laptop, but the transaction pooler is the one to give Cloud Run.

3. `GEMINI_CHAT_API_KEY` must be set in `.env`. It is passed to Cloud Run and is not baked into the image.

## Deploy

From the repo root:

```powershell
.\scripts\deploy_cloud_run.ps1 -Project YOUR_GCP_PROJECT
```

Defaults: service `srmap-rag-api`, region `asia-south1` (Mumbai, next to the Supabase project), 2 GiB RAM, 1 CPU, 300s timeout.

The first build downloads CPU torch and BGE-small, so expect 10–20 minutes.

## After deploy

1. Open `https://SERVICE-URL/health`. Documents and completed chunks should be non-zero.
2. In Vercel, set `NEXT_PUBLIC_API_URL` to that Cloud Run URL and redeploy the frontend.
3. `*.vercel.app` is already allowed by CORS. Set `CORS_ORIGINS` in `.env` to your real Vercel URL before deploying if you use a custom domain.

## Cloud Run MCP

Google's remote MCP is `https://run.googleapis.com/mcp`. It is registered in Cursor as `cloud-run`. After you reload MCP servers and sign in with Google, it can list services and deploy an image that is already in Artifact Registry. It cannot build this image by itself: the torch wheel is too large for the inline-source deploy tool. The script above uses Cloud Build, which is the right path for this app.
