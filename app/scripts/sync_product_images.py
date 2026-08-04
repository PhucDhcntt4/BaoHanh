import json
import mimetypes
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from app.config import (
    PRODUCTS_PATH,
    PRODUCT_IMAGE_DIR,
    PRODUCT_IMAGE_MANIFEST_PATH,
)


ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

EXTENSIONS_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def safe_name(value: Any) -> str:
    text = str(value or "").strip().upper()

    cleaned = re.sub(
        r"[^A-Z0-9_-]+",
        "_",
        text,
    ).strip("_")

    return cleaned or "UNKNOWN"


def product_code(
    wrapper: dict[str, Any],
    product: dict[str, Any],
) -> str:
    searched_sku = str(
        wrapper.get("searched_sku") or ""
    ).strip()

    if searched_sku:
        return safe_name(searched_sku)

    variants = (
        product
        .get("variants", {})
        .get("nodes", [])
    )

    for variant in variants:
        sku = str(
            variant.get("sku") or ""
        ).strip()

        if sku:
            return safe_name(sku)

    return "UNKNOWN"


def image_extension(
    image_url: str,
    mime_type: str,
) -> str:
    known_extension = EXTENSIONS_BY_MIME.get(
        mime_type
    )

    if known_extension:
        return known_extension

    suffix = Path(
        urlparse(image_url).path
    ).suffix.lower()

    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return ".jpg" if suffix == ".jpeg" else suffix

    guessed = mimetypes.guess_extension(mime_type)

    return guessed or ".jpg"


def load_products() -> list[dict[str, Any]]:
    data = json.loads(
        PRODUCTS_PATH.read_text(
            encoding="utf-8"
        )
    )

    if not isinstance(data, list):
        raise ValueError(
            "products.json phải là một danh sách"
        )

    return [
        item
        for item in data
        if isinstance(item, dict)
    ]


def load_manifest() -> dict[str, dict[str, str]]:
    if not PRODUCT_IMAGE_MANIFEST_PATH.exists():
        return {}

    try:
        data = json.loads(
            PRODUCT_IMAGE_MANIFEST_PATH.read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return {}

    return data if isinstance(data, dict) else {}


def collect_images(
    product: dict[str, Any],
) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []

    featured_image = product.get(
        "featuredImage"
    )

    if isinstance(featured_image, dict):
        images.append(featured_image)

    product_images = (
        product
        .get("images", {})
        .get("nodes", [])
    )

    for image in product_images:
        if not isinstance(image, dict):
            continue

        image_url = image.get("url")

        if not image_url:
            continue

        if any(
            existing.get("url") == image_url
            for existing in images
        ):
            continue

        images.append(image)

    return images


def download_image(
    image_url: str,
) -> tuple[bytes, str]:
    response = requests.get(
        image_url,
        timeout=30,
    )

    response.raise_for_status()

    mime_type = response.headers.get(
        "Content-Type",
        "image/jpeg",
    ).split(";")[0].strip().lower()

    if mime_type not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"Định dạng ảnh không hỗ trợ: {mime_type}"
        )

    if not response.content:
        raise ValueError("Ảnh không có dữ liệu")

    if len(response.content) > 20 * 1024 * 1024:
        raise ValueError("Ảnh vượt quá 20 MB")

    return response.content, mime_type


def sync_product_images(
    products: list[dict[str, Any]] | None = None,
    *,
    remove_stale: bool = False,
) -> dict[str, int]:
    """Tải ảnh catalog về local và cập nhật manifest."""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(
            encoding="utf-8",
            errors="replace",
        )

    if products is None:
        products = load_products()

    PRODUCT_IMAGE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest = load_manifest()

    downloaded = 0
    skipped = 0
    failed = 0
    removed_from_manifest = 0
    active_urls: set[str] = set()

    for wrapper in products:
        nested_product = wrapper.get("product")

        product = (
            nested_product
            if isinstance(nested_product, dict)
            else wrapper
        )

        code = product_code(
            wrapper=wrapper,
            product=product,
        )

        shopify_product_id = safe_name(
            product.get("legacyResourceId")
            or product.get("id")
        )

        image_items = collect_images(product)

        product_dir = PRODUCT_IMAGE_DIR / code

        product_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        for position, image in enumerate(
            image_items,
            start=1,
        ):
            image_url = str(
                image.get("url") or ""
            ).strip()

            if not image_url:
                continue

            active_urls.add(image_url)

            existing = manifest.get(image_url)

            if existing:
                existing_path = (
                    PRODUCT_IMAGE_DIR
                    / existing.get("local_path", "")
                )

                if existing_path.is_file():
                    skipped += 1
                    continue

            try:
                image_bytes, mime_type = (
                    download_image(image_url)
                )

                extension = image_extension(
                    image_url=image_url,
                    mime_type=mime_type,
                )

                filename = (
                    f"{shopify_product_id}_"
                    f"{position:02d}{extension}"
                )

                image_path = product_dir / filename

                image_path.write_bytes(image_bytes)

                relative_path = image_path.relative_to(
                    PRODUCT_IMAGE_DIR
                )

                manifest[image_url] = {
                    "local_path": relative_path.as_posix(),
                    "mime_type": mime_type,
                }

                downloaded += 1

                print(
                    f"Downloaded: {code} -> "
                    f"{relative_path}"
                )

            except Exception as error:
                failed += 1

                print(
                    f"Failed: {code} | "
                    f"{image_url} | {error}"
                )

    if remove_stale:
        for stale_url in set(manifest) - active_urls:
            manifest.pop(stale_url, None)
            removed_from_manifest += 1

    temporary_manifest_path = (
        PRODUCT_IMAGE_MANIFEST_PATH.with_suffix(".json.tmp")
    )
    temporary_manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary_manifest_path.replace(
        PRODUCT_IMAGE_MANIFEST_PATH
    )

    print("\n========== HOÀN THÀNH ==========")
    print(f"Đã tải: {downloaded}")
    print(f"Đã tồn tại: {skipped}")
    print(f"Lỗi: {failed}")
    print(
        "Đã loại khỏi manifest: "
        f"{removed_from_manifest}"
    )
    print(
        "Manifest: "
        f"{PRODUCT_IMAGE_MANIFEST_PATH}"
    )

    return {
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "removed_from_manifest": removed_from_manifest,
    }


def main() -> None:
    sync_product_images(remove_stale=True)


if __name__ == "__main__":
    main()
