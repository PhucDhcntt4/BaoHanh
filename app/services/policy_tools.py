import logging
from typing import TYPE_CHECKING, Any

from app.config import RAG_ENABLED
from app.services.policy_service import PolicyService

if TYPE_CHECKING:
    from app.knowledge.service import KnowledgeSearchService


policy_service = PolicyService()
logger = logging.getLogger(__name__)
knowledge_service: "KnowledgeSearchService | None" = None


def search_warranty_policy(question: str) -> dict[str, Any]:
    """Tra cứu chính sách bảo hành và đổi trả chính thức."""

    try:
        return policy_service.search(question)
    except Exception:
        logger.exception("Không thể đọc chính sách bảo hành")
        return {
            "success": False,
            "status": "policy_error",
            "content": "",
        }


def search_customer_care_knowledge(question: str) -> dict[str, Any]:
    """Tra cứu hướng dẫn chăm sóc khách hàng chính thức."""
    global knowledge_service

    if not RAG_ENABLED:
        return {
            "success": False,
            "status": "rag_disabled",
            "content": "",
            "sources": [],
        }

    try:
        from app.knowledge.service import KnowledgeSearchService

        knowledge_service = knowledge_service or KnowledgeSearchService()
        return knowledge_service.search(
            question,
            categories=None
        )
    except Exception:
        logger.exception("Không thể tra cứu kiến thức chăm sóc khách hàng")
        return {
            "success": False,
            "status": "knowledge_error",
            "content": "",
            "sources": [],
        }
