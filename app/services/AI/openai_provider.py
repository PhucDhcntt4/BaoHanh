import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import requests
from openai import OpenAI  # type: ignore

from app.config import (
    CONFIRMATION_PROMPT_PATH,
    IMAGE_INTENT_PROMPT_PATH,
    IMAGE_ORDER_EXTRACTION_PROMPT_PATH,
    PRODUCT_IMAGE_REQUEST_PROMPT_PATH,
    PRODUCT_RECOGNITION_PROMPT_PATH,
    PRODUCT_REPLY_PROMPT_PATH,
    WARRANTY_PROMPT_PATH,
)
from app.models import (
    ConfirmationIntent,
    ImageOrderInfo,
    ProductImageRequestIntent,
)
from app.product_recognition.catalog_service import ProductCatalogService
from app.product_recognition.models import (
    ImageIntent,
    ProductRecognitionResult,
)
from app.product_recognition.product_tools import (
    get_product_info,
    search_products,
)
from app.services.AI.base import AIService
from app.services.order_service import normalize_order_code, normalize_phone
from app.services.warranty_tools import (
    activate_warranty,
    search_order,
    search_warranty_policy,
)


class OpenAIProvider(AIService):
    provider_name = "openai"

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("Thiếu OPENAI_API_KEY trong file .env")

        self.client = OpenAI(api_key=api_key)
        self.model = os.getenv(
            "OPENAI_MODEL",
            "gpt-5.6-sol",
        ).strip()
        if not self.model:
            raise RuntimeError("OPENAI_MODEL không được để trống")

        self.system_prompt = self._read(WARRANTY_PROMPT_PATH)
        self.confirmation_prompt = self._read(
            CONFIRMATION_PROMPT_PATH
        )
        self.product_image_request_prompt = self._read(
            PRODUCT_IMAGE_REQUEST_PROMPT_PATH
        )
        self.image_intent_prompt = self._read(
            IMAGE_INTENT_PROMPT_PATH
        )
        self.image_order_prompt = self._read(
            IMAGE_ORDER_EXTRACTION_PROMPT_PATH
        )
        self.product_recognition_prompt = self._read(
            PRODUCT_RECOGNITION_PROMPT_PATH
        )
        self.product_reply_prompt = self._read(
            PRODUCT_REPLY_PROMPT_PATH
        )
        self.catalog = ProductCatalogService()

        self.tool_functions: dict[
            str, Callable[..., dict[str, Any]]
        ] = {
            "search_warranty_policy": search_warranty_policy,
            "search_order": search_order,
            "activate_warranty": activate_warranty,
            "search_products": search_products,
            "get_product_info": get_product_info,
        }
        self.tools = self._build_tools()

    def chat(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        inputs = self._history(history)
        inputs.append({
            "role": "user",
            "content": (
                f"THÔNG TIN HỆ THỐNG:\ncustomer_id: {customer_id}"
                f"\n\nTIN NHẮN KHÁCH HÀNG:\n{message}"
            ),
        })
        response = self._run_with_tools(inputs)
        reply = response.output_text or (
            "Dạ hiện tại em chưa thể xử lý yêu cầu. "
            "Anh/chị vui lòng thử lại sau ít phút ạ."
        )
        return {"success": True, "reply": reply.strip()}

    def compose_reply(
        self,
        event: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        inputs = self._history(history)
        inputs.append({
            "role": "user",
            "content": (
                "KẾT QUẢ NGHIỆP VỤ ĐÃ ĐƯỢC HỆ THỐNG XÁC MINH:\n"
                f"{event}\n\nViết câu trả lời ngắn gọn bằng tiếng Việt. "
                "Chỉ dùng dữ liệu trên, không tự thêm thông tin."
            ),
        })
        response = self.client.responses.create(
            model=self.model,
            instructions=self.system_prompt,
            input=inputs,
        )
        if not response.output_text:
            raise RuntimeError("OpenAI không tạo được câu trả lời")
        return response.output_text.strip()

    def classify_confirmation_intent(self, message: str) -> str:
        parsed = self._parse_text(
            message,
            self.confirmation_prompt,
            ConfirmationIntent,
        )
        return parsed.intent if parsed else "unknown"

    def classify_product_image_request(self, message: str) -> bool:
        parsed = self._parse_text(
            message,
            self.product_image_request_prompt,
            ProductImageRequestIntent,
        )
        return bool(parsed and parsed.intent == "request_images")

    def classify_image_intent(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> dict[str, str]:
        parsed = self._parse_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            instructions=self.image_intent_prompt,
            text=f"Caption: {caption or '(không có)'}",
            schema=ImageIntent,
        )
        return {
            "intent": parsed.intent if parsed else "unknown",
            "product_type": (
                parsed.product_type if parsed else "unknown"
            ),
        }

    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        extracted = self._parse_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            instructions=self.image_order_prompt,
            text="Đọc thông tin đơn hàng trong ảnh.",
            schema=ImageOrderInfo,
        )
        if extracted is None:
            raise RuntimeError("OpenAI không trả kết quả đọc ảnh")

        masked_phone = re.sub(
            r"[^0-9*xX]",
            "",
            extracted.masked_phone or "",
        ) or None
        return {
            "phone": normalize_phone(extracted.phone),
            "masked_phone": masked_phone,
            "order_code": normalize_order_code(extracted.order_code),
            "phone_confident": extracted.phone_confident,
            "masked_phone_confident": (
                extracted.masked_phone_confident
            ),
            "order_code_confident": extracted.order_code_confident,
        }

    def handle_product_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str = "unknown",
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": self.product_recognition_prompt,
            },
            {
                "type": "input_text",
                "text": "CUSTOMER IMAGE:",
            },
            self._image_item(image_bytes, mime_type),
        ]
        valid_codes: set[str] = set()

        reference_limit = (
            5 if product_type != "unknown"
            else 8
        )
        for reference in self.catalog.reference_products(
            product_type=(
                None
                if product_type == "unknown"
                else product_type
            ),
            limit=reference_limit,
        ):
            try:
                response = requests.get(
                    reference["image_url"],
                    timeout=30,
                )
                response.raise_for_status()
            except requests.RequestException:
                continue

            code = reference["product_code"]
            valid_codes.add(code)
            reference_mime = response.headers.get(
                "Content-Type", "image/jpeg"
            ).split(";")[0]
            content.extend([
                {
                    "type": "input_text",
                    "text": (
                        f"REFERENCE product_code={code}; "
                        f"title={reference['title']}"
                    ),
                },
                self._image_item(response.content, reference_mime),
            ])

        response = self.client.responses.parse(
            model=self.model,
            input=[{"role": "user", "content": content}],
            text_format=ProductRecognitionResult,
        )
        recognition = response.output_parsed or ProductRecognitionResult()
        candidates = []
        for candidate in recognition.candidates:
            if (
                candidate.confidence < 0.70
                or candidate.product_code.upper() not in valid_codes
            ):
                continue
            product = self.catalog.public_info(
                candidate.product_code
            )
            if product:
                candidates.append({
                    "confidence": candidate.confidence,
                    "visual_reason": candidate.reason,
                    "product": product,
                })

        if not candidates:
            return {
                "reply": (
                    "Dạ em chưa nhận diện chính xác được sản phẩm trong "
                    "ảnh này. Anh/chị gửi giúp em mã sản phẩm hoặc ảnh "
                    "rõ hơn, chụp trọn sản phẩm ở góc khác nhé. 😊"
                ),
                "product_codes": [],
            }

        reply_response = self.client.responses.create(
            model=self.model,
            instructions=self.product_reply_prompt,
            input=json.dumps(
                {
                    "status": "candidates_found",
                    "candidates": candidates,
                },
                ensure_ascii=False,
            ),
        )
        codes = sorted({
            item["product"]["product_code"]
            for item in candidates
        })
        reply = reply_response.output_text
        if not reply:
            top = candidates[0]["product"]
            reply = (
                f"Dạ em nhận diện được mẫu {top['product_name']}, "
                f"mã {top['product_code']} ạ."
            )
        return {"reply": reply.strip(), "product_codes": codes}

    def _run_with_tools(self, inputs: list[Any]):
        for _ in range(5):
            response = self.client.responses.create(
                model=self.model,
                instructions=self.system_prompt,
                input=inputs,
                tools=self.tools,
            )
            inputs.extend(response.output)
            calls = [
                item for item in response.output
                if item.type == "function_call"
            ]
            if not calls:
                return response
            for call in calls:
                function = self.tool_functions.get(call.name)
                try:
                    arguments = json.loads(call.arguments)
                    result = (
                        function(**arguments)
                        if function
                        else {
                            "success": False,
                            "status": "unknown_tool",
                        }
                    )
                except Exception as error:
                    result = {
                        "success": False,
                        "status": "tool_error",
                        "message": str(error),
                    }
                inputs.append({
                    "type": "function_call_output",
                    "call_id": call.call_id,
                    "output": json.dumps(result, ensure_ascii=False),
                })
        raise RuntimeError("OpenAI vượt quá giới hạn 5 lượt gọi tool")

    def _parse_text(self, text: str, instructions: str, schema):
        try:
            response = self.client.responses.parse(
                model=self.model,
                instructions=instructions,
                input=text,
                text_format=schema,
            )
            return response.output_parsed
        except Exception:
            return None

    def _parse_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        instructions: str,
        text: str,
        schema,
    ):
        self._validate_image(image_bytes, mime_type)
        response = self.client.responses.parse(
            model=self.model,
            instructions=instructions,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": text},
                    self._image_item(image_bytes, mime_type),
                ],
            }],
            text_format=schema,
        )
        return response.output_parsed

    @staticmethod
    def _image_item(data: bytes, mime_type: str) -> dict[str, str]:
        encoded = base64.b64encode(data).decode("ascii")
        return {
            "type": "input_image",
            "image_url": f"data:{mime_type};base64,{encoded}",
            "detail": "high",
        }

    @staticmethod
    def _validate_image(data: bytes, mime_type: str) -> None:
        if not data:
            raise ValueError("Ảnh không có dữ liệu")
        if mime_type not in {
            "image/jpeg", "image/png", "image/webp"
        }:
            raise ValueError("Định dạng ảnh không được hỗ trợ")

    @staticmethod
    def _history(
        history: list[dict[str, Any]] | None,
    ) -> list[Any]:
        result = []
        for item in history or []:
            role = item.get("role")
            text = item.get("text", "")
            if role == "model":
                role = "assistant"
            if role in {"user", "assistant"} and text:
                result.append({"role": role, "content": text})
        return result

    @staticmethod
    def _read(path: Path) -> str:
        if not path.exists():
            raise RuntimeError(f"Không tìm thấy prompt: {path}")
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise RuntimeError(f"Prompt đang để trống: {path}")
        return text

    @staticmethod
    def _build_tools() -> list[dict[str, Any]]:
        def tool(
            name: str,
            description: str,
            properties: dict[str, Any],
            required: list[str],
        ) -> dict[str, Any]:
            return {
                "type": "function",
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "strict": True,
            }

        string = {"type": "string"}
        nullable_string = {"type": ["string", "null"]}
        return [
            tool(
                "search_warranty_policy",
                "Tra cứu chính sách bảo hành và đổi hàng.",
                {"question": string},
                ["question"],
            ),
            tool(
                "search_order",
                "Tìm đơn theo số điện thoại và mã đơn.",
                {"phone": string, "order_code": nullable_string},
                ["phone", "order_code"],
            ),
            tool(
                "activate_warranty",
                "Kích hoạt bảo hành cho đơn đã xác minh.",
                {
                    "order_code": string,
                    "phone": string,
                    "customer_id": string,
                },
                ["order_code", "phone", "customer_id"],
            ),
            tool(
                "search_products",
                "Tìm sản phẩm trong catalog theo nhu cầu khách.",
                {"query": string},
                ["query"],
            ),
            tool(
                "get_product_info",
                "Lấy thông tin chính thức theo mã sản phẩm.",
                {"product_code": string},
                ["product_code"],
            ),
        ]
