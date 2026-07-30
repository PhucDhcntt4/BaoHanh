import json
import os
import re
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv  # type: ignore


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
) {
  productVariants(
    first: $variantLimit
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


def find_product_by_sku(
    sku: str,
) -> dict[str, Any] | None:
    """
    Tìm sản phẩm ACTIVE chứa variant có SKU chính xác.
    """

    normalized_sku = normalize_sku(sku)

    if not normalized_sku:
        raise ValueError("Mã SKU không được để trống.")

    escaped_sku = escape_search_value(normalized_sku)

    data = shopify_graphql(
        PRODUCT_BY_SKU_QUERY,
        {
            "query": f'sku:"{escaped_sku}"',
            "variantLimit": 50,
            "imageLimit": 100,
        },
    )

    variants = (
        data
        .get("productVariants", {})
        .get("nodes", [])
    )

    if not variants:
        return None

    # Shopify search có thể trả về nhiều kết quả gần giống.
    # Luôn kiểm tra lại SKU chính xác bằng Python.
    exact_matches = [
        variant
        for variant in variants
        if normalize_sku(
            str(variant.get("sku") or "")
        ) == normalized_sku
    ]

    if not exact_matches:
        return None

    # Chỉ lấy sản phẩm đang ACTIVE.
    active_matches = [
        variant
        for variant in exact_matches
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

    matched_variant = active_matches[0]
    product = matched_variant["product"]

    return {
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

    updated = False

    for index, item in enumerate(products):

        if (
            isinstance(item, dict)
            and item.get("searched_sku") == sku
        ):

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
            product_data = find_product_by_sku(sku)

            if not product_data:
                print(
                    f"Không tìm thấy sản phẩm có SKU: "
                    f"{normalize_sku(sku)}"
                )
                continue

            # Lưu hoặc cập nhật vào products.json
            save_product(product_data)

            print_product_summary(product_data)

            print("\n========== HOÀN THÀNH ==========")
            print(f"Đã lưu vào: {PRODUCTS_FILE}")

        except KeyboardInterrupt:
            print("\n\nĐã dừng chương trình.")
            break

        except Exception as error:
            print(f"\nLỗi: {error}")

if __name__ == "__main__":
    main()
