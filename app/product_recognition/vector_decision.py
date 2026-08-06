import math
from dataclasses import dataclass
from typing import Any, Literal

from app.config import (
    VECTOR_AUTO_ACCEPT_SIMILARITY,
    VECTOR_MAX_CANDIDATES,
    VECTOR_MIN_MARGIN,
    VECTOR_MIN_SIMILARITY,
)


VectorDecisionStatus = Literal[
    "auto_accept",
    "needs_verification",
    "no_match",
]


@dataclass(frozen=True)
class VectorDecision:
    status: VectorDecisionStatus
    top_similarity: float
    second_similarity: float
    margin: float
    candidates: list[dict[str, Any]]

    @property
    def best_candidate(self) -> dict[str, Any] | None:
        return self.candidates[0] if self.candidates else None


def normalize_color(value: Any) -> str:
    """Chuẩn hóa màu để gom các ảnh cùng biến thể."""

    return str(value or "").strip().casefold()


def _safe_similarity(value: Any) -> float:
    """Chuyển similarity sang float và loại giá trị không hợp lệ."""

    try:
        similarity = float(value)
    except (TypeError, ValueError):
        return 0.0

    return similarity if math.isfinite(similarity) else 0.0


def group_vector_results(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Chỉ giữ ảnh có similarity cao nhất của từng
    cặp product_code + color.
    """

    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        product_code = str(
            row.get("product_code") or ""
        ).strip().upper()
        if not product_code:
            continue

        similarity = _safe_similarity(row.get("similarity"))
        color_key = normalize_color(row.get("color"))
        key = (product_code, color_key)
        candidate = {
            **row,
            "product_code": product_code,
            "similarity": similarity,
        }

        current = grouped.get(key)
        if (
            current is None
            or similarity > _safe_similarity(current.get("similarity"))
        ):
            grouped[key] = candidate

    return sorted(
        grouped.values(),
        key=lambda item: _safe_similarity(item.get("similarity")),
        reverse=True,
    )


def best_distinct_products(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank a product from its best image plus supporting image matches."""

    products: dict[str, list[dict[str, Any]]] = {}

    for candidate in candidates:
        product_code = str(
            candidate.get("product_code") or ""
        ).strip().upper()
        if not product_code:
            continue

        products.setdefault(product_code, []).append(candidate)

    ranked: list[dict[str, Any]] = []
    for product_code, product_rows in products.items():
        product_rows.sort(
            key=lambda item: _safe_similarity(item.get("similarity")),
            reverse=True,
        )
        evidence = product_rows[:3]
        best = _safe_similarity(evidence[0].get("similarity"))
        mean = sum(
            _safe_similarity(item.get("similarity")) for item in evidence
        ) / len(evidence)
        aggregate = (0.75 * best) + (0.25 * mean)
        ranked.append({
            **evidence[0],
            "product_code": product_code,
            "similarity": aggregate,
            "best_similarity": best,
            "support_count": len(evidence),
        })

    return sorted(
        ranked,
        key=lambda item: _safe_similarity(item.get("similarity")),
        reverse=True,
    )


def decide_vector_match(
    rows: list[dict[str, Any]],
) -> VectorDecision:
    """Tạo quyết định từ danh sách kết quả pgvector."""

    # Keep evidence from multiple angles instead of retaining one image/color.
    distinct_products = best_distinct_products(rows)
    max_candidates = max(1, VECTOR_MAX_CANDIDATES)
    candidates = distinct_products[:max_candidates]

    if not candidates:
        return VectorDecision(
            status="no_match",
            top_similarity=0.0,
            second_similarity=0.0,
            margin=0.0,
            candidates=[],
        )

    top_similarity = _safe_similarity(
        candidates[0].get("similarity")
    )
    second_similarity = (
        _safe_similarity(candidates[1].get("similarity"))
        if len(candidates) > 1
        else 0.0
    )
    margin = max(0.0, top_similarity - second_similarity)

    if top_similarity < VECTOR_MIN_SIMILARITY:
        status: VectorDecisionStatus = "no_match"
    elif (
        top_similarity >= VECTOR_AUTO_ACCEPT_SIMILARITY
        and margin >= VECTOR_MIN_MARGIN
    ):
        status = "auto_accept"
    else:
        status = "needs_verification"

    return VectorDecision(
        status=status,
        top_similarity=top_similarity,
        second_similarity=second_similarity,
        margin=margin,
        candidates=candidates,
    )
