import json

from google import genai
from google.genai import types # type: ignore

from app.config import IMAGE_INTENT_PROMPT_PATH
from app.product_recognition.models import ImageIntent


class ImageIntentService:
    def __init__(
        self,
        client: genai.Client,
        model: str,
    ) -> None:
        self.client = client
        self.model = model
        self.prompt = IMAGE_INTENT_PROMPT_PATH.read_text(
            encoding="utf-8"
        )

    def classify(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(
                    data=image_bytes,
                    mime_type=mime_type,
                ),
                f"Caption khách gửi: {caption or '(không có)'}",
            ],
            config=types.GenerateContentConfig(
                system_instruction=self.prompt,
                response_mime_type="application/json",
                temperature=0,
            ),
        )
        if not response.text:
            return "unknown"
        try:
            result = ImageIntent.model_validate(
                json.loads(response.text)
            )
        except (json.JSONDecodeError, ValueError):
            return "unknown"
        return result.intent
