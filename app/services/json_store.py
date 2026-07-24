import json
import threading
from pathlib import Path
from typing import Any

_lock = threading.Lock()


class JsonStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def read(self, default: Any) -> Any:
        with _lock:
            if not self.path.exists():
                return default
            with self.path.open("r", encoding="utf-8") as file:
                content = file.read().strip()
                if not content:
                    return default
                return json.loads(content)

    def write(self, data: Any) -> None:
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with _lock:
            with temp_path.open("w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
            temp_path.replace(self.path)
