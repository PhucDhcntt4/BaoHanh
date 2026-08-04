import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb # type: ignore

from app.config import (
    PRODUCT_IMAGE_MANIFEST_PATH,
    PROJECT_ROOT,
    PRODUCTS_PATH,
)
from app.database.connection import database_connection


SCHEMA_PATH = PROJECT_ROOT / "db_postgre" / "001_product_catalog.sql"


def normalize_text(value: Any) -> str:
    normalized = unicodedata.normalize(
        "NFD", str(value or "").casefold()
    )
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("đ", "d").strip()


def product_code(item: dict[str, Any], product: dict[str, Any]) -> str:
    searched_sku = str(item.get("searched_sku") or "").strip().upper()
    if searched_sku:
        return searched_sku

    matched_sku = str(
        (item.get("matched_variant") or {}).get("sku") or ""
    ).strip().upper()
    if matched_sku:
        return matched_sku

    title_match = re.search(
        r"\b[A-Z]\d{3,}[A-Z0-9]*\b",
        str(product.get("title") or "").upper(),
    )
    if title_match:
        return title_match.group(0)

    for variant in (product.get("variants") or {}).get("nodes", []):
        sku = str(variant.get("sku") or "").strip().upper()
        if sku:
            return sku
    return ""


def selected_option(
    variant: dict[str, Any],
    option_name: str,
) -> str | None:
    expected = normalize_text(option_name)
    for option in variant.get("selectedOptions") or []:
        if normalize_text(option.get("name")) == expected:
            value = str(option.get("value") or "").strip()
            return value or None
    return None


def product_colors(product: dict[str, Any]) -> list[str]:
    colors: list[str] = []
    for option in product.get("options") or []:
        if normalize_text(option.get("name")) == "color":
            colors.extend(
                str(value).strip()
                for value in option.get("values") or []
                if str(value).strip()
            )
    return list(dict.fromkeys(colors))


def description_spec(description: str, labels: tuple[str, ...]) -> str | None:
    for label in labels:
        match = re.search(
            rf"-\s*{label}\s*:\s*([^-\r\n]+)",
            description,
            flags=re.IGNORECASE,
        )
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None


def description_colors(description: str) -> list[str]:
    match = re.search(
        r"-\s*(?:Màu sắc|Màu)\s*:\s*([^-]+)",
        description,
        flags=re.IGNORECASE,
    )
    if not match:
        return []
    return [
        color.strip()
        for color in match.group(1).split(",")
        if color.strip()
    ]


def normalize_catalog(data: list[dict[str, Any]]) -> dict[str, Any]:
    products: dict[str, dict[str, Any]] = {}
    source_payloads: list[dict[str, Any]] = []

    for item in data:
        if not isinstance(item, dict):
            continue
        product = item.get("product", item)
        if not isinstance(product, dict):
            continue

        code = product_code(item, product)
        if not code:
            continue

        description = str(product.get("description") or "")
        canonical = products.setdefault(code, {
            "product_code": code,
            "title": str(product.get("title") or code),
            "handle": product.get("handle"),
            "vendor": product.get("vendor"),
            "product_type": product.get("productType"),
            "description": description,
            "material": description_spec(description, ("Chất liệu",)),
            "sole": description_spec(description, ("Đế",)),
            "height": description_spec(
                description, ("Cao", "Chiều cao")
            ),
            "status": product.get("status") or "ACTIVE",
            "online_store_url": product.get("onlineStoreUrl"),
            "source_updated_at": product.get("updatedAt"),
            "variants": {},
            "images": {},
            "colors": {},
            "aliases": set(),
        })

        canonical["aliases"].update(filter(None, (
            canonical["title"],
            canonical["product_type"],
            code,
        )))

        colors = product_colors(product)
        for color in colors + description_colors(description):
            canonical["colors"].setdefault(
                normalize_text(color),
                color,
            )
        for variant in (product.get("variants") or {}).get("nodes", []):
            sku = str(variant.get("sku") or code).strip().upper()
            color = selected_option(variant, "Color")
            size = selected_option(variant, "Size")
            key = (sku, normalize_text(color), size or "")
            quantity = int(variant.get("inventoryQuantity") or 0)
            canonical["variants"][key] = {
                "external_id": variant.get("id"),
                "legacy_id": variant.get("legacyResourceId"),
                "sku": sku,
                "barcode": variant.get("barcode"),
                "variant_title": variant.get("title"),
                "color": color,
                "color_normalized": normalize_text(color),
                "size": size or "",
                "price": variant.get("price"),
                "compare_at_price": variant.get("compareAtPrice"),
                "inventory_quantity": quantity,
                "available": quantity > 0,
            }

        image_items: list[tuple[dict[str, Any], bool]] = []
        featured = product.get("featuredImage") or {}
        if featured.get("url"):
            image_items.append((featured, True))
        image_items.extend(
            (image, False)
            for image in (product.get("images") or {}).get("nodes", [])
            if image.get("url")
        )

        assigned_colors = colors or [None]
        for color in assigned_colors:
            for position, (image, is_featured) in enumerate(image_items):
                key = (str(image.get("url")), normalize_text(color))
                canonical["images"][key] = {
                    "external_id": image.get("id"),
                    "color": color,
                    "color_normalized": normalize_text(color),
                    "source_url": image.get("url"),
                    "alt_text": image.get("altText"),
                    "width": image.get("width"),
                    "height": image.get("height"),
                    "image_order": position,
                    "is_featured": is_featured,
                }

        encoded = json.dumps(
            item, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        source_payloads.append({
            "external_product_id": product.get("id"),
            "product_code": code,
            "payload": item,
            "payload_hash": hashlib.sha256(encoded).hexdigest(),
        })

    return {
        "products": products,
        "source_payloads": source_payloads,
    }


def initialize_schema(connection: Any) -> None:
    connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def sync_local_image_paths(connection: Any) -> int:
    if not PRODUCT_IMAGE_MANIFEST_PATH.exists():
        return 0
    manifest = json.loads(
        PRODUCT_IMAGE_MANIFEST_PATH.read_text(encoding="utf-8")
    )
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json phải là một object")

    updated = 0
    for source_url, metadata in manifest.items():
        if not isinstance(metadata, dict):
            continue
        local_path = str(metadata.get("local_path") or "").strip()
        if not local_path:
            continue
        result = connection.execute(
            """
            UPDATE product_images
            SET local_path = %s,
                mime_type = %s,
                checksum = COALESCE(%s, checksum),
                updated_at = NOW()
            WHERE source_url = %s
            """,
            (
                local_path,
                metadata.get("mime_type") or "image/jpeg",
                metadata.get("checksum"),
                source_url,
            ),
        )
        updated += result.rowcount
    return updated


def import_catalog(connection: Any, catalog: dict[str, Any]) -> int:
    run = connection.execute(
        """
        INSERT INTO catalog_sync_runs(source, status, total_records)
        VALUES ('products.json', 'running', %s)
        RETURNING id
        """,
        (len(catalog["source_payloads"]),),
    ).fetchone()
    run_id = run["id"]

    try:
        for source in catalog["source_payloads"]:
            connection.execute(
                """
                INSERT INTO product_source_payloads(
                    sync_run_id, external_product_id, product_code,
                    payload, payload_hash
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (external_product_id, payload_hash) DO NOTHING
                """,
                (
                    run_id,
                    source["external_product_id"],
                    source["product_code"],
                    Jsonb(source["payload"]),
                    source["payload_hash"],
                ),
            )

        for product in catalog["products"].values():
            row = connection.execute(
                """
                INSERT INTO products(
                    product_code, title, handle, vendor, product_type,
                    description, material, sole, height, status,
                    online_store_url, source_updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (product_code) DO UPDATE SET
                    title = EXCLUDED.title,
                    handle = EXCLUDED.handle,
                    vendor = EXCLUDED.vendor,
                    product_type = EXCLUDED.product_type,
                    description = EXCLUDED.description,
                    material = EXCLUDED.material,
                    sole = EXCLUDED.sole,
                    height = EXCLUDED.height,
                    status = EXCLUDED.status,
                    online_store_url = EXCLUDED.online_store_url,
                    source_updated_at = EXCLUDED.source_updated_at,
                    updated_at = NOW()
                RETURNING id
                """,
                (
                    product["product_code"], product["title"],
                    product["handle"], product["vendor"],
                    product["product_type"], product["description"],
                    product["material"], product["sole"],
                    product["height"], product["status"],
                    product["online_store_url"],
                    product["source_updated_at"],
                ),
            ).fetchone()
            product_id = row["id"]

            for variant in product["variants"].values():
                connection.execute(
                    """
                    INSERT INTO product_variants(
                        product_id, external_id, legacy_id, sku, barcode,
                        variant_title, color, color_normalized, size,
                        price, compare_at_price, inventory_quantity, available
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                    ON CONFLICT (product_id, sku, color_normalized, size)
                    DO UPDATE SET
                        external_id = EXCLUDED.external_id,
                        legacy_id = EXCLUDED.legacy_id,
                        barcode = EXCLUDED.barcode,
                        variant_title = EXCLUDED.variant_title,
                        price = EXCLUDED.price,
                        compare_at_price = EXCLUDED.compare_at_price,
                        inventory_quantity = EXCLUDED.inventory_quantity,
                        available = EXCLUDED.available,
                        updated_at = NOW()
                    """,
                    (product_id, *variant.values()),
                )

            for image in product["images"].values():
                connection.execute(
                    """
                    INSERT INTO product_images(
                        product_id, external_id, color, color_normalized,
                        source_url, alt_text, width, height, image_order,
                        is_featured
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (product_id, source_url, color_normalized)
                    DO UPDATE SET
                        alt_text = EXCLUDED.alt_text,
                        width = EXCLUDED.width,
                        height = EXCLUDED.height,
                        image_order = EXCLUDED.image_order,
                        is_featured = EXCLUDED.is_featured,
                        is_active = TRUE,
                        updated_at = NOW()
                    """,
                    (product_id, *image.values()),
                )

            for alias in product["aliases"]:
                connection.execute(
                    """
                    INSERT INTO product_aliases(
                        product_id, alias, alias_normalized, alias_type
                    ) VALUES (%s, %s, %s, 'catalog')
                    ON CONFLICT DO NOTHING
                    """,
                    (product_id, alias, normalize_text(alias)),
                )

            for normalized_color, color in product["colors"].items():
                connection.execute(
                    """
                    INSERT INTO product_colors(
                        product_id, color, color_normalized, source
                    ) VALUES (%s, %s, %s, 'catalog')
                    ON CONFLICT (product_id, color_normalized)
                    DO UPDATE SET
                        color = EXCLUDED.color,
                        updated_at = NOW()
                    """,
                    (product_id, color, normalized_color),
                )

        connection.execute(
            """
            UPDATE catalog_sync_runs
            SET status = 'completed', updated_records = %s,
                completed_at = NOW()
            WHERE id = %s
            """,
            (len(catalog["products"]), run_id),
        )
        sync_local_image_paths(connection)
        return run_id
    except Exception as exc:
        connection.execute(
            """
            UPDATE catalog_sync_runs
            SET status = 'failed', error_message = %s,
                completed_at = NOW()
            WHERE id = %s
            """,
            (str(exc), run_id),
        )
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import products.json into PostgreSQL"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=PRODUCTS_PATH,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Normalize and validate without connecting to PostgreSQL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    raw_data = json.loads(args.source.read_text(encoding="utf-8"))
    if not isinstance(raw_data, list):
        raise ValueError("products.json phải là một danh sách")

    catalog = normalize_catalog(raw_data)
    variant_count = sum(
        len(product["variants"])
        for product in catalog["products"].values()
    )
    image_count = sum(
        len(product["images"])
        for product in catalog["products"].values()
    )
    print(
        "Normalized catalog: "
        f"products={len(catalog['products'])} "
        f"variants={variant_count} images={image_count}"
    )

    if args.dry_run:
        return

    with database_connection() as connection:
        initialize_schema(connection)
        run_id = import_catalog(connection, catalog)
    print(f"Import completed: sync_run_id={run_id}")


if __name__ == "__main__":
    main()
