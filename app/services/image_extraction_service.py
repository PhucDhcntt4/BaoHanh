import json

from google import genai
from google.genai import types # type: ignore

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
                image_part,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini không trả kết quả đọc ảnh")

        data = json.loads(response.text)
        extracted = ImageOrderInfo.model_validate(data)

        return {
            "phone": normalize_phone(extracted.phone),
            "order_code": normalize_order_code(
                extracted.order_code
            ),
            "phone_confident": extracted.phone_confident,
            "order_code_confident": extracted.order_code_confident,
        }
