CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS product_image_embeddings (
    id BIGSERIAL PRIMARY KEY,

    product_image_id BIGINT NOT NULL
        REFERENCES product_images(id)
        ON DELETE CASCADE,

    model_name VARCHAR(100) NOT NULL,
    pretrained_name VARCHAR(150) NOT NULL,

    embedding vector(512) NOT NULL,

    image_checksum VARCHAR(64) NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (
        product_image_id,
        model_name,
        pretrained_name
    )
);

CREATE INDEX IF NOT EXISTS idx_product_image_embeddings_hnsw
ON product_image_embeddings
USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_product_image_embedding_image
ON product_image_embeddings(product_image_id);