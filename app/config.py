from pathlib import Path
import os

from dotenv import load_dotenv # type: ignore


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
PROMPT_DIR = PROJECT_ROOT / "prompts"

ORDERS_PATH = DATA_DIR / "orders.json"
WARRANTIES_PATH = DATA_DIR / "warranties.json"
WARRANTY_PROMPT_PATH = PROMPT_DIR / "warranty_agent.txt"
CONFIRMATION_PROMPT_PATH = PROMPT_DIR / "confirmation_intent.txt"
PRODUCT_IMAGE_REQUEST_PROMPT_PATH = (
    PROMPT_DIR / "product_image_request.txt"
)
WARRANTY_POLICY_PATH = PROMPT_DIR / "warranty_policy.txt"
IMAGE_ORDER_EXTRACTION_PROMPT_PATH = (
    PROMPT_DIR / "image_order_extraction.txt"
)
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
