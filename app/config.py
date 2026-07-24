from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PROMPT_DIR = PROJECT_ROOT / "prompts"

ORDERS_PATH = DATA_DIR / "orders.json"
WARRANTIES_PATH = DATA_DIR / "warranties.json"
WARRANTY_PROMPT_PATH = PROMPT_DIR / "warranty_agent.txt"
