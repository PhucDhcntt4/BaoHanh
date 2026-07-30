from typing import Literal

from pydantic import BaseModel, Field


class ImageIntent(BaseModel):
    intent: Literal[
        "warranty_document",
        "product_lookup",
        "unknown",
    ]


class ProductCandidate(BaseModel):
    product_code: str
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class ProductRecognitionResult(BaseModel):
    candidates: list[ProductCandidate] = Field(
        default_factory=list
    )
