import json
import logging
import re

from google import genai
from google.genai import types # type: ignore

from app.config import (
    PRODUCT_REPLY_PROMPT_PATH,
    PRODUCT_VECTOR_SEARCH_ENABLED,
    VECTOR_MIN_MARGIN,
    VECTOR_SEARCH_LIMIT,
)
from app.database.product_embedding_repository import (
    ProductEmbeddingRepository,
)
from app.product_recognition.catalog_service import (
    ProductCatalogService,
)
from app.product_recognition.recognition_service import (
    ProductRecognitionService,
)
from app.product_recognition.models import (
    MIN_PRODUCT_MATCH_CONFIDENCE,
)
from app.product_recognition.image_embedding_service import (
    ImageEmbeddingService,
)
from app.product_recognition.vector_decision import decide_vector_match
from app.product_recognition.product_type_groups import (
    equivalent_product_types,
)


logger = logging.getLogger("uvicorn.error")


class ProductImageHandler:
    def __init__(
        self,
        client: genai.Client,
        model: str,
        catalog: ProductCatalogService | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.catalog = catalog or ProductCatalogService()
        self.recognition = ProductRecognitionService(
            client=client,
            model=model,
            catalog=self.catalog,
        )
        self.reply_prompt = PRODUCT_REPLY_PROMPT_PATH.read_text(
            encoding="utf-8"
        )
        self.vector_enabled = PRODUCT_VECTOR_SEARCH_ENABLED
        self.embedding_service = None
        self.embedding_repository = None
        if self.vector_enabled:
            try:
                self.embedding_service = ImageEmbeddingService()
                self.embedding_repository = ProductEmbeddingRepository()
            except Exception:
                logger.exception(
                    "VECTOR INITIALIZATION ERROR; fallback=legacy_gemini"
                )
                self.vector_enabled = False

    def _match_with_vector(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str,
        original_image_bytes: bytes | None = None,
        original_mime_type: str | None = None,
    ) -> list[dict]:
        """Global vector retrieval plus an additive product-type recall lane."""

        if not self.embedding_service or not self.embedding_repository:
            return []

        embedding = self.embedding_service.embed_bytes(image_bytes)

        # Primary search is always global. The classifier never excludes a
        # product from this result set.
        rows = self.embedding_repository.search(
            embedding=embedding,
            model_name=self.embedding_service.model_name,
            pretrained_name=self.embedding_service.pretrained_name,
            product_type=None,
            limit=VECTOR_SEARCH_LIMIT,
        )

        global_row_count = len(rows)
        type_row_count = 0
        type_decision = None

        # The classified family is an additive recall lane for screenshots or
        # lifestyle photos whose catalog match ranks below the global limit.
        if product_type != "unknown":
            type_rows = self.embedding_repository.search(
                embedding=embedding,
                model_name=self.embedding_service.model_name,
                pretrained_name=self.embedding_service.pretrained_name,
                product_type=equivalent_product_types(product_type),
                limit=max(10, VECTOR_SEARCH_LIMIT // 2),
            )
            type_row_count = len(type_rows)
            type_decision = decide_vector_match(type_rows)

            merged_rows: dict[object, dict] = {}
            for row_index, row in enumerate([*rows, *type_rows]):
                identity = (
                    row.get("product_image_id")
                    or row.get("source_url")
                    or ("row", row_index)
                )
                current = merged_rows.get(identity)
                if (
                    current is None
                    or float(row.get("similarity") or 0)
                    > float(current.get("similarity") or 0)
                ):
                    merged_rows[identity] = row
            rows = sorted(
                merged_rows.values(),
                key=lambda item: float(item.get("similarity") or 0),
                reverse=True,
            )

        logger.info(
            "VECTOR RETRIEVAL classified_type=%s global_images=%s "
            "type_images=%s merged_images=%s",
            product_type,
            global_row_count,
            type_row_count,
            len(rows),
        )

        decision = decide_vector_match(rows)
        verification_candidates = list(decision.candidates)
        type_candidate = (
            type_decision.best_candidate
            if (
                type_decision is not None
                and type_decision.status != "no_match"
            )
            else None
        )
        if type_candidate and all(
            item.get("product_code") != type_candidate.get("product_code")
            for item in verification_candidates
        ):
            verification_candidates.append(type_candidate)

        logger.info(
            "VECTOR PRODUCT CANDIDATES classified_type=%s status=%s "
            "top=%.4f margin=%.4f values=%s",
            product_type,
            decision.status,
            decision.top_similarity,
            decision.margin,
            [
                {
                    "code": item["product_code"],
                    "type": item.get("product_type"),
                    "color": item.get("color"),
                    "similarity": round(float(item["similarity"]), 4),
                }
                for item in verification_candidates
            ],
        )
        if decision.status == "no_match":
            return []

        candidate_codes = [
            item["product_code"] for item in verification_candidates
        ]
        verification = self.recognition.verify_vector_candidates(
            image_bytes=image_bytes,
            mime_type=mime_type,
            candidate_rows=rows,
            candidate_codes=candidate_codes,
            original_image_bytes=original_image_bytes,
            original_mime_type=original_mime_type,
        )
        logger.info(
            "VECTOR CANDIDATE VERIFIED exact=%s code=%s confidence=%.3f "
            "reason=%s",
            verification.exact_match,
            verification.product_code,
            verification.confidence,
            verification.reason,
        )

        # A near tie means the joint verifier is comparing very similar
        # products. If it rejects the vector leader and selects another code,
        # validate the leader alone with its complete catalog image set before
        # committing to the other product.
        top_candidate = decision.best_candidate
        top_code = (
            str(top_candidate.get("product_code") or "").strip().upper()
            if top_candidate
            else ""
        )
        selected_code = str(
            verification.product_code or ""
        ).strip().upper()
        if (
            verification.exact_match
            and selected_code
            and top_code
            and selected_code != top_code
            and decision.margin < VECTOR_MIN_MARGIN
        ):
            top_verification = self.recognition.verify_exact_match(
                image_bytes=original_image_bytes or image_bytes,
                mime_type=original_mime_type or mime_type,
                product_code=top_code,
            )
            top_recheck_details = (
                "; ".join(top_verification.mismatches)
                if top_verification.mismatches
                else (
                    "matched_reference="
                    f"{top_verification.matched_reference}"
                )
            )
            logger.info(
                "VECTOR TOP RECHECK code=%s exact=%s confidence=%.3f "
                "details=%s",
                top_code,
                top_verification.exact_match,
                top_verification.confidence,
                top_recheck_details,
            )
            if (
                top_verification.exact_match
                and top_verification.confidence >= 0.90
            ):
                verification.exact_match = True
                verification.product_code = top_code
                verification.confidence = top_verification.confidence
                verification.reason = (
                    "Vector leader confirmed by dedicated recheck: "
                    f"{top_recheck_details}"
                )

        if (
            not verification.exact_match
            or not verification.product_code
            or verification.confidence < 0.90
        ):
            return []

        product = self.catalog.public_info(verification.product_code)
        if not product:
            return []
        return [{
            "confidence": verification.confidence,
            "visual_reason": verification.reason,
            "product": product,
        }]

    def _match_with_legacy_gemini(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str,
    ) -> list[dict]:
        """Luồng cũ, chỉ dùng khi vector bị tắt hoặc bị lỗi."""

        recognition = self.recognition.recognize(
            image_bytes=image_bytes,
            mime_type=mime_type,
            product_type=product_type,
        )

        # Bộ phân loại nhanh có thể nhầm giữa giày, dép, sandal và
        # sapo. Nếu danh sách đã lọc không cho kết quả đủ tin cậy,
        # thử lại bằng tập tham chiếu rộng hơn trước khi báo thất bại.
        if (
            product_type != "unknown"
            and not any(
                candidate.confidence >= MIN_PRODUCT_MATCH_CONFIDENCE
                for candidate in recognition.candidates
            )
        ):
            recognition = self.recognition.recognize(
                image_bytes=image_bytes,
                mime_type=mime_type,
                product_type="unknown",
            )
        candidates: list[dict] = []
        verification_candidates = sorted(
            (
                candidate
                for candidate in recognition.candidates
                if candidate.confidence >= 0.70
            ),
            key=lambda item: item.confidence,
            reverse=True,
        )[:3]
        logger.info(
            "PRODUCT CANDIDATES values=%s",
            [
                {
                    "code": candidate.product_code,
                    "confidence": candidate.confidence,
                    "reason": candidate.reason,
                }
                for candidate in recognition.candidates
            ],
        )

        for candidate in verification_candidates:
            verification = self.recognition.verify_exact_match(
                image_bytes=image_bytes,
                mime_type=mime_type,
                product_code=candidate.product_code,
            )
            logger.info(
                "PRODUCT MATCH VERIFIED code=%s exact=%s confidence=%.3f "
                "matched_reference=%s mismatches=%s",
                candidate.product_code,
                verification.exact_match,
                verification.confidence,
                verification.matched_reference,
                verification.mismatches,
            )
            if (
                not verification.exact_match
                or verification.confidence < 0.90
            ):
                continue
            product = self.catalog.public_info(
                candidate.product_code
            )
            if product:
                candidates.append(
                    {
                        "confidence": candidate.confidence,
                        "visual_reason": candidate.reason,
                        "product": product,
                    }
                )
                break

        return candidates

    def handle(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str = "unknown",
        original_image_bytes: bytes | None = None,
        original_mime_type: str | None = None,
    ) -> dict:
        candidates: list[dict]
        if self.vector_enabled:
            try:
                candidates = self._match_with_vector(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    product_type=product_type,
                    original_image_bytes=original_image_bytes,
                    original_mime_type=original_mime_type,
                )
            except Exception:
                logger.exception(
                    "VECTOR PRODUCT SEARCH ERROR; fallback=legacy_gemini"
                )
                candidates = self._match_with_legacy_gemini(
                    image_bytes=image_bytes,
                    mime_type=mime_type,
                    product_type=product_type,
                )
        else:
            candidates = self._match_with_legacy_gemini(
                image_bytes=image_bytes,
                mime_type=mime_type,
                product_type=product_type,
            )

        if not candidates:
            return {
                "reply": (
                    "Dạ, em chưa tìm thấy sản phẩm khớp với hình ảnh này "
                    "trong hệ thống. Anh/chị có thể gửi mã sản phẩm hoặc "
                    "một ảnh rõ hơn để em kiểm tra lại nhé. 😊"
                ),
                "product_codes": [],
            }

        payload = {
            "status": (
                "candidates_found"
                if candidates
                else "not_confident"
            ),
            "candidates": candidates,
        }
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(
                payload,
                ensure_ascii=False,
            ),
            config=types.GenerateContentConfig(
                system_instruction=self.reply_prompt,
                temperature=0.2,
            ),
        )
        allowed_codes = {
            item["product"]["product_code"]
            for item in candidates
        }
        if response.text:
            reply = response.text.strip()
            mentioned_codes = set(
                re.findall(
                    r"\b[A-Z]\d{3,}[A-Z0-9]*\b",
                    reply.upper(),
                )
            )
            if mentioned_codes.issubset(allowed_codes):
                return {
                    "reply": reply,
                    "product_codes": sorted(allowed_codes),
                    "follow_up": (
                        "Anh/chị cần tư vấn thêm hay muốn đặt "
                        "hàng mẫu này không ạ? ❤️"
                    ),
                }

        top = candidates[0]
        product = top["product"]
        confidence = top["confidence"]
        wording = (
            "em nhận diện được"
            if confidence >= 0.90
            else "sản phẩm trong ảnh khá giống"
        )
        return {
            "reply": (
                f"Dạ {wording} mẫu {product['product_name']}, "
                f"mã {product['product_code']} ạ."
            ),
            "product_codes": [product["product_code"]],
            "follow_up": (
                "Anh/chị cần tư vấn thêm hay muốn đặt "
                "hàng mẫu này không ạ? ❤️"
            ),
        }
