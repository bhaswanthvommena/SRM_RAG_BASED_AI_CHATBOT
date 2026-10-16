# Hugging Face Spaces (Docker) — backend deploy checklist

Prepared and ready. **Do not deploy until you say go.**

## Architecture

| Piece | Host |
|--------|------|
| Next.js UI | Vercel (done) |
| FastAPI + local BGE | Hugging Face Space (Docker, `cpu-basic`) |
| Postgres + pgvector | Hosted DB (Supabase / Neon / other) — **required** |
| Gemini chat | Google AI Studio key (Space secret) |

Space URL shape after deploy:

`https://bhaswanthv-srm-rag-api.hf.space`  
(exact name chosen at create time)

Then set Vercel env:

`NEXT_PUBLIC_API_URL=https://<your-space>.hf.space`

## Repo files ready

- `Dockerfile` — CPU torch, prefetches `BAAI/bge-small-en-v1.5`, listens on **7860**
- `.dockerignore`
- `backend/requirements-space.txt`
- README YAML frontmatter (`sdk: docker`, `app_port: 7860`)
- API warmup for local embedder + `/` + `/health`

## Secrets to set on the Space (Settings → Secrets)

| Secret | Value |
|--------|--------|
| `DATABASE_URL` | Hosted Postgres URL with `pgvector` (must already contain your crawled data / embeddings) |
| `GEMINI_CHAT_API_KEY` | Chat account key |
| `GEMINI_EMBEDDING_API_KEY` | Optional if using Gemini embeds; not needed while on local BGE |
| `CORS_ORIGINS` | Your Vercel URL, e.g. `https://your-app.vercel.app` |

## Variables (non-secret) on the Space

| Variable | Suggested value |
|----------|-----------------|
| `EMBEDDING_PROVIDER` | `local` |
| `LOCAL_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` |
| `EMBEDDING_DIMS` | `384` |
| `GEMINI_CHAT_MODEL` | `gemini-3.5-flash-lite` |

## Before deploy — database

Hugging Face Spaces **do not** host Postgres. Use the Supabase project already created:

- See **[DEPLOY_SUPABASE.md](./DEPLOY_SUPABASE.md)** (`srmap-rag` / `lunscpeozscmgpwcprgs`)
- Schema is applied (BGE **384-d**); you still need to **load data** from local Docker
- Put the same `DATABASE_URL` in Space secrets

Until data is loaded, the Space will boot but chat retrieval will be empty / degraded.

## Deploy steps (when you say proceed)

1. Confirm HF login (`hf auth whoami` / plugin)
2. Create Docker Space (private or public), hardware `cpu-basic`
3. Set secrets + variables above
4. Push this GitHub repo to the Space (or connect GitHub → Space)
5. Wait for build (first build downloads torch + BGE — several minutes)
6. Smoke test: `GET /health`, then one chat from Vercel
7. Set `NEXT_PUBLIC_API_URL` on Vercel and redeploy frontend

## Notes

- Free Spaces **sleep** when idle; first request after sleep can take 1–2+ minutes (model already in image, but process cold-starts).
- `cpu-basic` is enough for BGE-small query embedding + FastAPI.
- Do **not** commit `.env` or real API keys.
- Local `docker compose` Postgres remains for development only.
