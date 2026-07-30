import json
import re

from google import genai
from google.genai import types # type: ignore

from app.config import PRODUCT_REPLY_PROMPT_PATH
from app.product_recognition.catalog_service import (
    ProductCatalogService,
)
from app.product_recognition.recognition_service import (
    ProductRecognitionService,
)


class ProductImageHandler:
    def __init__(
        self,
        client: genai.Client,
        model: str,
    ) -> None:
        self.client = client
        self.model = model
        self.catalog = ProductCatalogService()
        self.recognition = ProductRecognitionService(
            client=client,
            model=model,
            catalog=self.catalog,
        )
        self.reply_prompt = PRODUCT_REPLY_PROMPT_PATH.read_text(
            encoding="utf-8"
        )

    def handle(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict:
        recognition = self.recognition.recognize(
            image_bytes=image_bytes,
            mime_type=mime_type,
        )
        candidates = []

        for candidate in recognition.candidates:
            if candidate.confidence < 0.70:
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

        if not candidates:
            return {
                "reply": (
                    "Dạ em chưa nhận diện chính xác được sản phẩm trong "
                    "ảnh này. Anh/chị gửi giúp em mã sản phẩm hoặc một "
                    "ảnh rõ hơn, chụp trọn sản phẩm ở góc khác để em "
                    "kiểm tra lại nhé. 😊"
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
        }
