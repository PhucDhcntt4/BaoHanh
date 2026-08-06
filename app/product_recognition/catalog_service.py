import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.config import PRODUCTS_PATH, PRODUCT_CATALOG_SOURCE
from app.database.product_repository import ProductRepository


class ProductCatalogService:
    def __init__(
        self,
        path: str | Path = PRODUCTS_PATH,
        source: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.source = (source or PRODUCT_CATALOG_SOURCE).casefold()
        if self.source not in {"json", "database"}:
            raise ValueError("Catalog source must be 'json' or 'database'")
        self.repository = ProductRepository()
        # Runtime database mode must not require products.json to exist or be
        # valid. JSON is loaded only when explicitly selected (for local
        # tooling, comparison or import workflows).
        self._products = self._load() if self.source == "json" else []
        self._by_code: dict[str, list[dict[str, Any]]] = {}
        for product in self._products:
            for code in self.product_codes(product):
                self._by_code.setdefault(code, []).append(product)

    def _load(self) -> list[dict[str, Any]]:
        data = json.loads(
            self.path.read_text(encoding="utf-8")
        )
        if not isinstance(data, list):
            raise ValueError("products.json phải là một danh sách")
        products = []
        for item in data:
            if not isinstance(item, dict):
                continue

            nested_product = item.get("product")
            if isinstance(nested_product, dict):
                product = dict(nested_product)
                product["_searched_sku"] = item.get(
                    "searched_sku"
                )
                product["_matched_variant"] = item.get(
                    "matched_variant"
                )
                products.append(product)
            else:
                products.append(item)

        return products

    @staticmethod
    def product_code(product: dict[str, Any]) -> str:
        searched_sku = str(
            product.get("_searched_sku") or ""
        ).strip().upper()
        if searched_sku:
            return searched_sku

        title = str(product.get("title", ""))
        match = re.search(r"\b[A-Z]\d{3,}[A-Z0-9]*\b", title.upper())
        if match:
            return match.group(0)

        variants = product.get("variants", {}).get("nodes", [])
        for variant in variants:
            sku = str(variant.get("sku") or "").strip().upper()
            if sku:
                return sku
        return ""

    @classmethod
    def product_codes(
        cls,
        product: dict[str, Any],
    ) -> set[str]:
        codes = set()

        primary_code = cls.product_code(product)
        if primary_code:
            codes.add(primary_code)

        searched_sku = str(
            product.get("_searched_sku") or ""
        ).strip().upper()
        if searched_sku:
            codes.add(searched_sku)

        matched_variant = product.get("_matched_variant") or {}
        matched_sku = str(
            matched_variant.get("sku") or ""
        ).strip().upper()
        if matched_sku:
            codes.add(matched_sku)

        variants = product.get("variants", {}).get("nodes", [])
        for variant in variants:
            sku = str(
                variant.get("sku") or ""
            ).strip().upper()
            if sku:
                codes.add(sku)

        return codes

    def reference_products(
        self,
        product_type: str | None = None,
        limit: int = 5,
        images_per_product: int = 3,
    ) -> list[dict[str, Any]]:
        references: list[dict[str, Any]] = []
        canonical_type = self.resolve_product_type(
            product_type
        )
        if (
            product_type
            and product_type != "unknown"
            and canonical_type is None
        ):
            return []

        if self.source == "database":
            return self.repository.reference_products(
                product_type=canonical_type,
                limit=limit,
                images_per_product=images_per_product,
            )

        normalized_type = self._normalize_search_text(
            canonical_type or ""
        ).strip()

        references_by_code: dict[str, dict[str, Any]] = {}

        for product in self._products:
            code = self.product_code(product)
            searchable_type = self._normalize_search_text(
                product.get("productType", "")
            ).strip()

            if normalized_type and searchable_type != normalized_type:
                continue

            # Một mã có thể có nhiều Shopify Product theo màu.
            # Nhận diện kiểu dáng chỉ cần một ảnh đại diện cho mỗi mã,
            # tránh để hai màu chiếm hai vị trí trong giới hạn.
            if not code:
                continue

            reference = references_by_code.get(code)
            if reference is None:
                if len(references) >= limit:
                    continue
                reference = {
                    "product_code": code,
                    "title": str(product.get("title", "")),
                    "product_type": str(product.get("productType", "")),
                    "image_urls": [],
                }
                references_by_code[code] = reference
                references.append(reference)

            candidate_urls: list[str] = []
            featured_url = (
                product.get("featuredImage") or {}
            ).get("url")
            if featured_url:
                candidate_urls.append(str(featured_url))
            for image in product.get("images", {}).get("nodes", []):
                image_url = image.get("url")
                if image_url:
                    candidate_urls.append(str(image_url))

            image_urls = reference["image_urls"]
            for image_url in candidate_urls:
                if len(image_urls) >= images_per_product:
                    break
                if image_url not in image_urls:
                    image_urls.append(image_url)

            if len(references) >= limit and all(
                len(item["image_urls"]) >= images_per_product
                for item in references
            ):
                break
        return [item for item in references if item["image_urls"]]

    def product_types(
        self,
        active_only: bool = True,
    ) -> list[str]:
        """Lấy danh sách productType thật và duy nhất từ catalog."""

        if self.source == "database":
            return self.repository.product_types(active_only)

        values = {
            str(product.get("productType") or "").strip()
            for product in self._products
            if (
                product.get("productType")
                and (
                    not active_only
                    or product.get("status") == "ACTIVE"
                )
            )
        }
        return sorted(value for value in values if value)

    def resolve_product_type(
        self,
        value: str | None,
        active_only: bool = True,
    ) -> str | None:
        """Xác minh và trả đúng cách viết productType trong catalog."""

        normalized_value = self._normalize_search_text(
            value or ""
        ).strip()
        if not normalized_value:
            return None

        valid_types = {
            self._normalize_search_text(item).strip(): item
            for item in self.product_types(
                active_only=active_only
            )
        }
        return valid_types.get(normalized_value)

    def get(self, product_code: str) -> dict[str, Any] | None:
        products = self.get_all(product_code)
        return products[0] if products else None

    def get_all(
        self,
        product_code: str,
    ) -> list[dict[str, Any]]:
        return self._by_code.get(
            product_code.strip().upper(),
            [],
        )

    @staticmethod
    def _normalize_search_text(value: Any) -> str:
        normalized = unicodedata.normalize(
            "NFD",
            str(value or "").casefold(),
        )
        without_accents = "".join(
            character
            for character in normalized
            if unicodedata.category(character) != "Mn"
        )
        return without_accents.replace("đ", "d")

    def search(
        self,
        query: str,
        active_only: bool = True,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        if self.source == "database":
            return self.repository.search(query, active_only, limit)

        normalized_query = self._normalize_search_text(query).strip()
        if not normalized_query:
            return []

        query_tokens = set(normalized_query.split())
        stop_words = {
            "e",
            "em",
            "a",
            "anh",
            "chi",
            "co",
            "khong",
            "k",
            "shop",
            "cua",
            "hang",
            "minh",
            "cho",
            "hoi",
        }

        query_tokens = {
            token
            for token in query_tokens
            if token not in stop_words and len(token) >= 2
        }
        scored_products = []
        scored_codes: set[str] = set()

        for product in self._products:
            if (
                active_only
                and product.get("status") != "ACTIVE"
            ):
                continue

            codes = " ".join(self.product_codes(product))
            searchable = self._normalize_search_text(
                " ".join(
                    [
                        str(product.get("title", "")),
                        str(product.get("productType", "")),
                        str(product.get("description", "")),
                        str(product.get("vendor", "")),
                        codes,
                    ]
                )
            )
            matched_tokens = sum(
                1 for token in query_tokens
                if token in searchable
            )

            if normalized_query in searchable:
                score = 100 + matched_tokens
            elif matched_tokens:
                score = matched_tokens
            else:
                continue

            code = self.product_code(product)
            if not code or code in scored_codes:
                continue

            info = self.public_info(code)
            if info:
                scored_products.append((score, info))
                scored_codes.add(code)

        scored_products.sort(
            key=lambda item: item[0],
            reverse=True,
        )
        return [
            product
            for _, product in scored_products[:limit]
        ]

    def public_info(
        self,
        product_code: str,
    ) -> dict[str, Any] | None:
        if self.source == "database":
            return self.repository.public_info(product_code)

        related_products = self.get_all(product_code)
        if not related_products:
            return None

        product = related_products[0]
        variants = [
            variant
            for related_product in related_products
            for variant in (
                related_product
                .get("variants", {})
                .get("nodes", [])
            )
        ]
        prices = sorted(
            {
                int(float(item["price"]))
                for item in variants
                if item.get("price") is not None
            }
        )
        available_sizes = []
        availability_by_color: dict[str, dict[str, Any]] = {}
        for variant in variants:
            quantity = int(variant.get("inventoryQuantity") or 0)
            variant_color = None
            variant_size = None
            for option in variant.get("selectedOptions", []):
                option_name = str(
                    option.get("name", "")
                ).casefold()
                option_value = str(option.get("value"))
                if option_name == "color":
                    variant_color = option_value
                elif option_name == "size":
                    variant_size = option_value

            if variant_color:
                color_info = availability_by_color.setdefault(
                    variant_color,
                    {
                        "available": False,
                        "available_sizes": [],
                    },
                )
                if quantity > 0:
                    color_info["available"] = True
                    if variant_size:
                        color_info["available_sizes"].append(
                            variant_size
                        )

            if quantity > 0 and variant_size:
                available_sizes.append(variant_size)

        for color_info in availability_by_color.values():
            color_info["available_sizes"] = sorted(
                set(color_info["available_sizes"])
            )

        colors = []
        for related_product in related_products:
            for option in related_product.get("options", []):
                if str(option.get("name", "")).casefold() == "color":
                    colors.extend(
                        str(value)
                        for value in option.get("values", [])
                    )

        description = str(product.get("description") or "")
        description_color_match = re.search(
            r"-\s*(?:Màu sắc|Màu)\s*:\s*([^-]+)",
            description,
            flags=re.IGNORECASE,
        )
        if description_color_match:
            colors.extend(
                color.strip()
                for color in description_color_match.group(1).split(",")
                if color.strip()
            )

        def description_spec(label: str) -> str | None:
            match = re.search(
                rf"-\s*(?:{label})\s*:\s*([^-]+)",
                description,
                flags=re.IGNORECASE,
            )
            if not match:
                return None
            value = match.group(1).strip()
            return value or None

        material = description_spec(r"Chất liệu")
        sole = description_spec(r"Đế")
        height = description_spec(r"Cao|Chiều cao")

        normalized_code = product_code.strip().upper()

        image_urls = []
        image_urls_by_color: dict[str, list[str]] = {}

        for related_product in related_products:
            product_images = []
            related_featured_image = (
                related_product.get("featuredImage") or {}
            ).get("url")

            if related_featured_image:
                product_images.append(
                    str(related_featured_image)
                )

            for image in (
                related_product
                .get("images", {})
                .get("nodes", [])
            ):
                image_url = image.get("url")
                if (
                    image_url
                    and image_url not in product_images
                ):
                    product_images.append(str(image_url))

            product_colors = []
            for option in related_product.get("options", []):
                if str(option.get("name", "")).casefold() == "color":
                    product_colors.extend(
                        str(value)
                        for value in option.get("values", [])
                    )

            for color in product_colors:
                existing_images = image_urls_by_color.setdefault(
                    color,
                    [],
                )
                for image_url in product_images:
                    if image_url not in existing_images:
                        existing_images.append(image_url)

            for image_url in product_images:
                if image_url not in image_urls:
                    image_urls.append(image_url)

        featured_image = (
            product.get("featuredImage") or {}
        ).get("url")

        return {
            "product_code": normalized_code,
            "product_name": product.get("title"),
            "product_type": product.get("productType"),
            "description": description,
            "material": material,
            "sole": sole,
            "height": height,
            "status": product.get("status"),
            "prices": prices,
            "colors": list(dict.fromkeys(colors)),
            "available_sizes": sorted(set(available_sizes)),
            "availability_by_color": availability_by_color,
            "featured_image": featured_image,
            "image_urls": image_urls[:4],
            "image_urls_by_color": {
                color: urls[:4]
                for color, urls in image_urls_by_color.items()
            },
            "handle": product.get("handle"),
        }
