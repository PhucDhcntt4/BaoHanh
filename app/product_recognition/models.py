
from typing import Literal

from pydantic import BaseModel, Field


class ImageIntent(BaseModel):
    intent: Literal[
        "product_lookup",
        "unknown",
    ]
    product_type: str | None = None
    bounding_box: list[int] | None = Field(
        default=None,
        min_length=4,
        max_length=4,
    )

class ProductCandidate(BaseModel):
    product_code: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class ProductRecognitionResult(BaseModel):
    candidates: list[ProductCandidate] = Field(
        default_factory=list
    )


class ProductMatchVerification(BaseModel):
    exact_match: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    matched_reference: int | None = None
    mismatches: list[str] = Field(default_factory=list)


class VectorCandidateVerification(BaseModel):
    exact_match: bool = False
    product_code: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    reason: str = ""


MIN_PRODUCT_MATCH_CONFIDENCE = 0.85
MIN_PRODUCT_MATCH_MARGIN = 0.08


def select_confident_product_candidate(
    candidates: list[ProductCandidate],
) -> ProductCandidate | None:
    """Only accept one clear visual match."""
    ranked = sorted(
        candidates,
        key=lambda item: item.confidence,
        reverse=True,
    )
    if not ranked or ranked[0].confidence < MIN_PRODUCT_MATCH_CONFIDENCE:
        return None
    if (
        len(ranked) > 1
        and ranked[0].confidence - ranked[1].confidence
        < MIN_PRODUCT_MATCH_MARGIN
    ):
        return None
    return ranked[0]
