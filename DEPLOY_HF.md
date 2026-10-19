# Hugging Face Spaces

The public API runs on Cloud Run. See [DEPLOY_CLOUD_RUN.md](./DEPLOY_CLOUD_RUN.md). This page is only for running the same image on a Hugging Face Docker Space.

A Space does not include Postgres. Point `DATABASE_URL` at Supabase or another hosted Postgres with pgvector, using the setup in [DEPLOY_SUPABASE.md](./DEPLOY_SUPABASE.md).

## Image

The [Dockerfile](./Dockerfile) installs CPU torch, prefetches `BAAI/bge-small-en-v1.5`, and listens on `PORT`. The default is `7860`, which Spaces expect. Cloud Run overrides `PORT`.

Python packages for the image are in [backend/requirements-space.txt](./backend/requirements-space.txt). Torch is installed separately so the CPU wheel is used.

## Space settings

Secrets:

| Name | Value |
| --- | --- |
| `DATABASE_URL` | Transaction pooler URI for the hosted database |
| `GEMINI_CHAT_API_KEY` | Chat key from Google AI Studio |
| `CORS_ORIGINS` | The Vercel origin, if you use a custom domain |

Variables:

| Name | Value |
| --- | --- |
| `EMBEDDING_PROVIDER` | `local` |
| `LOCAL_EMBEDDING_MODEL` | `BAAI/bge-small-en-v1.5` |
| `EMBEDDING_DIMS` | `384` |
| `GEMINI_CHAT_MODEL` | `gemini-3.5-flash-lite` |
| `TRUSTED_PROXY_HOPS` | `0`, unless you know the Space proxy appends the client IP |

`GEMINI_EMBEDDING_API_KEY` is unused while the index stays on local BGE.

## Deploy

1. Create a Docker Space with enough CPU memory for torch and BGE-small. A free static Space cannot run this API.
2. Set the secrets and variables above. Do not put them in the repo.
3. Push the image or connect the GitHub repo.
4. Wait for the first build. It downloads torch and the embedding model.
5. Check `GET /health/live`, then `GET /health/ready`.
6. Set `NEXT_PUBLIC_API_URL` on Vercel to the Space URL and redeploy the frontend.

An idle Space sleeps. The first request after sleep is slow even though the model is already in the image.

Do not commit `.env`, API keys, or a database dump.
