import logging
from typing import Any


logger = logging.getLogger("uvicorn.error")


class KnowledgeSearchService:
    def __init__(
        self,
        embedding_service: Any | None = None,
        repository: Any | None = None,
        *,
        top_k: int | None = None,
        min_similarity: float | None = None,
        max_context_chars: int | None = None,
    ) -> None:
        if (
            top_k is None
            or min_similarity is None
            or max_context_chars is None
        ):
            from app.config import (
                RAG_MAX_CONTEXT_CHARS,
                RAG_MIN_SIMILARITY,
                RAG_TOP_K,
            )

            top_k = RAG_TOP_K if top_k is None else top_k
            min_similarity = (
                RAG_MIN_SIMILARITY
                if min_similarity is None
                else min_similarity
            )
            max_context_chars = (
                RAG_MAX_CONTEXT_CHARS
                if max_context_chars is None
                else max_context_chars
            )

        if embedding_service is None:
            from app.knowledge.embedding_service import (
                create_text_embedding_service,
            )

            embedding_service = create_text_embedding_service()
        if repository is None:
            from app.database.knowledge_repository import KnowledgeRepository

            repository = KnowledgeRepository()

        self.embedding_service = embedding_service
        self.repository = repository
        self.top_k = top_k
        self.min_similarity = min_similarity
        self.max_context_chars = max_context_chars

    def search(
        self,
        question: str,
        *,
        categories: list[str] | None = None,
    ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            return {
                "success": False,
                "status": "invalid_question",
                "content": "",
                "sources": [],
            }

        embedding = self.embedding_service.embed_query(question)
        rows = self.repository.search(
            embedding=embedding,
            embedding_provider=self.embedding_service.provider_name,
            embedding_model=self.embedding_service.model,
            embedding_dimension=self.embedding_service.dimension,
            categories=categories,
            min_similarity=self.min_similarity,
            limit=self.top_k,
        )
        if not rows:
            logger.info(
                "RAG SEARCH status=not_found provider=%s model=%s "
                "categories=%s",
                self.embedding_service.provider_name,
                self.embedding_service.model,
                categories or ["all"],
            )
            return {
                "success": False,
                "status": "knowledge_not_found",
                "content": "",
                "sources": [],
            }

        context_parts: list[str] = []
        sources: list[dict[str, Any]] = []
        current_length = 0
        for row in rows:
            label = row["title"]
            if row.get("heading"):
                label = f"{label} > {row['heading']}"
            part = f"[Nguồn: {label}]\n{row['content']}"
            if (
                context_parts
                and current_length + len(part) > self.max_context_chars
            ):
                break
            context_parts.append(part)
            current_length += len(part)
            sources.append({
                "source_key": row["source_key"],
                "title": row["title"],
                "category": row["category"],
                "heading": row.get("heading"),
                "chunk_index": row["chunk_index"],
                "similarity": round(float(row["similarity"]), 4),
            })

        logger.info(
            "RAG SEARCH status=found provider=%s model=%s "
            "categories=%s top=%.4f values=%s",
            self.embedding_service.provider_name,
            self.embedding_service.model,
            categories or ["all"],
            float(rows[0]["similarity"]),
            [
                {
                    "source": item["source_key"],
                    "chunk": item["chunk_index"],
                    "similarity": round(float(item["similarity"]), 4),
                }
                for item in rows
            ],
        )
        return {
            "success": True,
            "status": "knowledge_found",
            "content": "\n\n".join(context_parts),
            "sources": sources,
        }
