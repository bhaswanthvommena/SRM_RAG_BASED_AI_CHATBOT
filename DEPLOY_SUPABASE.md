# Supabase database setup

## Project

| Field | Value |
|--------|--------|
| Name | `srmap-rag` |
| Ref | `lunscpeozscmgpwcprgs` |
| Region | `ap-south-1` (Mumbai) |
| Dashboard | https://supabase.com/dashboard/project/lunscpeozscmgpwcprgs |
| API URL | https://lunscpeozscmgpwcprgs.supabase.co |
| DB host | `db.lunscpeozscmgpwcprgs.supabase.co` |

## Schema (already applied)

- Extensions: `vector`, `pg_trgm`
- Tables: `documents`, `chunks`, `faculty`, `programmes`, `conversations`, `messages`, `feedback`
- `chunks.embedding` = **vector(384)** (matches local BGE)
- RLS enabled on all tables with **no** anon policies (Data API locked down; FastAPI uses the DB connection string)

## DATABASE_URL

1. Open [Database settings](https://supabase.com/dashboard/project/lunscpeozscmgpwcprgs/settings/database)
2. Copy the **URI** (or reset database password if you never set one)
3. Put it in local `.env`:

```env
DATABASE_URL=postgresql://postgres:[YOUR-PASSWORD]@db.lunscpeozscmgpwcprgs.supabase.co:5432/postgres
```

For serverless / HF later, the **Transaction pooler** URI (port `6543`) is often better:

```env
DATABASE_URL=postgresql://postgres.lunscpeozscmgpwcprgs:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:6543/postgres
```

Use SSL if required by the URI (`?sslmode=require`).

## Load your local data into Supabase

Your embeddings live in local Docker Postgres today. After `DATABASE_URL` points at Supabase, either:

### Option A — `pg_dump` / `pg_restore` (recommended)

```bat
 Rem Export from local Docker
docker exec srmap-rag-db pg_dump -U srmap -d srmap_rag -Fc -f /tmp/srmap.dump
docker cp srmap-rag-db:/tmp/srmap.dump .\srmap.dump

 Rem Import (set PGPASSWORD to your Supabase DB password)
set PGPASSWORD=YOUR_PASSWORD
pg_restore -h db.lunscpeozscmgpwcprgs.supabase.co -U postgres -d postgres --clean --if-exists --no-owner --no-acl .\srmap.dump
```

If `pg_restore` complains about extensions/owners, prefer dumping **data only** for the RAG tables:

```bat
docker exec srmap-rag-db pg_dump -U srmap -d srmap_rag -a --column-inserts ^
  -t documents -t chunks -t faculty -t programmes -t conversations -t messages -t feedback ^
  -f /tmp/srmap_data.sql
docker cp srmap-rag-db:/tmp/srmap_data.sql .\srmap_data.sql
```

Then run that SQL against Supabase (psql or SQL editor). Large dumps may need the direct connection (port 5432), not the pooler.

### Option B — re-persist + re-embed

Point `.env` at Supabase and re-run `persist` + `embed-worker` (slow; only if dump is painful).

## Verify

```sql
SELECT COUNT(*) FROM documents;
SELECT COUNT(*) FROM chunks WHERE embed_status = 'completed';
SELECT format_type(a.atttypid, a.atttypmod)
FROM pg_attribute a
JOIN pg_class c ON a.attrelid = c.oid
WHERE c.relname = 'chunks' AND a.attname = 'embedding';
```

Expect ~3635 documents, ~21466 completed chunks, type `vector(384)`.

## Next

Once data is loaded and local/API chat works against Supabase, proceed to Hugging Face Spaces (`DEPLOY_HF.md`) using the same `DATABASE_URL` as a Space secret.
