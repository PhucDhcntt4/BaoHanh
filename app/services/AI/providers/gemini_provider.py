import json
import os
from typing import Any

from google import genai
from google.genai import types # type: ignore

from app.config import (
    CUSTOMER_AGENT_PROMPT_PATH,
    PRODUCT_IMAGE_REQUEST_PROMPT_PATH,
)
from app.models import (
    ProductImageRequestIntent,
)
from app.product_recognition.product_tools import (
    get_product_info,
    search_products,
)
from app.product_recognition.catalog_service import (
    ProductCatalogService,
)
from app.product_recognition.handler import ProductImageHandler
from app.product_recognition.image_intent_service import (
    ImageIntentService,
)
from app.services.AI.base import AIService
from app.services.policy_tools import (
    search_warranty_policy,
)


class GeminiProvider(AIService):
    provider_name = "gemini"

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "Thiếu GEMINI_API_KEY trong file .env"
            )

        self.client = genai.Client(
            api_key=api_key
        )

        self.model = os.getenv(
            "GEMINI_MODEL",
            "gemini-2.5-flash",
        )

        if not CUSTOMER_AGENT_PROMPT_PATH.exists():
            raise RuntimeError(
                f"Không tìm thấy prompt: {CUSTOMER_AGENT_PROMPT_PATH}"
            )

        self.system_prompt = CUSTOMER_AGENT_PROMPT_PATH.read_text(
            encoding="utf-8"
        )

        if not PRODUCT_IMAGE_REQUEST_PROMPT_PATH.exists():
            raise RuntimeError(
                "Không tìm thấy prompt phân loại yêu cầu ảnh: "
                f"{PRODUCT_IMAGE_REQUEST_PROMPT_PATH}"
            )

        self.product_image_request_prompt = (
            PRODUCT_IMAGE_REQUEST_PROMPT_PATH.read_text(
                encoding="utf-8"
            )
        )

        self.tools = [
            search_warranty_policy,
            search_products,
            get_product_info,
        ]
        self.catalog = ProductCatalogService()
        self.image_intent_service = ImageIntentService(
            client=self.client,
            model=self.model,
            catalog=self.catalog,
        )
        self.product_image_handler = ProductImageHandler(
            client=self.client,
            model=self.model,
            catalog=self.catalog,
        )

    def chat(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Gửi tin nhắn cho Gemini Agent.

        Gemini tự:
        - Hiểu yêu cầu
        - Chọn tool
        - Nhận kết quả tool
        - Viết câu trả lời cuối cùng
        """

        contents = self._build_contents(
            message=message,
            customer_id=customer_id,
            history=history,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=self.tools,
                temperature=0.2,
                automatic_function_calling=(
                    types.AutomaticFunctionCallingConfig(
                        disable=False,
                        maximum_remote_calls=5,
                    )
                ),
            ),
        )

        reply = response.text

        if not reply:
            reply = (
                "Dạ hiện tại em chưa thể xử lý yêu cầu. "
                "Anh/chị vui lòng thử lại sau ít phút ạ."
            )

        return {
            "success": True,
            "reply": reply.strip(),
        }

    def compose_reply(
        self,
        event: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """
        Viết câu trả lời tự nhiên từ một kết quả nghiệp vụ đã được
        Python xác minh. Hàm này không đăng ký tools nên chỉ
        có thể diễn đạt lại dữ liệu đã được cung cấp.
        """

        contents: list[types.Content] = []

        if history:
            for item in history:
                role = item.get("role")
                text = item.get("text", "")

                if role in {"user", "model"} and text:
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[
                                types.Part.from_text(text=text)
                            ],
                        )
                    )

        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=(
                            "KẾT QUẢ NGHIỆP VỤ ĐÃ ĐƯỢC HỆ THỐNG "
                            "XÁC MINH:\n"
                            f"{event}\n\n"
                            "Hãy viết một câu trả lời ngắn gọn, tự "
                            "nhiên bằng tiếng Việt cho khách hàng. "
                            "Chỉ dùng dữ liệu trên, không tự thêm "
                            "thông tin và không mô tả kỹ thuật."
                        )
                    )
                ],
            )
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                temperature=0.3,
            ),
        )

        if not response.text:
            raise RuntimeError("Gemini không tạo được câu trả lời")

        return response.text.strip()

    def classify_product_image_request(
        self,
        message: str,
    ) -> bool:
        response = self.client.models.generate_content(
            model=self.model,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=self.product_image_request_prompt,
                response_mime_type="application/json",
                temperature=0,
            ),
        )

        if not response.text:
            return False

        try:
            parsed = ProductImageRequestIntent.model_validate(
                json.loads(response.text)
            )
        except (json.JSONDecodeError, ValueError):
            return False

        return parsed.intent == "request_images"

    def classify_image_intent(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        return self.image_intent_service.classify(
            image_bytes=image_bytes,
            mime_type=mime_type,
            caption=caption,
        )

    def handle_product_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str = "unknown",
        original_image_bytes: bytes | None = None,
        original_mime_type: str | None = None,
    ) -> dict[str, Any]:
        return self.product_image_handler.handle(
            image_bytes=image_bytes,
            mime_type=mime_type,
            product_type=product_type,
            original_image_bytes=original_image_bytes,
            original_mime_type=original_mime_type,
        )

    def _build_contents(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None,
    ) -> list[types.Content]:
        """
        Chuyển lịch sử hội thoại và tin nhắn hiện tại
        thành định dạng Gemini Content.
        """

        contents: list[types.Content] = []

        if history:
            for item in history:
                role = item.get("role")
                text = item.get("text", "")

                if role not in {"user", "model"}:
                    continue

                if not text:
                    continue

                contents.append(
                    types.Content(
                        role=role,
                        parts=[
                            types.Part.from_text(
                                text=text
                            )
                        ],
                    )
                )

        current_message = (
            "THÔNG TIN HỆ THỐNG:\n"
            f"customer_id: {customer_id}\n\n"
            "TIN NHẮN KHÁCH HÀNG:\n"
            f"{message}"
        )

        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(
                        text=current_message
                    )
                ],
            )
        )

        return contents
