BEGIN;

CREATE TABLE IF NOT EXISTS catalog_sync_runs (
    id BIGSERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    status VARCHAR(30) NOT NULL,
    total_records INTEGER NOT NULL DEFAULT 0,
    inserted_records INTEGER NOT NULL DEFAULT 0,
    updated_records INTEGER NOT NULL DEFAULT 0,
    failed_records INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS product_source_payloads (
    id BIGSERIAL PRIMARY KEY,
    sync_run_id BIGINT REFERENCES catalog_sync_runs(id) ON DELETE SET NULL,
    external_product_id VARCHAR(180),
    product_code VARCHAR(50),
    payload JSONB NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(external_product_id, payload_hash)
);

CREATE TABLE IF NOT EXISTS products (
    id BIGSERIAL PRIMARY KEY,
    product_code VARCHAR(50) NOT NULL UNIQUE,
    title VARCHAR(500) NOT NULL,
    handle VARCHAR(300),
    vendor VARCHAR(200),
    product_type VARCHAR(200),
    description TEXT,
    material VARCHAR(300),
    sole VARCHAR(300),
    height VARCHAR(200),
    status VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
    online_store_url TEXT,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS product_variants (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    external_id VARCHAR(180),
    legacy_id BIGINT,
    sku VARCHAR(100) NOT NULL,
    barcode VARCHAR(100),
    variant_title VARCHAR(300),
    color VARCHAR(100),
    color_normalized VARCHAR(100) NOT NULL DEFAULT '',
    size VARCHAR(100) NOT NULL DEFAULT '',
    price NUMERIC(14, 2),
    compare_at_price NUMERIC(14, 2),
    inventory_quantity INTEGER NOT NULL DEFAULT 0,
    available BOOLEAN NOT NULL DEFAULT FALSE,
    source_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(product_id, sku, color_normalized, size)
);

CREATE TABLE IF NOT EXISTS product_images (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    variant_id BIGINT REFERENCES product_variants(id) ON DELETE SET NULL,
    external_id VARCHAR(180),
    color VARCHAR(100),
    color_normalized VARCHAR(100) NOT NULL DEFAULT '',
    source_url TEXT,
    local_path TEXT,
    alt_text TEXT,
    mime_type VARCHAR(100),
    width INTEGER,
    height INTEGER,
    image_order INTEGER NOT NULL DEFAULT 0,
    is_featured BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    checksum VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(product_id, source_url, color_normalized)
);

CREATE TABLE IF NOT EXISTS product_attributes (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    attribute_key VARCHAR(100) NOT NULL,
    attribute_value TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(product_id, attribute_key)
);

CREATE TABLE IF NOT EXISTS product_colors (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    color VARCHAR(100) NOT NULL,
    color_normalized VARCHAR(100) NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'catalog',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(product_id, color_normalized)
);

CREATE TABLE IF NOT EXISTS product_aliases (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    alias VARCHAR(300) NOT NULL,
    alias_normalized VARCHAR(300) NOT NULL,
    alias_type VARCHAR(50) NOT NULL DEFAULT 'keyword',
    UNIQUE(product_id, alias_normalized, alias_type)
);

CREATE INDEX IF NOT EXISTS idx_products_type_status
    ON products(product_type, status);
CREATE INDEX IF NOT EXISTS idx_variants_product
    ON product_variants(product_id);
CREATE INDEX IF NOT EXISTS idx_variants_sku
    ON product_variants(sku);
CREATE INDEX IF NOT EXISTS idx_variants_product_color
    ON product_variants(product_id, color_normalized);
CREATE INDEX IF NOT EXISTS idx_images_product_color
    ON product_images(product_id, color_normalized, image_order);
CREATE INDEX IF NOT EXISTS idx_product_colors_product
    ON product_colors(product_id, color_normalized);
CREATE INDEX IF NOT EXISTS idx_aliases_normalized
    ON product_aliases(alias_normalized);

COMMIT;
