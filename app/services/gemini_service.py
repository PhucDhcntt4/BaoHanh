import os
from typing import Any

from google import genai
from google.genai import types # type: ignore

from app.config import WARRANTY_PROMPT_PATH
from app.services.warranty_tools import (
    activate_warranty,
    search_order,
)


class GeminiService:
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

        if not WARRANTY_PROMPT_PATH.exists():
            raise RuntimeError(
                f"Không tìm thấy prompt: {WARRANTY_PROMPT_PATH}"
            )

        self.system_prompt = WARRANTY_PROMPT_PATH.read_text(
            encoding="utf-8"
        )

        self.tools = [
            search_order,
            activate_warranty,
        ]

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
