-- Decouple crawl/persist from embedding: queue status on chunks
-- Safe to run on existing DBs (IF NOT EXISTS / additive).

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS content_hash TEXT,
    ADD COLUMN IF NOT EXISTS embed_status TEXT NOT NULL DEFAULT 'pending',
    ADD COLUMN IF NOT EXISTS embed_attempts INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embed_error TEXT,
    ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS embed_locked_at TIMESTAMPTZ;

-- Backfill: rows that already have embeddings are completed
UPDATE chunks
SET embed_status = 'completed',
    embedded_at = COALESCE(embedded_at, NOW())
WHERE embedding IS NOT NULL
  AND embed_status = 'pending';

-- Rows with null embedding stay pending (default)

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chunks_embed_status_check'
    ) THEN
        ALTER TABLE chunks
            ADD CONSTRAINT chunks_embed_status_check
            CHECK (embed_status IN ('pending', 'processing', 'completed', 'failed'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_chunks_embed_pending
    ON chunks (embed_status, id)
    WHERE embed_status IN ('pending', 'failed');

CREATE INDEX IF NOT EXISTS idx_chunks_content_hash
    ON chunks (document_id, content_hash);
