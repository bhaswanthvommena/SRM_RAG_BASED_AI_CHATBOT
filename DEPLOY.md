# Deployment

The public app is three pieces:

| Piece | Host | What it runs |
| --- | --- | --- |
| Chat UI | Vercel | Next.js app in `frontend/` |
| API | Google Cloud Run | FastAPI plus the local BGE embedder |
| Database | Supabase | Postgres 16 with pgvector |

Guides: [DEPLOY_CLOUD_RUN.md](./DEPLOY_CLOUD_RUN.md), [DEPLOY_SUPABASE.md](./DEPLOY_SUPABASE.md). An optional Hugging Face path is in [DEPLOY_HF.md](./DEPLOY_HF.md).

## Frontend

Deploy the `frontend/` directory to Vercel. Set one environment variable and redeploy:

```env
NEXT_PUBLIC_API_URL=https://YOUR_CLOUD_RUN_URL
```

That value is public. It is baked into the browser bundle at build time. Do not put the database URL or the Gemini key in the frontend project.

Pushing `main` redeploys the connected Vercel project.

## Backend

From the repo root, with `gcloud` authenticated and `.env` filled in:

```powershell
.\scripts\deploy_cloud_run.ps1 -Project YOUR_GCP_PROJECT
```

The script reads `DATABASE_URL` and `GEMINI_CHAT_API_KEY` from `.env` and passes them to Cloud Run. They are not written into the image. See [DEPLOY_CLOUD_RUN.md](./DEPLOY_CLOUD_RUN.md) for limits, health checks, and logs.

## Database

Apply `backend/db/schema.sql` on a Postgres instance with the `vector` extension, then load documents and chunks. The production index is 384-dimensional BGE. Details are in [DEPLOY_SUPABASE.md](./DEPLOY_SUPABASE.md).

Local development uses `docker compose up -d`.

## Refresh the index

Crawl and embedding are separate. A refresh does not delete stored text.

```bash
python -m ingestion.cli discover
python -m ingestion.cli fetch
python -m ingestion.cli parse
python -m ingestion.cli pdfs
python -m ingestion.cli persist
python -m ingestion.cli embed-worker --batch-size 16 --max-per-run 10000
```

`fetch` skips unchanged cached pages unless you pass `--force`. There is no scheduler in this repo. Run the commands when the site should be recrawled.

## Secrets

Keep `.env` out of git. Never commit API keys, database passwords, or a database dump.
