import logging
from typing import Any

from app.services.policy_service import PolicyService


policy_service = PolicyService()
logger = logging.getLogger(__name__)


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
