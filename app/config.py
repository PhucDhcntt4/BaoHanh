from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
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
PRODUCTS_PATH = PROJECT_ROOT / "products.json"
IMAGE_INTENT_PROMPT_PATH = PROMPT_DIR / "image_intent.txt"
PRODUCT_RECOGNITION_PROMPT_PATH = (
    PROMPT_DIR / "product_recognition.txt"
)
PRODUCT_REPLY_PROMPT_PATH = PROMPT_DIR / "product_reply.txt"
