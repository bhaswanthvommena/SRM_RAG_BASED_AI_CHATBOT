---
title: SRM AP RAG API
emoji: 🎓
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: FastAPI RAG backend for SRM University-AP campus chat
---

# SRM University-AP RAG Chatbot

Standalone web chat app that answers questions about [srmap.edu.in](https://www.srmap.edu.in/) using retrieval-augmented generation (RAG).

**Stack:** Python ingestion + FastAPI backend + Next.js frontend + Postgres/pgvector + Google Gemini.

**Deploy:** Frontend on Vercel · Backend Docker on Hugging Face Spaces · DB on hosted Postgres/pgvector. See [DEPLOY_HF.md](./DEPLOY_HF.md).

## Quick start

### 1. Environment

```bash
cp .env.example .env
# Add GEMINI_API_KEY from https://aistudio.google.com/apikey
```

### 2. Database

```bash
docker compose up -d
```

### 3. Python deps

From the repo root:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r ingestion/requirements.txt -r backend/requirements.txt
```

Set `PYTHONPATH` to the repo root when running modules:

```bash
# Windows PowerShell
$env:PYTHONPATH = (Get-Location).Path

# bash
export PYTHONPATH=$PWD
```

### 4. Ingest the website

```bash
# Discover all MVP URLs (REST + sitemaps) → data/manifest.jsonl
python -m ingestion.cli discover

# Fetch a small pilot first (recommended)
python -m ingestion.cli fetch --limit 50 --doc-type programme --doc-type page

# Parse HTML → data/parsed/
python -m ingestion.cli parse --limit 50

# Optional: priority PDFs
python -m ingestion.cli pdfs --limit 20

# Persist documents + text chunks to Postgres (NO embedding API — safe anytime)
python -m ingestion.cli persist

# Check crawl / embed-queue counts
python -m ingestion.cli status

# --- API keys (prefer separate accounts to split quota) ---
# GEMINI_CHAT_API_KEY=...        # chat / rewrite
# GEMINI_EMBEDDING_API_KEY=...   # embed-worker + query embeddings

# --- Local embeddings (BAAI/bge-small-en-v1.5, 384-d) ---
python -m ingestion.cli prepare-local-index
# .env: EMBEDDING_PROVIDER=local, EMBEDDING_DIMS=384
python -m ingestion.cli embed-worker --batch-size 8 --max-per-run 500

# --- Hosted Gemini embeddings (768-d) ---
python -m ingestion.cli prepare-gemini-index
# .env: EMBEDDING_PROVIDER=gemini, EMBEDDING_DIMS=768, GEMINI_EMBEDDING_API_KEY=...
python -m ingestion.cli embed-worker --batch-size 8 --max-per-run 500
```

Crawl and embedding are **decoupled**. Never mix Gemini (768-d) and local BGE (384-d) in the same index — use `prepare-local-index` / `prepare-gemini-index` when switching.

### 5. Run API + UI

```bash
# Terminal A — API
cd backend
uvicorn app.main:app --reload --port 8000

# Terminal B — UI
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

## Project layout

```
ingestion/     discover → fetch → parse → pdfs → persist | embed-worker
backend/       FastAPI hybrid retrieval + streaming chat
frontend/      Next.js chat UI with citations
eval/          Golden questions
data/          Raw HTML cache, parsed JSON, PDFs (gitignored)
```

## MVP corpus

Indexed: pages, programmes, faculty, staff, leadership, news, press releases, placements, and priority PDFs.

Deferred: publications (12.9k), patents, projects, taxonomy archives.

## Notes

- Polite crawl: ~1.5 req/s, browser-like User-Agent, gzip HTML cache.
- Hybrid retrieval: pgvector + Postgres full-text + reciprocal rank fusion + intent boosts.
- Answers include source citations and “as of” dates for fees/admissions content.
- **Content quirks on srmap.edu.in:** many `programme` CPT pages are Elementor shells with little body text (details live on school pages/PDFs). Faculty/news pages extract well. Some PDFs (hostel floor plans) are image-only and need OCR later.
- Start Docker Desktop before `docker compose up -d`.
- `GEMINI_API_KEY` is only required for `embed-worker` and chat — not for crawl/persist.
