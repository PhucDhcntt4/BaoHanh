import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv  # type: ignore

from app.database.connection import database_connection
from app.scripts.import_products_to_db import (
    import_catalog,
    initialize_schema,
    normalize_catalog,
)
from app.scripts.sync_product_images import sync_product_images

from app.scripts.build_product_image_embeddings import(
    main as build_product_image_embeddings
)



load_dotenv()


SHOP = os.getenv("SHOP")
TOKEN = os.getenv("SHOPIFY_TOKEN")
API_VERSION = os.getenv("SHOPIFY_API_VERSION")

# Thư mục lưu file JSON.
PROJECT_ROOT = Path(__file__).resolve().parent


PRODUCT_BY_SKU_QUERY = """
query ProductBySku(
  $query: String!
  $variantLimit: Int!
  $imageLimit: Int!
  $after: String
) {
  productVariants(
    first: $variantLimit
    after: $after
    query: $query
  ) {
    nodes {
      id
      legacyResourceId
      title
      sku
      barcode
      price
      compareAtPrice
      inventoryQuantity

      selectedOptions {
        name
        value
      }

      inventoryItem {
        id
        tracked

        measurement {
          weight {
            value
            unit
          }
        }
      }

      image {
        id
        url
        altText
        width
        height
      }

      product {
        id
        legacyResourceId

        title
        handle
        vendor
        productType

        description
        status

        createdAt
        updatedAt

        onlineStoreUrl

        featuredImage {
          id
          url
          altText
          width
          height
        }

        images(first: $imageLimit) {
          nodes {
            id
            url
            altText
            width
            height
          }
        }

        options {
          id
          name
          values
        }

        variants(first: 100) {
          nodes {
            id
            legacyResourceId

            title
            sku
            barcode

            price
            compareAtPrice
            inventoryQuantity

            selectedOptions {
              name
              value
            }

            image {
              id
              url
              altText
              width
              height
            }

            inventoryItem {
              id
              tracked

              measurement {
                weight {
                  value
                  unit
                }
              }
            }
          }
        }

        seo {
          title
          description
        }
      }
    }

    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


PRODUCT_VARIANTS_QUERY = """
query ProductVariants(
  $productId: ID!
  $first: Int!
  $after: String
) {
  product(id: $productId) {
    id

    variants(
      first: $first
      after: $after
    ) {
      nodes {
        id
        legacyResourceId
        title
        sku
        barcode
        price
        compareAtPrice
        inventoryQuantity

        selectedOptions {
          name
          value
        }

        inventoryItem {
          id
          tracked

          measurement {
            weight {
              value
              unit
            }
          }
        }

        image {
          id
          url
          altText
          width
          height
        }
      }

      pageInfo {
        hasNextPage
        endCursor
      }
    }
  }
}
"""


def validate_config() -> None:
    """
    Kiểm tra các biến cấu hình Shopify.
    """

    missing: list[str] = []

    if not SHOP:
        missing.append("SHOP")

    if not TOKEN:
        missing.append("SHOPIFY_TOKEN")

    if not API_VERSION:
        missing.append("SHOPIFY_API_VERSION")

    if missing:
        raise ValueError(
            "Thiếu biến môi trường trong file .env: "
            + ", ".join(missing)
        )


def shopify_graphql(
    query: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Gọi Shopify GraphQL Admin API.
    """

    validate_config()

    url = (
        f"https://{SHOP}/admin/api/"
        f"{API_VERSION}/graphql.json"
    )

    try:
        response = requests.post(
            url,
            headers={
                "X-Shopify-Access-Token": str(TOKEN),
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "variables": variables or {},
            },
            timeout=30,
        )

        response.raise_for_status()

    except requests.Timeout as error:
        raise RuntimeError(
            "Shopify API phản hồi quá thời gian."
        ) from error

    except requests.RequestException as error:
        raise RuntimeError(
            f"Không thể kết nối Shopify API: {error}"
        ) from error

    try:
        result = response.json()

    except ValueError as error:
        raise RuntimeError(
            "Shopify trả về dữ liệu không phải JSON."
        ) from error

    if result.get("errors"):
        raise RuntimeError(
            "Shopify GraphQL lỗi:\n"
            + json.dumps(
                result["errors"],
                ensure_ascii=False,
                indent=2,
            )
        )

    if "data" not in result:
        raise RuntimeError(
            "Shopify không trả về trường data."
        )

    return result["data"]


def normalize_sku(sku: str) -> str:
    """
    Chuẩn hóa SKU người dùng nhập.
    """

    return sku.strip().upper()


def escape_search_value(value: str) -> str:
    """
    Escape giá trị dùng trong Shopify search query.
    """

    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )


def product_contains_code(
    variant: dict[str, Any],
    product_code: str,
) -> bool:
    """
    Kiểm tra mã mẫu trên SKU, handle và mô tả sản phẩm.

    Một số màu được quản lý thành Shopify Product riêng và có thể dùng
    SKU variant khác, trong khi mã mẫu vẫn nằm trong handle/mô tả.
    """

    normalized_code = normalize_sku(product_code)
    variant_sku = normalize_sku(
        str(variant.get("sku") or "")
    )

    if variant_sku == normalized_code:
        return True

    product = variant.get("product") or {}
    handle = str(product.get("handle") or "").upper()

    handle_tokens = [
        token
        for token in re.split(r"[^A-Z0-9]+", handle)
        if token
    ]

    if normalized_code in handle_tokens:
        return True

    description = str(product.get("description") or "")
    code_pattern = re.compile(
        r"(?:MÃ|MA)\s*SẢN\s*PHẨM\s*:\s*"
        + re.escape(normalized_code)
        + r"\b",
        re.IGNORECASE,
    )

    return bool(code_pattern.search(description))


def get_all_product_variants(
    product_id: str,
) -> list[dict[str, Any]]:
    """
    Lấy toàn bộ variants của một sản phẩm bằng phân trang Shopify.
    """

    all_variants: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        data = shopify_graphql(
            PRODUCT_VARIANTS_QUERY,
            {
                "productId": product_id,
                "first": 100,
                "after": cursor,
            },
        )

        product = data.get("product")

        if not product:
            raise ValueError(
                f"Không tìm thấy sản phẩm Shopify: {product_id}"
            )

        variants_connection = product.get("variants", {})
        nodes = variants_connection.get("nodes", [])

        if not isinstance(nodes, list):
            raise RuntimeError(
                "Shopify trả về danh sách variants không hợp lệ."
            )

        all_variants.extend(nodes)

        page_info = variants_connection.get("pageInfo", {})

        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")

        if not cursor:
            raise RuntimeError(
                "Shopify báo còn trang variants nhưng không trả endCursor."
            )

    return all_variants


def find_products_by_sku(
    sku: str,
) -> list[dict[str, Any]]:
    """
    Tìm tất cả sản phẩm ACTIVE chứa variant có SKU chính xác.

    Shopify có thể quản lý mỗi màu thành một Product riêng nhưng
    các Product đó vẫn dùng chung một mã SKU.
    """

    normalized_sku = normalize_sku(sku)

    if not normalized_sku:
        raise ValueError("Mã SKU không được để trống.")

    escaped_sku = escape_search_value(normalized_sku)

    variants: list[dict[str, Any]] = []
    cursor: str | None = None

    while True:
        data = shopify_graphql(
            PRODUCT_BY_SKU_QUERY,
            {
                # Tìm mặc định trên nhiều trường thay vì chỉ SKU.
                # Cần thiết khi mỗi màu là một Product riêng.
                "query": f'"{escaped_sku}"',
                "variantLimit": 50,
                "imageLimit": 100,
                "after": cursor,
            },
        )

        variants_connection = data.get("productVariants", {})
        page_nodes = variants_connection.get("nodes", [])

        if not isinstance(page_nodes, list):
            raise RuntimeError(
                "Shopify trả về kết quả tìm SKU không hợp lệ."
            )

        variants.extend(page_nodes)

        page_info = variants_connection.get("pageInfo", {})

        if not page_info.get("hasNextPage"):
            break

        cursor = page_info.get("endCursor")

        if not cursor:
            raise RuntimeError(
                "Shopify báo còn kết quả SKU nhưng không trả endCursor."
            )

    if not variants:
        return []

    # Shopify search có thể trả về nhiều kết quả gần giống.
    # Kiểm tra mã trên SKU, handle hoặc mô tả bằng Python.
    code_matches = [
        variant
        for variant in variants
        if product_contains_code(variant, normalized_sku)
    ]

    if not code_matches:
        return []

    # Chỉ lấy sản phẩm đang ACTIVE.
    active_matches = [
        variant
        for variant in code_matches
        if (
            variant.get("product")
            and variant["product"].get("status") == "ACTIVE"
        )
    ]

    if not active_matches:
        raise ValueError(
            f"Đã tìm thấy SKU '{normalized_sku}', "
            "nhưng sản phẩm không ở trạng thái ACTIVE."
        )

    products_by_id: dict[str, dict[str, Any]] = {}

    for matched_variant in active_matches:
        product = matched_variant["product"]
        product_id = product.get("id")

        if not product_id:
            continue

        # Một Product có nhiều size cùng SKU nên cần khử trùng theo
        # Shopify Product ID trước khi tải toàn bộ variants.
        if product_id in products_by_id:
            continue

        all_variants = get_all_product_variants(product_id)
        product["variants"] = {
            "nodes": all_variants,
        }

        products_by_id[product_id] = {
            "searched_sku": normalized_sku,
            "matched_variant": {
                "id": matched_variant.get("id"),
                "legacyResourceId": matched_variant.get(
                    "legacyResourceId"
                ),
                "title": matched_variant.get("title"),
                "sku": matched_variant.get("sku"),
                "barcode": matched_variant.get("barcode"),
                "price": matched_variant.get("price"),
                "compareAtPrice": matched_variant.get(
                    "compareAtPrice"
                ),
                "inventoryQuantity": matched_variant.get(
                    "inventoryQuantity"
                ),
                "selectedOptions": matched_variant.get(
                    "selectedOptions",
                    [],
                ),
                "image": matched_variant.get("image"),
                "inventoryItem": matched_variant.get(
                    "inventoryItem"
                ),
            },
            "product": product,
        }

    return list(products_by_id.values())


def find_product_by_sku(
    sku: str,
) -> dict[str, Any] | None:
    """
    Hàm tương thích cho nơi chỉ cần kết quả đầu tiên.
    """

    products = find_products_by_sku(sku)
    return products[0] if products else None

def print_product_summary(
    product_data: dict[str, Any],
) -> None:
    """
    In thông tin tóm tắt ra terminal.
    """

    product = product_data["product"]
    matched_variant = product_data["matched_variant"]

    print("\n========== SẢN PHẨM ==========")
    print(f"Tên: {product.get('title')}")
    print(f"Handle: {product.get('handle')}")
    print(f"Trạng thái: {product.get('status')}")
    print(f"Loại: {product.get('productType')}")
    print(f"Nhà cung cấp: {product.get('vendor')}")
    print(f"SKU tìm thấy: {matched_variant.get('sku')}")
    print(f"Giá: {matched_variant.get('price')}")
    print(
        "Tồn kho: "
        f"{matched_variant.get('inventoryQuantity')}"
    )
    print(
        "Số lượng ảnh: "
        f"{len(product.get('images', {}).get('nodes', []))}"
    )
    print(
        "Số lượng variants: "
        f"{len(product.get('variants', {}).get('nodes', []))}"
    )


PROJECT_ROOT = Path(__file__).resolve().parent
PRODUCTS_FILE = PROJECT_ROOT / "products.json"


def validate_product_structure(
    product_data: dict[str, Any],
) -> None:
    """
    Bảo đảm dữ liệu lưu ra đúng schema mà ứng dụng đang đọc:
    searched_sku + matched_variant + product.
    """

    searched_sku = product_data.get("searched_sku")
    matched_variant = product_data.get("matched_variant")
    product = product_data.get("product")

    if not isinstance(searched_sku, str) or not searched_sku:
        raise ValueError("Dữ liệu thiếu searched_sku hợp lệ.")

    if not isinstance(matched_variant, dict):
        raise ValueError("Dữ liệu thiếu matched_variant.")

    if not isinstance(product, dict):
        raise ValueError("Dữ liệu thiếu product.")

    if not isinstance(product.get("featuredImage"), dict):
        raise ValueError("Product thiếu featuredImage.")

    images = product.get("images")
    if not isinstance(images, dict) or not isinstance(
        images.get("nodes"),
        list,
    ):
        raise ValueError("Product.images.nodes không hợp lệ.")

    variants = product.get("variants")
    if not isinstance(variants, dict) or not isinstance(
        variants.get("nodes"),
        list,
    ):
        raise ValueError("Product.variants.nodes không hợp lệ.")


def save_product(product_data: dict) -> None:
    """
    Thêm hoặc cập nhật sản phẩm vào products.json
    """

    validate_product_structure(product_data)

    PRODUCTS_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # Nếu chưa có file
    if not PRODUCTS_FILE.exists():
        products = []
    else:
        with open(
            PRODUCTS_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            try:
                products = json.load(f)
            except Exception:
                products = []

    sku = product_data["searched_sku"]
    product_id = product_data["product"].get("id")

    if not product_id:
        raise ValueError("Sản phẩm không có Shopify ID.")

    updated = False

    for index, item in enumerate(products):

        existing_product = (
            item.get("product", {})
            if isinstance(item, dict)
            else {}
        )

        if existing_product.get("id") == product_id:

            products[index] = product_data
            updated = True
            break

    if not updated:
        products.append(product_data)

    with open(
        PRODUCTS_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            products,
            f,
            ensure_ascii=False,
            indent=4
        )

    if updated:
        print(f"✓ Updated: {sku}")
    else:
        print(f"✓ Added: {sku}")


def import_products_to_database(
    product_items: list[dict[str, Any]],
) -> int:
    """Chuẩn hóa và upsert các sản phẩm vừa lấy vào PostgreSQL."""

    if not product_items:
        raise ValueError("Không có sản phẩm để import vào database.")

    catalog = normalize_catalog(product_items)
    if not catalog["products"]:
        raise ValueError("Dữ liệu sản phẩm không thể chuẩn hóa.")

    variant_count = sum(
        len(product["variants"])
        for product in catalog["products"].values()
    )
    image_count = sum(
        len(product["images"])
        for product in catalog["products"].values()
    )

    print(
        "\nĐang import database: "
        f"products={len(catalog['products'])}, "
        f"variants={variant_count}, images={image_count}"
    )

    with database_connection() as connection:
        initialize_schema(connection)
        return import_catalog(connection, catalog)

def main() -> None:
    print("====================================")
    print("  LẤY SẢN PHẨM SHOPIFY THEO SKU")
    print("====================================")
    print("Nhập 'exit' hoặc 'q' để thoát chương trình.")

    while True:
        print("\n------------------------------------")

        sku = input(
            "Nhập mã SKU sản phẩm: "
        ).strip()

        # Thoát chương trình
        if sku.lower() in {"exit", "quit", "q"}:
            print("\nĐã kết thúc chương trình.")
            break

        if not sku:
            print("Lỗi: Bạn chưa nhập mã SKU.")
            continue

        try:
            matching_products = find_products_by_sku(sku)

            if not matching_products:
                print(
                    f"Không tìm thấy sản phẩm có SKU: "
                    f"{normalize_sku(sku)}"
                )
                continue

            print(
                "\nTìm thấy "
                f"{len(matching_products)} Shopify Product "
                f"cùng mã {normalize_sku(sku)}."
            )

            for product_data in matching_products:
                # Mỗi màu có thể là một Shopify Product riêng.
                save_product(product_data)
                print_product_summary(product_data)

            image_result = sync_product_images(
                matching_products,
                remove_stale=False,
            )

            sync_run_id = import_products_to_database(
                matching_products
            )

            print("\n========== TẠO IMAGE EMBEDDING ==========")

            try:
                build_product_image_embeddings()
            except Exception as error:
                print(
                    "Cảnh báo: sản phẩm đã được import nhưng "
                    f"không thể tạo embedding: {error}"
                )

            print("\n========== HOÀN THÀNH ==========")
            print(f"Đã lưu vào: {PRODUCTS_FILE}")
            print(
                "Đã import/cập nhật PostgreSQL: "
                f"sync_run_id={sync_run_id}"
            )
            print(
                "Ảnh local: "
                f"tải mới={image_result['downloaded']}, "
                f"đã có={image_result['skipped']}, "
                f"lỗi={image_result['failed']}"
            )

        except KeyboardInterrupt:
            print("\n\nĐã dừng chương trình.")
            break

        except Exception as error:
            print(f"\nLỗi: {error}")

if __name__ == "__main__":
    main()
