-- Support switching embedding models without mixing vector spaces.
-- Local BGE uses 384 dims; Gemini used 768. prepare-local-index applies the cutover.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS embed_model TEXT;

-- Tag existing completed 768-d vectors as Gemini (inferred from dims + project history).
UPDATE chunks
SET embed_model = 'gemini-embedding-001'
WHERE embedding IS NOT NULL
  AND embed_model IS NULL
  AND vector_dims(embedding) = 768;
