import os

from app.services.AI.base import AIService


def create_ai_service() -> AIService:
    provider = os.getenv(
        "AI_PROVIDER",
        "gemini",
    ).strip().lower()

    if provider == "gemini":
        from app.services.gemini_service import GeminiService

        return GeminiService()

    if provider == "openai":
        from app.services.AI.openai_provider import OpenAIProvider

        return OpenAIProvider()

    raise RuntimeError(
        f"AI_PROVIDER không được hỗ trợ: {provider}. "
        "Giá trị hợp lệ: gemini hoặc openai."
    )
