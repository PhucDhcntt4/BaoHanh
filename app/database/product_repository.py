from decimal import Decimal
from typing import Any
import unicodedata

from app.database.connection import database_connection


class ProductRepository:

    @staticmethod
    def _number(value: Any) -> int | float:
        if isinstance(value, Decimal):
            if value == value.to_integral_value():
                return int(value)
            return float(value)
        return value

    def health(self) -> dict[str, Any]:
        with database_connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM products) AS products,
                    (SELECT COUNT(*) FROM product_variants) AS variants,
                    (SELECT COUNT(*) FROM product_images) AS images
                """
            ).fetchone()
        return dict(row)

    def product_types(
        self,
        active_only: bool = True,
    ) -> list[str]:
        conditions = "WHERE status = 'ACTIVE'" if active_only else ""
        with database_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT product_type
                FROM products
                {conditions}
                AND product_type IS NOT NULL
                AND product_type <> ''
                ORDER BY product_type
                """
                if conditions
                else """
                SELECT DISTINCT product_type
                FROM products
                WHERE product_type IS NOT NULL
                  AND product_type <> ''
                ORDER BY product_type
                """
            ).fetchall()
        return [str(row["product_type"]) for row in rows]

    def public_info(
        self,
        product_code: str,
    ) -> dict[str, Any] | None:
        normalized_code = product_code.strip().upper()
        if not normalized_code:
            return None

        with database_connection() as connection:
            product = connection.execute(
                """
                SELECT *
                FROM products
                WHERE product_code = %s
                """,
                (normalized_code,),
            ).fetchone()
            if not product:
                return None

            variants = connection.execute(
                """
                SELECT color, color_normalized, size, price,
                       inventory_quantity, available
                FROM product_variants
                WHERE product_id = %s
                ORDER BY color_normalized, size
                """,
                (product["id"],),
            ).fetchall()
            catalog_colors = connection.execute(
                """
                SELECT color
                FROM product_colors
                WHERE product_id = %s
                ORDER BY id
                """,
                (product["id"],),
            ).fetchall()
            images = connection.execute(
                """
                SELECT color, color_normalized, source_url, local_path,
                       image_order, is_featured
                FROM product_images
                WHERE product_id = %s AND is_active = TRUE
                ORDER BY id
                """,
                (product["id"],),
            ).fetchall()

        prices = sorted({
            self._number(variant["price"])
            for variant in variants
            if variant["price"] is not None
        })

        colors: list[str] = [
            str(row["color"])
            for row in catalog_colors
        ]
        available_sizes: set[str] = set()
        availability_by_color: dict[str, dict[str, Any]] = {}
        for variant in variants:
            color = str(variant["color"] or "").strip()
            size = str(variant["size"] or "").strip()
            available = bool(variant["available"])

            if color and color not in colors:
                colors.append(color)
            if available and size:
                available_sizes.add(size)
            if color:
                color_info = availability_by_color.setdefault(
                    color,
                    {"available": False, "available_sizes": []},
                )
                if available:
                    color_info["available"] = True
                    if size:
                        color_info["available_sizes"].append(size)

        for color_info in availability_by_color.values():
            color_info["available_sizes"] = sorted(set(
                color_info["available_sizes"]
            ))

        image_urls: list[str] = []
        image_urls_by_color: dict[str, list[str]] = {}
        featured_image = None
        for image in images:
            image_url = image["source_url"] or image["local_path"]
            if not image_url:
                continue
            image_url = str(image_url)
            if image["is_featured"] and featured_image is None:
                featured_image = image_url
            if image_url not in image_urls:
                image_urls.append(image_url)

            color = str(image["color"] or "").strip()
            if color:
                color_urls = image_urls_by_color.setdefault(color, [])
                if image_url not in color_urls:
                    color_urls.append(image_url)

        return {
            "product_code": normalized_code,
            "product_name": product["title"],
            "product_type": product["product_type"],
            "description": product["description"] or "",
            "material": product["material"],
            "sole": product["sole"],
            "height": product["height"],
            "status": product["status"],
            "prices": prices,
            "colors": colors,
            "available_sizes": sorted(available_sizes),
            "availability_by_color": availability_by_color,
            "featured_image": featured_image,
            "image_urls": image_urls[:4],
            "image_urls_by_color": {
                color: urls[:4]
                for color, urls in image_urls_by_color.items()
            },
            "handle": product["handle"],
        }

    def all_product_codes(self) -> list[str]:
        with database_connection() as connection:
            rows = connection.execute(
                """
                SELECT product_code
                FROM products
                ORDER BY product_code
                """
            ).fetchall()
        return [str(row["product_code"]) for row in rows]

    @staticmethod
    def _normalize(value: Any) -> str:
        normalized = unicodedata.normalize(
            "NFD", str(value or "").casefold()
        )
        return "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        ).replace("đ", "d")

    def search(
        self,
        query: str,
        active_only: bool = True,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        normalized_query = self._normalize(query).strip()
        if not normalized_query:
            return []
        stop_words = {
            "e", "em", "a", "anh", "chi", "co", "khong", "k",
            "shop", "cua", "hang", "minh", "cho", "hoi",
        }
        query_tokens = {
            token for token in normalized_query.split()
            if token not in stop_words and len(token) >= 2
        }
        with database_connection() as connection:
            rows = connection.execute(
                """
                SELECT p.product_code, p.title, p.product_type,
                       p.description, p.vendor, p.status,
                       COALESCE(string_agg(a.alias_normalized, ' '), '') aliases
                FROM products p
                LEFT JOIN product_aliases a ON a.product_id = p.id
                GROUP BY p.id
                ORDER BY p.id
                """
            ).fetchall()

        scored: list[tuple[int, dict[str, Any]]] = []
        for row in rows:
            if active_only and row["status"] != "ACTIVE":
                continue
            searchable = self._normalize(" ".join([
                str(row["title"] or ""), str(row["product_type"] or ""),
                str(row["description"] or ""), str(row["vendor"] or ""),
                str(row["product_code"] or ""), str(row["aliases"] or ""),
            ]))
            matched_tokens = sum(token in searchable for token in query_tokens)
            if normalized_query in searchable:
                score = 100 + matched_tokens
            elif matched_tokens:
                score = matched_tokens
            else:
                continue
            info = self.public_info(str(row["product_code"]))
            if info:
                scored.append((score, info))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:limit]]

    def reference_products(
        self,
        product_type: str | None = None,
        limit: int = 5,
        images_per_product: int = 3,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = []
        condition = ""
        if product_type:
            condition = "AND p.product_type = %s"
            parameters.append(product_type)
        parameters.append(limit)

        with database_connection() as connection:
            products = connection.execute(
                f"""
                SELECT p.id, p.product_code, p.title, p.product_type
                FROM products p
                WHERE p.status = 'ACTIVE' {condition}
                ORDER BY p.id
                LIMIT %s
                """,
                tuple(parameters),
            ).fetchall()
            references = []
            for product in products:
                images = connection.execute(
                    """
                    SELECT source_url, local_path
                    FROM product_images
                    WHERE product_id = %s AND is_active = TRUE
                    ORDER BY id
                    LIMIT %s
                    """,
                    (product["id"], images_per_product),
                ).fetchall()
                image_urls = list(dict.fromkeys(
                    str(image["source_url"] or image["local_path"])
                    for image in images
                    if image["source_url"] or image["local_path"]
                ))
                if image_urls:
                    references.append({
                        "product_code": product["product_code"],
                        "title": product["title"],
                        "product_type": product["product_type"],
                        "image_urls": image_urls,
                    })
        return references
