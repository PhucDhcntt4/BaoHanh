import json
import threading
from io import BytesIO

import requests
from google import genai
from google.genai import types # type: ignore
from PIL import Image, UnidentifiedImageError

from app.config import PRODUCT_RECOGNITION_PROMPT_PATH
from app.product_recognition.catalog_service import (
    ProductCatalogService,
)
from app.product_recognition.models import (
    ProductMatchVerification,
    ProductRecognitionResult,
    VectorCandidateVerification,
)
from app.services.product_image_store import (
    ProductImageStore,
)


class ProductRecognitionService:
    VECTOR_REFERENCES_PER_PRODUCT = 4

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

    @staticmethod
    def _difference_hash(image_bytes: bytes) -> int | None:
        """Tạo dHash 64-bit, bền với resize và nén ảnh nhẹ."""

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                pixels = list(
                    image.convert("L").resize((9, 8)).getdata()
                )
        except (UnidentifiedImageError, OSError, ValueError):
            return None

        value = 0
        for row in range(8):
            offset = row * 9
            for column in range(8):
                value <<= 1
                if pixels[offset + column] > pixels[offset + column + 1]:
                    value |= 1
        return value

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
            else 50
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

    def verify_exact_match(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_code: str,
    ) -> ProductMatchVerification:
        product = self.catalog.public_info(product_code)
        if not product:
            return ProductMatchVerification()

        contents: list = [
            (
                "Xác minh ảnh CUSTOMER có phải đúng cùng một mẫu sản phẩm "
                f"mã {product_code} hay không. Không chỉ kiểm tra cùng loại "
                "hoặc cùng màu. Phải so sánh cấu trúc thân, kiểu quai, mũi, "
                "đế, gót, đường may, khóa kéo, logo và các chi tiết trang trí. "
                "Nền ảnh, chữ quảng cáo và giao diện website không phải bằng "
                "chứng cùng mẫu. Hãy so sánh CUSTOMER riêng với TỪNG ảnh "
                "REFERENCE. Chỉ cần khớp rõ với ít nhất một REFERENCE thì "
                "exact_match=true và ghi số ảnh đó vào matched_reference. "
                "Không được phủ nhận một ảnh đã khớp chỉ vì REFERENCE khác "
                "có góc chụp, màu hoặc phụ kiện tháo rời khác. Chỉ đặt "
                "exact_match=true và confidence>=0.90 khi gần như chắc chắn "
                "là cùng một mẫu."
            ),
            "CUSTOMER IMAGE:",
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        ]
        loaded = 0
        for reference_index, image_url in enumerate(
            product.get("image_urls") or [],
            start=1,
        ):
            try:
                reference_bytes, reference_mime = self._reference_image(
                    str(image_url)
                )
            except requests.RequestException:
                continue
            loaded += 1
            contents.extend([
                (
                    f"REFERENCE {reference_index} "
                    f"FOR product_code={product_code}:"
                ),
                types.Part.from_bytes(
                    data=reference_bytes,
                    mime_type=reference_mime,
                ),
            ])
        if not loaded:
            return ProductMatchVerification()

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ProductMatchVerification,
                temperature=0,
            ),
        )
        if not response.text:
            return ProductMatchVerification()
        try:
            return ProductMatchVerification.model_validate_json(
                response.text
            )
        except ValueError:
            return ProductMatchVerification()

    def verify_vector_candidates(
        self,
        image_bytes: bytes,
        mime_type: str,
        candidate_rows: list[dict],
        candidate_codes: list[str],
        original_image_bytes: bytes | None = None,
        original_mime_type: str | None = None,
    ) -> VectorCandidateVerification:
        """Gemini xác minh một lần trên shortlist từ pgvector."""

        allowed_codes = {
            str(code).strip().upper()
            for code in candidate_codes
            if str(code).strip()
        }
        if not allowed_codes:
            return VectorCandidateVerification()

        contents: list = [
            (
                "Hãy xác minh CUSTOMER IMAGE với các sản phẩm "
                "ứng viên do hệ thống tìm kiếm ảnh cung cấp. "
                "So sánh chi tiết hình dáng, thân, nắp, quai, "
                "khóa, logo, đường may, mũi, đế, gót và trang trí. "
                "Bỏ qua phông nền, người mẫu và chữ quảng cáo. "
                "Tuy nhiên, nếu CUSTOMER ORIGINAL và một REFERENCE là "
                "cùng một bức ảnh hoặc cùng cảnh chụp, chỉ khác do "
                "crop, resize hoặc nén, thì đó là bằng chứng quyết định "
                "cho product_code của REFERENCE đó. Không được chọn "
                "mẫu khác chỉ vì cùng có phụ kiện hình con vật; "
                "phải ưu tiên cấu trúc thân túi, nắp, miệng túi, "
                "vị trí quai, khóa và đường may. "
                "Màu hoặc góc chụp khác nhau không có nghĩa là "
                "khác mẫu. Chỉ chọn một product_code khi có đủ "
                "chi tiết đặc trưng trùng khớp. Nếu không ứng viên "
                "nào khớp rõ, trả exact_match=false, product_code=null. "
                "Không được trả mã ngoài danh sách: "
                f"{sorted(allowed_codes)}."
            ),
            "CUSTOMER CROPPED IMAGE:",
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        ]

        if original_image_bytes:
            contents.extend([
                "CUSTOMER ORIGINAL IMAGE:",
                types.Part.from_bytes(
                    data=original_image_bytes,
                    mime_type=original_mime_type or mime_type,
                ),
            ])

        rows_per_code: dict[str, list[dict]] = {
            code: [] for code in allowed_codes
        }
        for row in candidate_rows:
            code = str(row.get("product_code") or "").strip().upper()
            if code in allowed_codes and row.get("source_url"):
                rows_per_code[code].append(row)

        # Give Gemini diverse evidence: first one strong image per color,
        # then fill the remaining slots with other high-scoring angles.
        selected_rows: list[dict] = []
        for candidate_code in candidate_codes:
            code = str(candidate_code).strip().upper()
            rows_for_code = rows_per_code.get(code, [])
            rows_for_code.sort(
                key=lambda item: float(item.get("similarity") or 0),
                reverse=True,
            )
            chosen: list[dict] = []
            seen_urls: set[str] = set()
            seen_colors: set[str] = set()
            for row in rows_for_code:
                url = str(row.get("source_url") or "").strip()
                color = str(row.get("color") or "").strip().casefold()
                if not url or url in seen_urls or color in seen_colors:
                    continue
                chosen.append(row)
                seen_urls.add(url)
                seen_colors.add(color)
                if len(chosen) >= self.VECTOR_REFERENCES_PER_PRODUCT:
                    break
            for row in rows_for_code:
                if len(chosen) >= self.VECTOR_REFERENCES_PER_PRODUCT:
                    break
                url = str(row.get("source_url") or "").strip()
                if not url or url in seen_urls:
                    continue
                chosen.append(row)
                seen_urls.add(url)

            # The global vector LIMIT may contain fewer than four rows for a
            # candidate. Complete its evidence from the official catalog so a
            # correct code is not judged from only one accidental angle.
            if len(chosen) < self.VECTOR_REFERENCES_PER_PRODUCT:
                product = self.catalog.public_info(code) or {}
                for image_url in product.get("image_urls") or []:
                    if len(chosen) >= self.VECTOR_REFERENCES_PER_PRODUCT:
                        break
                    url = str(image_url or "").strip()
                    if not url or url in seen_urls:
                        continue
                    chosen.append({
                        "product_code": code,
                        "source_url": url,
                        "color": "",
                        "similarity": 0.0,
                    })
                    seen_urls.add(url)
            selected_rows.extend(chosen)

        loaded_per_code: dict[str, int] = {}
        original_hash = (
            self._difference_hash(original_image_bytes)
            if original_image_bytes
            else None
        )
        near_duplicate: tuple[int, str] | None = None
        for row in selected_rows:
            code = str(row.get("product_code") or "").strip().upper()
            if code not in allowed_codes:
                continue
            image_url = str(row.get("source_url") or "").strip()
            if not image_url:
                continue
            try:
                reference_bytes, reference_mime = self._reference_image(
                    image_url
                )
            except requests.RequestException:
                continue
            if original_hash is not None:
                reference_hash = self._difference_hash(reference_bytes)
                if reference_hash is not None:
                    distance = (original_hash ^ reference_hash).bit_count()
                    if near_duplicate is None or distance < near_duplicate[0]:
                        near_duplicate = (distance, code)
            loaded_per_code[code] = loaded_per_code.get(code, 0) + 1
            contents.extend([
                (
                    f"CANDIDATE product_code={code}; "
                    f"color={row.get('color') or ''}; "
                    f"vector_similarity={float(row.get('similarity') or 0):.4f}; "
                    f"reference={loaded_per_code[code]}"
                ),
                types.Part.from_bytes(
                    data=reference_bytes,
                    mime_type=reference_mime,
                ),
            ])

        if not loaded_per_code:
            return VectorCandidateVerification()

        # dHash <= 3/64 cho thấy đây gần như là cùng một ảnh,
        # có thể chỉ khác do Telegram resize hoặc nén lại.
        if near_duplicate is not None and near_duplicate[0] <= 3:
            distance, matched_code = near_duplicate
            return VectorCandidateVerification(
                exact_match=True,
                product_code=matched_code,
                confidence=1.0,
                reason=(
                    "Near-duplicate catalog image matched by dHash "
                    f"(distance={distance}/64)."
                ),
            )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=VectorCandidateVerification,
                temperature=0,
            ),
        )
        if not response.text:
            return VectorCandidateVerification()
        try:
            result = VectorCandidateVerification.model_validate_json(
                response.text
            )
        except ValueError:
            return VectorCandidateVerification()

        selected_code = str(result.product_code or "").strip().upper()
        if not result.exact_match or selected_code not in allowed_codes:
            return VectorCandidateVerification(
                exact_match=False,
                confidence=result.confidence,
                reason=result.reason,
            )
        result.product_code = selected_code
        return result
