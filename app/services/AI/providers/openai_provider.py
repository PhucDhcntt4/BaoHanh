import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

import requests
from openai import OpenAI  # type: ignore

from app.config import (
    CUSTOMER_AGENT_PROMPT_PATH,
    IMAGE_INTENT_PROMPT_PATH,
    PRODUCT_IMAGE_REQUEST_PROMPT_PATH,
    PRODUCT_RECOGNITION_PROMPT_PATH,
    PRODUCT_REPLY_PROMPT_PATH,
)
from app.models import (
    ProductImageRequestIntent,
)
from app.product_recognition.catalog_service import ProductCatalogService
from app.product_recognition.models import (
    ImageIntent,
    ProductMatchVerification,
    ProductRecognitionResult,
)
from app.product_recognition.product_tools import (
    get_product_info,
    search_products,
)
from app.services.AI.base import AIService
from app.services.product_image_store import ProductImageStore
from app.services.policy_tools import (
    search_customer_care_knowledge,
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

        self.system_prompt = self._read(CUSTOMER_AGENT_PROMPT_PATH)
        self.product_image_request_prompt = self._read(
            PRODUCT_IMAGE_REQUEST_PROMPT_PATH
        )
        self.image_intent_prompt = self._read(
            IMAGE_INTENT_PROMPT_PATH
        )
        self.product_recognition_prompt = self._read(
            PRODUCT_RECOGNITION_PROMPT_PATH
        )
        self.product_reply_prompt = self._read(
            PRODUCT_REPLY_PROMPT_PATH
        )
        self.catalog = ProductCatalogService()
        self.image_store = ProductImageStore()

        self.tool_functions: dict[
            str, Callable[..., dict[str, Any]]
        ] = {
            "search_warranty_policy": search_warranty_policy,
            "search_customer_care_knowledge": (
                search_customer_care_knowledge
            ),
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
    ) -> dict[str, Any]:
        product_types = self.catalog.product_types()
        dynamic_instructions = (
            self.image_intent_prompt
            + "\n\nCác productType hợp lệ trong catalog:\n"
            + "\n".join(
                f"- {product_type}"
                for product_type in product_types
            )
            + "\n\nChỉ chọn đúng nguyên văn một giá trị trong "
            "danh sách. Nếu không chắc, trả product_type=null."
        )
        parsed = self._parse_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            instructions=dynamic_instructions,
            text=f"Caption: {caption or '(không có)'}",
            schema=ImageIntent,
        )
        intent = parsed.intent if parsed else "unknown"
        resolved_type = (
            self.catalog.resolve_product_type(
                parsed.product_type
            )
            if parsed and intent == "product_lookup"
            else None
        )
        return {
            "intent": intent,
            "product_type": resolved_type or "unknown",
            "bounding_box": (
                parsed.bounding_box
                if parsed and intent == "product_lookup"
                else None
            ),
        }

    def _verify_product_match(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_code: str,
    ) -> ProductMatchVerification:
        product = self.catalog.public_info(product_code)
        if not product:
            return ProductMatchVerification()
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Xác minh CUSTOMER có đúng cùng một mẫu sản phẩm với "
                    f"REFERENCE mã {product_code}. Không chỉ kiểm tra cùng "
                    "loại hoặc cùng màu. So sánh cấu trúc thân, quai, mũi, "
                    "đế, gót, đường may, logo và chi tiết trang trí. Bỏ qua "
                    "nền, chữ quảng cáo và giao diện website. So sánh CUSTOMER "
                    "riêng với từng REFERENCE. Nếu khớp rõ ít nhất một ảnh, "
                    "exact_match=true và ghi số ảnh vào matched_reference. "
                    "Không phủ nhận ảnh đã khớp vì reference khác có góc, màu "
                    "hoặc phụ kiện tháo rời khác. Chỉ exact_match=true và "
                    "confidence>=0.90 khi gần như chắc chắn cùng mẫu."
                ),
            },
            {"type": "input_text", "text": "CUSTOMER IMAGE:"},
            self._image_item(image_bytes, mime_type),
        ]
        loaded = 0
        for reference_index, image_url in enumerate(
            product.get("image_urls") or [],
            start=1,
        ):
            try:
                local_image = self.image_store.get(str(image_url))
                if local_image:
                    reference_bytes, reference_mime = local_image
                else:
                    response = requests.get(str(image_url), timeout=30)
                    response.raise_for_status()
                    reference_bytes = response.content
                    reference_mime = response.headers.get(
                        "Content-Type", "image/jpeg"
                    ).split(";")[0]
            except requests.RequestException:
                continue
            loaded += 1
            content.extend([
                {
                    "type": "input_text",
                    "text": (
                        f"REFERENCE {reference_index} "
                        f"product_code={product_code}"
                    ),
                },
                self._image_item(reference_bytes, reference_mime),
            ])
        if not loaded:
            return ProductMatchVerification()
        response = self.client.responses.parse(
            model=self.model,
            input=[{"role": "user", "content": content}],
            text_format=ProductMatchVerification,
        )
        return response.output_parsed or ProductMatchVerification()

    def handle_product_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str = "unknown",
        original_image_bytes: bytes | None = None,
        original_mime_type: str | None = None,
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
                    local_image = self.image_store.get(image_url)
                    if local_image:
                        reference_bytes, reference_mime = local_image
                    else:
                        response = requests.get(image_url, timeout=30)
                        response.raise_for_status()
                        reference_bytes = response.content
                        reference_mime = response.headers.get(
                            "Content-Type", "image/jpeg"
                        ).split(";")[0]
                except requests.RequestException:
                    continue
                loaded_images += 1
                content.extend([
                    {
                        "type": "input_text",
                        "text": (
                            f"REFERENCE product_code={code}; "
                            f"title={reference['title']}; "
                            f"view={image_index}"
                        ),
                    },
                    self._image_item(reference_bytes, reference_mime),
                ])
            if loaded_images:
                valid_codes.add(code)

        response = self.client.responses.parse(
            model=self.model,
            input=[{"role": "user", "content": content}],
            text_format=ProductRecognitionResult,
        )
        recognition = response.output_parsed or ProductRecognitionResult()
        valid_candidates = [
            candidate
            for candidate in recognition.candidates
            if candidate.product_code.upper() in valid_codes
        ]
        verification_candidates = sorted(
            (
                candidate
                for candidate in valid_candidates
                if candidate.confidence >= 0.70
            ),
            key=lambda item: item.confidence,
            reverse=True,
        )[:3]
        if not verification_candidates and product_type != "unknown":
            return self.handle_product_image(
                image_bytes=image_bytes,
                mime_type=mime_type,
                product_type="unknown",
            )
        candidates = []
        for candidate in verification_candidates:
            verification = self._verify_product_match(
                image_bytes=image_bytes,
                mime_type=mime_type,
                product_code=candidate.product_code,
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
                candidates.append({
                    "confidence": candidate.confidence,
                    "visual_reason": candidate.reason,
                    "product": product,
                })
                break

        if not candidates:
            return {
                "reply": (
                    "Dạ, em chưa tìm thấy sản phẩm khớp với hình ảnh này "
                    "trong hệ thống. Anh/chị có thể gửi mã sản phẩm hoặc "
                    "một ảnh rõ hơn để em kiểm tra lại nhé. 😊"
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
        return [
            tool(
                "search_warranty_policy",
                "Tra cứu chính sách bảo hành và đổi hàng.",
                {"question": string},
                ["question"],
            ),
            tool(
                "search_customer_care_knowledge",
                "Tra cứu hướng dẫn chăm sóc khách hàng chính thức.",
                {"question": string},
                ["question"],
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
