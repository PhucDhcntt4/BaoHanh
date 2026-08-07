BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id BIGSERIAL PRIMARY KEY,
    source_key VARCHAR(500) NOT NULL UNIQUE,
    title VARCHAR(500) NOT NULL,
    category VARCHAR(100) NOT NULL DEFAULT 'customer_care',
    source_checksum VARCHAR(64) NOT NULL,
    embedding_provider VARCHAR(50) NOT NULL DEFAULT 'gemini',
    embedding_model VARCHAR(150) NOT NULL,
    embedding_dimension INTEGER NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE knowledge_documents
ADD COLUMN IF NOT EXISTS embedding_provider VARCHAR(50)
NOT NULL DEFAULT 'gemini';

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL
        REFERENCES knowledge_documents(id)
        ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    heading VARCHAR(500),
    content TEXT NOT NULL,
    content_checksum VARCHAR(64) NOT NULL,
    embedding vector(768) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_documents_category
ON knowledge_documents(category, is_active);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_document
ON knowledge_chunks(document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding_hnsw
ON knowledge_chunks
USING hnsw (embedding vector_cosine_ops);

COMMIT;
