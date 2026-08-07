from typing import Any

from pgvector import Vector  # type: ignore
from psycopg.types.json import Jsonb  # type: ignore

from app.database.connection import database_connection


class KnowledgeRepository:
    def document_state(self, source_key: str) -> dict[str, Any] | None:
        with database_connection() as connection:
            row = connection.execute(
                """
                SELECT source_checksum, embedding_provider, embedding_model,
                       embedding_dimension, is_active
                FROM knowledge_documents
                WHERE source_key = %s
                """,
                (source_key,),
            ).fetchone()
        return dict(row) if row else None

    def replace_document(
        self,
        *,
        source_key: str,
        title: str,
        category: str,
        source_checksum: str,
        embedding_provider: str,
        embedding_model: str,
        embedding_dimension: int,
        metadata: dict[str, Any],
        chunks: list[dict[str, Any]],
    ) -> int:
        with database_connection() as connection:
            row = connection.execute(
                """
                INSERT INTO knowledge_documents(
                    source_key, title, category, source_checksum,
                    embedding_provider, embedding_model,
                    embedding_dimension, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (source_key) DO UPDATE SET
                    title = EXCLUDED.title,
                    category = EXCLUDED.category,
                    source_checksum = EXCLUDED.source_checksum,
                    embedding_provider = EXCLUDED.embedding_provider,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding_dimension = EXCLUDED.embedding_dimension,
                    metadata = EXCLUDED.metadata,
                    is_active = TRUE,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    source_key,
                    title,
                    category,
                    source_checksum,
                    embedding_provider,
                    embedding_model,
                    embedding_dimension,
                    Jsonb(metadata),
                ),
            ).fetchone()
            if not row:
                raise RuntimeError("Không thể lưu tài liệu kiến thức")

            document_id = int(row["id"])
            connection.execute(
                "DELETE FROM knowledge_chunks WHERE document_id = %s",
                (document_id,),
            )

            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks(
                        document_id, chunk_index, heading, content,
                        content_checksum, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        chunk["chunk_index"],
                        chunk.get("heading"),
                        chunk["content"],
                        chunk["content_checksum"],
                        Vector(chunk["embedding"]),
                    ),
                )
        return document_id

    def search(
        self,
        *,
        embedding: list[float],
        embedding_provider: str,
        embedding_model: str,
        embedding_dimension: int,
        categories: list[str] | None,
        min_similarity: float,
        limit: int,
    ) -> list[dict[str, Any]]:
        conditions = [
            "kd.is_active = TRUE",
            "kd.embedding_provider = %s",
            "kd.embedding_model = %s",
            "kd.embedding_dimension = %s",
        ]
        parameters: list[Any] = [
            Vector(embedding),
            embedding_provider,
            embedding_model,
            embedding_dimension,
        ]

        if categories:
            conditions.append("kd.category = ANY(%s)")
            parameters.append(categories)

        parameters.extend([min_similarity, limit])
        query = f"""
            WITH ranked AS (
                SELECT
                    kd.source_key,
                    kd.title,
                    kd.category,
                    kd.metadata,
                    kc.chunk_index,
                    kc.heading,
                    kc.content,
                    1 - (kc.embedding <=> %s) AS similarity
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd
                    ON kd.id = kc.document_id
                WHERE {" AND ".join(conditions)}
            )
            SELECT *
            FROM ranked
            WHERE similarity >= %s
            ORDER BY similarity DESC
            LIMIT %s
        """

        with database_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]
