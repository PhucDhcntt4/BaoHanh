import math
import os
from typing import Any

from app.config import (
    RAG_EMBEDDING_DIMENSION,
    RAG_EMBEDDING_MODEL,
)


class TextEmbeddingService:
    def __init__(self, client: Any | None = None) -> None:
        from google import genai
        from google.genai import types  # type: ignore

        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if client is None and not api_key:
            raise RuntimeError(
                "Thiếu GEMINI_API_KEY để tạo text embedding"
            )

        self.client = client or genai.Client(api_key=api_key)
        self._types = types
        self.model = RAG_EMBEDDING_MODEL
        self.dimension = RAG_EMBEDDING_DIMENSION

        if self.dimension != 768:
            raise RuntimeError(
                "RAG_EMBEDDING_DIMENSION phải bằng 768 để khớp "
                "db_postgre/003_customer_care_rag.sql"
            )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        if not text.strip():
            raise ValueError("Câu hỏi không được để trống")
        return self._embed([text], task_type="QUESTION_ANSWERING")[0]

    def _embed(
        self,
        texts: list[str],
        task_type: str,
    ) -> list[list[float]]:
        cleaned = [text.strip() for text in texts if text.strip()]
        if len(cleaned) != len(texts):
            raise ValueError("Nội dung embedding không được để trống")
        if not cleaned:
            return []

        response = self.client.models.embed_content(
            model=self.model,
            contents=cleaned,
            config=self._types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=self.dimension,
            ),
        )
        embeddings = response.embeddings or []
        if len(embeddings) != len(cleaned):
            raise RuntimeError("Gemini trả về sai số lượng embedding")

        return [
            self._normalize(list(item.values or []))
            for item in embeddings
        ]

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