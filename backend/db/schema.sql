-- SRM AP RAG schema: documents, chunks (pgvector + FTS), structured entities, chat

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Source documents (one row per page / PDF)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id              BIGSERIAL PRIMARY KEY,
    url             TEXT NOT NULL,
    canonical_url   TEXT,
    wp_type         TEXT NOT NULL DEFAULT 'page',
    wp_id           BIGINT,
    title           TEXT,
    school          TEXT,
    department      TEXT,
    doc_type        TEXT NOT NULL DEFAULT 'page',
    published_at    TIMESTAMPTZ,
    modified_at     TIMESTAMPTZ,
    content_hash    TEXT,
    source          TEXT NOT NULL DEFAULT 'html',  -- html | pdf
    is_stale        BOOLEAN NOT NULL DEFAULT FALSE,
    crawled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (url)
);

CREATE INDEX IF NOT EXISTS idx_documents_wp_type ON documents (wp_type);
CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents (doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_modified ON documents (modified_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_documents_stale ON documents (is_stale) WHERE is_stale = TRUE;

-- ---------------------------------------------------------------------------
-- Chunks with hybrid retrieval indexes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    ord             INT NOT NULL DEFAULT 0,
    heading_path    TEXT,
    text            TEXT NOT NULL,
    token_count     INT,
    content_hash    TEXT,
    -- Default Gemini 768-d; run prepare-local-index to switch to BGE 384-d
    embedding       vector(768),
    embed_model     TEXT,
    embed_status    TEXT NOT NULL DEFAULT 'pending'
                    CHECK (embed_status IN ('pending', 'processing', 'completed', 'failed')),
    embed_attempts  INT NOT NULL DEFAULT 0,
    embed_error     TEXT,
    embedded_at     TIMESTAMPTZ,
    embed_locked_at TIMESTAMPTZ,
    tsv             tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', coalesce(heading_path, '') || ' ' || coalesce(text, ''))
                    ) STORED,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (document_id, ord)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS idx_chunks_text_trgm ON chunks USING GIN (text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_embed_pending
    ON chunks (embed_status, id)
    WHERE embed_status IN ('pending', 'failed');
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash
    ON chunks (document_id, content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_embed_model
    ON chunks (embed_model)
    WHERE embedding IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Structured entities (exact lookups)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS faculty (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    designation     TEXT,
    department      TEXT,
    school          TEXT,
    email           TEXT,
    research_areas  TEXT[],
    url             TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_faculty_name_trgm ON faculty USING GIN (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_faculty_department ON faculty (department);

CREATE TABLE IF NOT EXISTS programmes (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    school          TEXT,
    department      TEXT,
    level           TEXT,  -- ug | pg | phd | other
    eligibility     TEXT,
    duration        TEXT,
    fee_summary     TEXT,
    url             TEXT,
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_programmes_name_trgm ON programmes USING GIN (name gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Chat
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS messages (
    id              BIGSERIAL PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content         TEXT NOT NULL,
    retrieved_chunk_ids BIGINT[] DEFAULT '{}',
    citations       JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS feedback (
    id              BIGSERIAL PRIMARY KEY,
    message_id      BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    rating          SMALLINT NOT NULL CHECK (rating IN (-1, 1)),
    comment         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

