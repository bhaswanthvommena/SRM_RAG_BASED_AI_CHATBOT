# Deploy notes (post-MVP)

## Database

- Local: `docker compose up -d` (pgvector/pg16).
- Hosted: [Neon](https://neon.tech) or [Supabase](https://supabase.com) with the `vector` extension enabled.
- Apply `backend/db/schema.sql` once, then set `DATABASE_URL`.

## Backend

- Run with `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- Suitable hosts: Render, Fly.io, Google Cloud Run.
- Set env: `DATABASE_URL`, `GEMINI_API_KEY`, `CORS_ORIGINS` (include your frontend origin).

## Frontend

- Deploy `frontend/` to Vercel.
- Set `NEXT_PUBLIC_API_URL` to the public API URL.

## Incremental refresh

```bash
python -m ingestion.cli discover
python -m ingestion.cli fetch          # skips cached unchanged files unless --force
python -m ingestion.cli parse
python -m ingestion.cli pdfs --limit 50
python -m ingestion.cli load
```

Schedule nightly via GitHub Actions or a cron container. Later: WordPress publish webhook → re-fetch single URL.

## Website widget (later)

- Expose `/widget` or load the chat UI in an iframe.
- Add API key / origin allowlist and per-IP rate limits on `/chat/stream`.
