# Cloud Run backend

The production API is a container that runs FastAPI and local BGE-small. The chat UI stays on Vercel. The database stays on Supabase.

Current service: [srmap-rag-api](https://srmap-rag-api-1031641815896.asia-south1.run.app) in `asia-south1`.

`GET /health` returns the index size. Counts are cached for `HEALTH_CACHE_SECONDS` (default 120). A cold container can take a minute to answer the first request because the embedder loads on startup.

## Before deploy

1. Install the [Google Cloud SDK](https://cloud.google.com/sdk/docs/install) and sign in:

```bat
gcloud auth login
gcloud config set project YOUR_GCP_PROJECT
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

2. Put the Supabase **transaction pooler** URI (port `6543`) in `.env` as `DATABASE_URL`. Cloud Run often cannot reach the direct database host. Use the session pooler only from a machine that can open it. See [DEPLOY_SUPABASE.md](./DEPLOY_SUPABASE.md).

3. Set `GEMINI_CHAT_API_KEY` in `.env`. The deploy script sends it to Cloud Run. It is not baked into the image.

## Deploy

From the repo root:

```powershell
.\scripts\deploy_cloud_run.ps1 -Project YOUR_GCP_PROJECT
```

Defaults: service `srmap-rag-api`, region `asia-south1`, 2 GiB RAM, 1 CPU.

Initial safety settings, not a billing guarantee:

- concurrency `4`, because one CPU running BGE cannot serve the platform default of 80
- maximum instances `2`
- minimum instances `0`, so the first request after idle is slow
- request timeout `120` seconds

The first build downloads CPU torch and BGE-small. Expect 10–20 minutes.

The in-app rate limit is per instance, not global. The same IP can be under the limit on each instance at once. The script sets `TRUSTED_PROXY_HOPS=1` so the client IP is the address Cloud Run appends to `X-Forwarded-For`. Local runs should keep `TRUSTED_PROXY_HOPS=0`, which ignores that header.

Configure defaults in `.env`:

```env
RATE_LIMIT_PER_MINUTE=10
RATE_LIMIT_PER_HOUR=60
RATE_LIMIT_MAX_KEYS=10000
```

## After deploy

1. Open `https://SERVICE_URL/health/live`. This is process liveness and does not query the database. The image health check uses this path.
2. Open `https://SERVICE_URL/health/ready`. This runs `SELECT 1`. It returns 503 when the database is down, without the exception text. Row counts refresh only when the cache is stale.
3. Open `https://SERVICE_URL/health` for the same cached document and chunk counts.
4. In Vercel, set `NEXT_PUBLIC_API_URL` to the service URL and redeploy the frontend.
5. `https://*.vercel.app` is allowed by CORS. Set `CORS_ORIGINS` before deploying if you use a custom domain.

## Logs

Each chat writes one JSON line to stdout. Cloud Run sends it to Cloud Logging. The line includes the request id, a hash of the client IP, timings, HTTP status, intent, and citation count. It does not include the question text. Source URLs and chunk ids stay in the log body.

Use only these fields as metric labels:

- `route`
- `status_class` (`2xx`, `4xx`, `5xx`)
- `error_category` (`none`, `rate_limit`, `validation`, `retrieval`, `persistence`, `model_unavailable`, `generation_failed`, `client_disconnect`)
- `intent`

Do not use `request_id`, `ip_hash`, source URLs, or chunk ids as metric labels.

A model failure before the stream starts is an HTTP error. A failure after the stream has started is HTTP 200 plus an SSE `error` event that carries the request id and a short message. The UI keeps any text already shown and appends that message. Alert on `error_category`, not only on HTTP 5xx.

## Google Cloud setup outside this repo

The deploy script does not create a budget or a monitoring dashboard. Instance caps are not a spending cap.

Create a billing budget in Cloud Billing for the project. In Cloud Monitoring, build log-based metrics from the `labels` fields above:

- request count
- lines where `error_category` is not `none`
- distribution of `timings_ms.total_ms` and `timings_ms.ttft_ms`

Alert when 5xx or `model_unavailable` repeats, when `/health/ready` fails, or when the service stays at the maximum instance count. Use `/health/live` as the process uptime check and `/health/ready` as the database check.
