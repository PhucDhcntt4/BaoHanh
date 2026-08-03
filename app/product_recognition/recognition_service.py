import json
import threading

import requests
from google import genai
from google.genai import types # type: ignore

from app.config import PRODUCT_RECOGNITION_PROMPT_PATH
from app.product_recognition.catalog_service import (
    ProductCatalogService,
)
from app.product_recognition.models import (
    ProductRecognitionResult,
)
from app.services.product_image_store import (
    ProductImageStore,
)


class ProductRecognitionService:
    def __init__(
        self,
        client: genai.Client,
        model: str,
        catalog: ProductCatalogService,
    ) -> None:
        self.client = client
        self.model = model
        self.catalog = catalog
        self.prompt = PRODUCT_RECOGNITION_PROMPT_PATH.read_text(
            encoding="utf-8"
        )
        self._image_cache: dict[str, tuple[bytes, str]] = {}
        self._cache_lock = threading.Lock()
        self.image_store = ProductImageStore()

    def _reference_image(
        self,
        url: str,
    ) -> tuple[bytes, str]:
        with self._cache_lock:
            cached = self._image_cache.get(url)

        if cached:
            return cached

        local_image = self.image_store.get(url)

        if local_image:
            with self._cache_lock:
                self._image_cache[url] = local_image

            return local_image

        response = requests.get(
            url,
            timeout=30,
        )

        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type",
            "image/jpeg",
        ).split(";")[0]

        result = (
            response.content,
            content_type,
        )

        with self._cache_lock:
            self._image_cache[url] = result

        return result

    def recognize(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str = "unknown",
    ) -> ProductRecognitionResult:
        contents: list = [
            self.prompt,
            "CUSTOMER IMAGE:",
            types.Part.from_bytes(
                data=image_bytes,
                mime_type=mime_type,
            ),
        ]

        valid_codes = set()
        reference_limit = (
            5 if product_type != "unknown"
            else 15
        )
        for reference in self.catalog.reference_products(
            product_type=(
                None
                if product_type == "unknown"
                else product_type
            ),
            limit=reference_limit,
        ):
            code = reference["product_code"]
            loaded_images = 0
            for image_index, image_url in enumerate(
                reference["image_urls"], start=1
            ):
                try:
                    reference_bytes, reference_mime = (
                        self._reference_image(image_url)
                    )
                except requests.RequestException:
                    continue
                loaded_images += 1
                contents.extend([
                    (
                        f"REFERENCE product_code={code}; "
                        f"title={reference['title']}; view={image_index}"
                    ),
                    types.Part.from_bytes(
                        data=reference_bytes,
                        mime_type=reference_mime,
                    ),
                ])
            if loaded_images:
                valid_codes.add(code)

        if not valid_codes:
            raise RuntimeError("Không tải được ảnh catalog")

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        if not response.text:
            return ProductRecognitionResult()

        try:
            parsed = ProductRecognitionResult.model_validate(
                json.loads(response.text)
            )
        except (json.JSONDecodeError, ValueError):
            return ProductRecognitionResult()

        filtered = [
            candidate
            for candidate in parsed.candidates
            if candidate.product_code.upper() in valid_codes
        ]
        filtered.sort(
            key=lambda item: item.confidence,
            reverse=True,
        )
        return ProductRecognitionResult(candidates=filtered[:3])
