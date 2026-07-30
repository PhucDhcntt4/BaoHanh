import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from app.config import PRODUCTS_PATH


class ProductCatalogService:
    def __init__(
        self,
        path: str | Path = PRODUCTS_PATH,
    ) -> None:
        self.path = Path(path)
        self._products = self._load()
        self._by_code: dict[str, dict[str, Any]] = {}
        for product in self._products:
            for code in self.product_codes(product):
                self._by_code[code] = product

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
        limit: int = 20,
    ) -> list[dict[str, str]]:
        references = []
        for product in self._products:
            code = self.product_code(product)
            image_url = (
                product.get("featuredImage") or {}
            ).get("url")
            if code and image_url:
                references.append(
                    {
                        "product_code": code,
                        "title": str(product.get("title", "")),
                        "image_url": str(image_url),
                    }
                )
            if len(references) >= limit:
                break
        return references

    def get(self, product_code: str) -> dict[str, Any] | None:
        return self._by_code.get(product_code.strip().upper())

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
            info = self.public_info(code)
            if info:
                scored_products.append((score, info))

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
        product = self.get(product_code)
        if not product:
            return None

        variants = product.get("variants", {}).get("nodes", [])
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
        for option in product.get("options", []):
            if str(option.get("name", "")).casefold() == "color":
                colors.extend(str(value) for value in option.get("values", []))

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

        normalized_code = product_code.strip().upper()

        image_urls = []

        featured_image = (
            product.get("featuredImage") or {}
        ).get("url")

        if featured_image:
            image_urls.append(str(featured_image))

        for image in product.get("images", {}).get("nodes", []):
            image_url = image.get("url")

            if image_url and image_url not in image_urls:
                image_urls.append(str(image_url))

        return {
            "product_code": normalized_code,
            "product_name": product.get("title"),
            "product_type": product.get("productType"),
            "description": description,
            "status": product.get("status"),
            "prices": prices,
            "colors": list(dict.fromkeys(colors)),
            "available_sizes": sorted(set(available_sizes)),
            "availability_by_color": availability_by_color,
            "featured_image": featured_image,
            "image_urls": image_urls[:4],
            "handle": product.get("handle"),
        }
