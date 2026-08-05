from pathlib import Path
import os

from dotenv import load_dotenv # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
PROMPT_DIR = PROJECT_ROOT / "prompts"

CUSTOMER_AGENT_PROMPT_PATH = PROMPT_DIR / "customer_agent.txt"
PRODUCT_IMAGE_REQUEST_PROMPT_PATH = (
    PROMPT_DIR / "product_image_request.txt"
)
WARRANTY_POLICY_PATH = PROMPT_DIR / "warranty_policy.txt"
PRODUCTS_PATH = PROJECT_ROOT / "products.json"
IMAGE_INTENT_PROMPT_PATH = PROMPT_DIR / "image_intent.txt"
PRODUCT_RECOGNITION_PROMPT_PATH = (
    PROMPT_DIR / "product_recognition.txt"
)
PRODUCT_REPLY_PROMPT_PATH = PROMPT_DIR / "product_reply.txt"

PRODUCT_IMAGE_DIR = DATA_DIR / "product_images"

PRODUCT_IMAGE_MANIFEST_PATH = (
    PRODUCT_IMAGE_DIR / "manifest.json"
)

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
PRODUCT_CATALOG_SOURCE = os.getenv(
    "PRODUCT_CATALOG_SOURCE", "json"
).strip().casefold()

if PRODUCT_CATALOG_SOURCE not in {"json", "database"}:
    raise RuntimeError(
        "PRODUCT_CATALOG_SOURCE chỉ nhận 'json' hoặc 'database'"
    )

VECTOR_MIN_SIMILARITY = float(
    os.getenv(
        "VECTOR_MIN_SIMILARITY",
        "0.80",
    )
)

VECTOR_AUTO_ACCEPT_SIMILARITY = float(
    os.getenv(
        "VECTOR_AUTO_ACCEPT_SIMILARITY",
        "0.96",
    )
)

VECTOR_MIN_MARGIN = float(
    os.getenv(
        "VECTOR_MIN_MARGIN",
        "0.08",
    )
)

VECTOR_MAX_CANDIDATES = int(
    os.getenv(
        "VECTOR_MAX_CANDIDATES",
        "3",
    )
)

PRODUCT_VECTOR_SEARCH_ENABLED = os.getenv(
    "PRODUCT_VECTOR_SEARCH_ENABLED", "false"
).strip().casefold() in {"1", "true", "yes", "on"}

VECTOR_SEARCH_LIMIT = int(
    os.getenv("VECTOR_SEARCH_LIMIT", "20")
)

IMAGE_EMBEDDING_MODEL = os.getenv(
    "IMAGE_EMBEDDING_MODEL", "ViT-B-32"
).strip()

IMAGE_EMBEDDING_PRETRAINED = os.getenv(
    "IMAGE_EMBEDDING_PRETRAINED", "laion2b_s34b_b79k"
).strip()
