from pathlib import Path

from pgvector import Vector # type: ignore

from app.config import PRODUCT_IMAGE_DIR
from app.database.connection import database_connection
from app.product_recognition.image_embedding_service import (
    ImageEmbeddingService,
)


def main() -> dict[str, int]:
    service = ImageEmbeddingService()

    with database_connection() as connection:
        images = connection.execute(
            """
            SELECT
                pi.id,
                pi.local_path,
                p.product_code
            FROM product_images pi
            JOIN products p
                ON p.id = pi.product_id
            WHERE pi.is_active = TRUE
              AND pi.local_path IS NOT NULL
              AND pi.local_path <> ''
            ORDER BY p.product_code, pi.image_order
            """
        ).fetchall()

        created = 0
        skipped = 0
        failed = 0

        for row in images:
            image_path = (
                PRODUCT_IMAGE_DIR
                / row["local_path"]
            )

            if not image_path.is_file():
                print(
                    "Missing:",
                    row["product_code"],
                    image_path,
                )
                failed += 1
                continue

            try:
                image_bytes = image_path.read_bytes()
                checksum = service.checksum(image_bytes)

                existing = connection.execute(
                    """
                    SELECT image_checksum
                    FROM product_image_embeddings
                    WHERE product_image_id = %s
                      AND model_name = %s
                      AND pretrained_name = %s
                    """,
                    (
                        row["id"],
                        service.model_name,
                        service.pretrained_name,
                    ),
                ).fetchone()

                if (
                    existing
                    and existing["image_checksum"]
                    == checksum
                ):
                    skipped += 1
                    continue

                embedding = service.embed_bytes(
                    image_bytes
                )

                connection.execute(
                    """
                    INSERT INTO product_image_embeddings(
                        product_image_id,
                        model_name,
                        pretrained_name,
                        embedding,
                        image_checksum
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (
                        product_image_id,
                        model_name,
                        pretrained_name
                    )
                    DO UPDATE SET
                        embedding = EXCLUDED.embedding,
                        image_checksum =
                            EXCLUDED.image_checksum,
                        updated_at = NOW()
                    """,
                    (
                        row["id"],
                        service.model_name,
                        service.pretrained_name,
                        Vector(embedding),
                        checksum,
                    ),
                )

                created += 1
                print(
                    "Embedded:",
                    row["product_code"],
                    row["local_path"],
                )

            except Exception as error:
                failed += 1
                print(
                    "Failed:",
                    row["product_code"],
                    error,
                )

        print("Created:", created)
        print("Skipped:", skipped)
        print("Failed:", failed)
        return {
            "created": created,
            "skipped": skipped,
            "failed": failed,
        }


if __name__ == "__main__":
    main()
