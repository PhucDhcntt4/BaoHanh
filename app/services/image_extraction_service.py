import json
import re

from google import genai
from google.genai import types # type: ignore

from app.config import IMAGE_ORDER_EXTRACTION_PROMPT_PATH
from app.models import ImageOrderInfo
from app.services.order_service import (
    normalize_order_code,
    normalize_phone,
)


class ImageExtractionService:
    def __init__(
        self,
        client: genai.Client,
        model: str,
    ) -> None:
        self.client = client
        self.model = model

        if not IMAGE_ORDER_EXTRACTION_PROMPT_PATH.exists():
            raise RuntimeError(
                "Không tìm thấy prompt đọc thông tin đơn hàng: "
                f"{IMAGE_ORDER_EXTRACTION_PROMPT_PATH}"
            )

        self.prompt = IMAGE_ORDER_EXTRACTION_PROMPT_PATH.read_text(
            encoding="utf-8"
        ).strip()

    def extract(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict:
        if not image_bytes:
            raise ValueError("Ảnh không có dữ liệu")

        allowed_mime_types = {
            "image/jpeg",
            "image/png",
            "image/webp",
        }

        if mime_type not in allowed_mime_types:
            raise ValueError("Định dạng ảnh không được hỗ trợ")

        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type=mime_type,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                self.prompt,
                image_part,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=ImageOrderInfo,
                temperature=0,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini không trả kết quả đọc ảnh")

        data = json.loads(response.text)
        extracted = ImageOrderInfo.model_validate(data)
        masked_phone = re.sub(
            r"[^0-9*xX]",
            "",
            extracted.masked_phone or "",
        ) or None

        return {
            "phone": normalize_phone(extracted.phone),
            "masked_phone": masked_phone,
            "order_code": normalize_order_code(
                extracted.order_code
            ),
            "phone_confident": extracted.phone_confident,
            "masked_phone_confident": (
                extracted.masked_phone_confident
            ),
            "order_code_confident": extracted.order_code_confident,
        }
