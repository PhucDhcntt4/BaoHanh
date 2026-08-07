from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import RAG_ENABLED, WARRANTY_POLICY_PATH

if TYPE_CHECKING:
    from app.knowledge.service import KnowledgeSearchService


class PolicyService:
    def __init__(
        self,
        path: str | Path = WARRANTY_POLICY_PATH,
        rag_service: "KnowledgeSearchService | None" = None,
    ) -> None:
        self.path = Path(path)
        self._rag_service = rag_service

    def search(self, question: str) -> dict[str, Any]:
        if not question.strip():
            return {
                "success": False,
                "status": "invalid_question",
                "content": "",
            }

        if RAG_ENABLED or self._rag_service is not None:
            from app.knowledge.service import KnowledgeSearchService

            service = self._rag_service or KnowledgeSearchService()
            self._rag_service = service
            result = service.search(
                question,
                categories=[
                    "warranty",
                    "returns",
                    "exchange",
                    "policy",
                ],
            )
            if result["status"] == "knowledge_not_found":
                result["status"] = "policy_not_found"
            return result

        if not self.path.exists():
            return {
                "success": False,
                "status": "policy_not_found",
                "content": "",
            }

        content = self.path.read_text(
            encoding="utf-8"
        ).strip()

        if not content:
            return {
                "success": False,
                "status": "policy_empty",
                "content": "",
            }

        return {
            "success": True,
            "status": "policy_found_legacy",
            "source": self.path.name,
            "content": content,
        }
