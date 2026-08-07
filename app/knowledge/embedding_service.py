import math
import os
from abc import ABC, abstractmethod
from typing import Any

from app.config import (
    RAG_EMBEDDING_DIMENSION,
    RAG_EMBEDDING_MODEL,
    RAG_EMBEDDING_PROVIDER,
)


class TextEmbeddingService(ABC):
    provider_name: str
    model: str
    dimension: int

    def __init__(self, model: str, dimension: int) -> None:
        self.model = model
        self.dimension = dimension
        if self.dimension != 768:
            raise RuntimeError(
                "RAG_EMBEDDING_DIMENSION phải bằng 768 để khớp "
                "db_postgre/003_customer_care_rag.sql"
            )

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Tạo embedding cho tài liệu dùng để lập chỉ mục."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Tạo embedding cho câu hỏi dùng để tìm kiếm."""

    def _clean(self, texts: list[str]) -> list[str]:
        cleaned = [text.strip() for text in texts]
        if any(not text for text in cleaned):
            raise ValueError("Nội dung embedding không được để trống")
        return cleaned

    def _normalize(self, values: list[float]) -> list[float]:
        if len(values) != self.dimension:
            raise RuntimeError(
                "Embedding trả về không đúng số chiều: "
                f"{len(values)} != {self.dimension}"
            )
        magnitude = math.sqrt(sum(value * value for value in values))
        if magnitude == 0:
            raise RuntimeError("Embedding có độ dài bằng 0")
        return [value / magnitude for value in values]


class GeminiTextEmbeddingService(TextEmbeddingService):
    provider_name = "gemini"

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = "gemini-embedding-001",
        dimension: int = 768,
    ) -> None:
        from google import genai
        from google.genai import types  # type: ignore

        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if client is None and not api_key:
            raise RuntimeError(
                "Thiếu GEMINI_API_KEY để tạo Gemini embedding"
            )

        super().__init__(model=model, dimension=dimension)
        self.client = client or genai.Client(api_key=api_key)
        self._types = types

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], task_type="QUESTION_ANSWERING")[0]

    def _embed(
        self,
        texts: list[str],
        task_type: str,
    ) -> list[list[float]]:
        cleaned = self._clean(texts)
        if not cleaned:
            return []

        if self.model == "gemini-embedding-2":
            prepared = [
                (
                    f"title: none | text: {text}"
                    if task_type == "RETRIEVAL_DOCUMENT"
                    else f"task: question answering | query: {text}"
                )
                for text in cleaned
            ]
            contents = [
                self._types.Content(
                    parts=[self._types.Part.from_text(text=text)]
                )
                for text in prepared
            ]
            config = self._types.EmbedContentConfig(
                output_dimensionality=self.dimension,
            )
        else:
            contents = cleaned
            config = self._types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimension,
            )

        response = self.client.models.embed_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        embeddings = response.embeddings or []
        if len(embeddings) != len(cleaned):
            raise RuntimeError("Gemini trả về sai số lượng embedding")
        return [
            self._normalize(list(item.values or []))
            for item in embeddings
        ]


class OpenAITextEmbeddingService(TextEmbeddingService):
    provider_name = "openai"

    def __init__(
        self,
        client: Any | None = None,
        *,
        model: str = "text-embedding-3-small",
        dimension: int = 768,
    ) -> None:
        from openai import OpenAI  # type: ignore

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if client is None and not api_key:
            raise RuntimeError(
                "Thiếu OPENAI_API_KEY để tạo OpenAI embedding"
            )

        super().__init__(model=model, dimension=dimension)
        self.client = client or OpenAI(api_key=api_key)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def _embed(self, texts: list[str]) -> list[list[float]]:
        cleaned = self._clean(texts)
        if not cleaned:
            return []

        response = self.client.embeddings.create(
            model=self.model,
            input=cleaned,
            dimensions=self.dimension,
            encoding_format="float",
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        if len(ordered) != len(cleaned):
            raise RuntimeError("OpenAI trả về sai số lượng embedding")
        return [
            self._normalize(list(item.embedding))
            for item in ordered
        ]


def resolved_embedding_provider() -> str:
    provider = RAG_EMBEDDING_PROVIDER
    if provider == "auto":
        provider = os.getenv("AI_PROVIDER", "gemini").strip().casefold()
    if provider not in {"gemini", "openai"}:
        raise RuntimeError(
            "RAG_EMBEDDING_PROVIDER chỉ hỗ trợ "
            "auto, gemini hoặc openai"
        )
    return provider


def create_text_embedding_service() -> TextEmbeddingService:
    provider = resolved_embedding_provider()
    model = RAG_EMBEDDING_MODEL

    if provider == "gemini" and model.startswith("text-embedding-"):
        raise RuntimeError(
            "RAG_EMBEDDING_MODEL đang là model OpenAI nhưng "
            "RAG_EMBEDDING_PROVIDER đã chọn Gemini"
        )
    if provider == "openai" and model.startswith("gemini-embedding-"):
        raise RuntimeError(
            "RAG_EMBEDDING_MODEL đang là model Gemini nhưng "
            "RAG_EMBEDDING_PROVIDER đã chọn OpenAI"
        )

    if provider == "gemini":
        return GeminiTextEmbeddingService(
            model=model or "gemini-embedding-001",
            dimension=RAG_EMBEDDING_DIMENSION,
        )
    return OpenAITextEmbeddingService(
        model=model or "text-embedding-3-small",
        dimension=RAG_EMBEDDING_DIMENSION,
    )
