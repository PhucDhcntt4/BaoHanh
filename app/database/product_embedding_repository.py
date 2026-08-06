from typing import Any

from pgvector import Vector # type: ignore

from app.database.connection import database_connection

def group_product_candidates(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str],
        dict[str, Any],
    ] = {}

    for row in rows:
        product_code = str(
            row.get("product_code") or ""
        ).strip().upper()

        color = str(
            row.get("color") or ""
        ).strip()

        if not product_code:
            continue

        key = (
            product_code,
            color.casefold(),
        )

        current = grouped.get(key)

        if (
            current is None
            or float(row["similarity"])
            > float(current["similarity"])
        ):
            grouped[key] = row

    return sorted(
        grouped.values(),
        key=lambda item: float(
            item["similarity"]
        ),
        reverse=True,
    )

class ProductEmbeddingRepository:
    def search(
        self,
        embedding: list[float],
        model_name: str,
        pretrained_name: str,
        product_type: str | list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        conditions = [
            "pi.is_active = TRUE",
            "p.status = 'ACTIVE'",
            "pie.model_name = %s",
            "pie.pretrained_name = %s",
        ]

        vector = Vector(embedding)

        product_types = (
            [product_type]
            if isinstance(product_type, str)
            else list(product_type or [])
        )

        if product_types:
            conditions.append("p.product_type = ANY(%s)")

        parameters: list[Any] = [
            vector,
            model_name,
            pretrained_name,
        ]

        if product_types:
            parameters.append(product_types)

        parameters.extend([
            vector,
            limit,
        ])

        query = f"""
            SELECT
                p.product_code,
                p.title,
                p.product_type,
                pi.id AS product_image_id,
                pi.color,
                pi.image_order,
                pi.is_featured,
                pi.local_path,
                pi.source_url,

                1 - (
                    pie.embedding <=> %s
                ) AS similarity

            FROM product_image_embeddings pie

            JOIN product_images pi
                ON pi.id = pie.product_image_id

            JOIN products p
                ON p.id = pi.product_id

            WHERE {" AND ".join(conditions)}

            ORDER BY pie.embedding <=> %s
            LIMIT %s
        """

        with database_connection() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()

        return [dict(row) for row in rows]
