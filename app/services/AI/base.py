from abc import ABC, abstractmethod
from typing import Any


class AIService(ABC):
    provider_name: str
    model: str

    @abstractmethod
    def chat(
        self,
        message: str,
        customer_id: str,
        history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Trả lời và gọi các tool nghiệp vụ khi cần."""

    @abstractmethod
    def compose_reply(
        self,
        event: str,
        history: list[dict[str, Any]] | None = None,
    ) -> str:
        """Viết câu trả lời từ kết quả nghiệp vụ đã xác minh."""

    @abstractmethod
    def classify_confirmation_intent(self, message: str) -> str:
        """Trả về confirm, cancel hoặc unknown."""

    @abstractmethod
    def classify_product_image_request(self, message: str) -> bool:
        """Xác định khách có yêu cầu gửi ảnh sản phẩm hay không."""

    @abstractmethod
    def classify_image_intent(
        self,
        image_bytes: bytes,
        mime_type: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        """Phân loại ảnh bảo hành, ảnh sản phẩm hoặc không rõ."""

    @abstractmethod
    def extract_order_from_image(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        """Đọc số điện thoại và mã đơn từ ảnh."""

    @abstractmethod
    def handle_product_image(
        self,
        image_bytes: bytes,
        mime_type: str,
        product_type: str = "unknown",
    ) -> dict[str, Any]:
        """Nhận diện ảnh và soạn câu trả lời sản phẩm."""
