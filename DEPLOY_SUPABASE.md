# Supabase database

Production data lives in Postgres with pgvector. Supabase is the host used for the public app. Any Postgres 16 instance with the `vector` extension can run the same schema.

## Create the project

1. Create a Supabase project in the region closest to Cloud Run. The public API runs in `asia-south1`.
2. Apply [backend/db/schema.sql](./backend/db/schema.sql).

The schema enables `vector` and `pg_trgm`, then creates `documents`, `chunks`, `faculty`, `programmes`, `conversations`, `messages`, and `feedback`.

`chunks.embedding` is `vector(384)`, which matches `BAAI/bge-small-en-v1.5`. Do not point a 768-dimensional Gemini index at this column. Run `prepare-gemini-index` only when you intend to switch the whole index.

Row Level Security is enabled and there are no anon policies. The Data API is locked. FastAPI connects with `DATABASE_URL`.

## Connection string

In the Supabase dashboard, open **Project Settings → Database** and copy a URI.

| Client | Which URI |
| --- | --- |
| Cloud Run | Transaction pooler, port `6543` |
| A machine that can reach the pooler | Session pooler, port `5432` |
| A one-off `pg_restore` | Direct host, port `5432` |

Cloud Run should not use the direct host. Put the chosen URI in `.env`:

```env
DATABASE_URL=postgresql://postgres.PROJECT_REF:PASSWORD@POOLER_HOST:6543/postgres?sslmode=require
```

Do not commit `.env` or the password.

## Load data

Build the index with the ingestion commands in the [README](./README.md), with `DATABASE_URL` pointed at Supabase.

To copy an existing local Docker database instead:

```bat
docker exec srmap-rag-db pg_dump -U srmap -d srmap_rag -Fc -f /tmp/srmap.dump
docker cp srmap-rag-db:/tmp/srmap.dump .\srmap.dump
```

Restore with `pg_restore` against the direct host, using `--no-owner --no-acl`. Do not commit the dump. Large restores should use the direct connection, not the pooler.

If the restore fights extensions or owners, dump data only for `documents`, `chunks`, `faculty`, `programmes`, `conversations`, `messages`, and `feedback`, then run that SQL in the Supabase SQL editor.

## Verify

```sql
SELECT COUNT(*) FROM documents;
SELECT COUNT(*) FROM chunks WHERE embed_status = 'completed';
```

The public index is about 3,635 documents and 21,466 completed chunks. The embedding column type is `vector(384)`.

Confirm from the API:

```text
GET /health/ready
```

`status` is `ok` when the database accepts `SELECT 1`. Document and chunk counts on `/health` refresh at most once per two minutes.
