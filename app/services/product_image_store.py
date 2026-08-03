import json
from pathlib import Path
from typing import Any

from app.config import (
    PRODUCT_IMAGE_DIR,
    PRODUCT_IMAGE_MANIFEST_PATH,
)


class ProductImageStore:
    def __init__(
        self,
        image_dir: str | Path = PRODUCT_IMAGE_DIR,
        manifest_path: str | Path = PRODUCT_IMAGE_MANIFEST_PATH,
    ) -> None:
        self.image_dir = Path(image_dir)
        self.manifest_path = Path(manifest_path)
        self._manifest = self._load_manifest()

    def _load_manifest(
        self,
    ) -> dict[str, dict[str, Any]]:
        if not self.manifest_path.exists():
            return {}

        try:
            data = json.loads(
                self.manifest_path.read_text(encoding="utf-8")
            )

        except (OSError, json.JSONDecodeError):
            return {}

        if not isinstance(data, dict):
            return {}

        return {
            str(url): item
            for url, item in data.items()
            if isinstance(item, dict)
        }

    def reload(self) -> None:
        self._manifest = self._load_manifest()

    def get(
        self,
        source_url: str,
    ) -> tuple[bytes, str] | None:
        item = self._manifest.get(source_url)

        if not item:
            return None

        relative_path = item.get("local_path")
        mime_type = str(
            item.get("mime_type") or "image/jpeg"
        ).strip().lower()

        if mime_type not in {
            "image/jpeg",
            "image/png",
            "image/webp",
        }:
            return None
        if not relative_path:
            return None

        image_path = (
            self.image_dir / str(relative_path)
        ).resolve()

        image_root = self.image_dir.resolve()

        try:
            image_path.relative_to(image_root)
        except ValueError:
            return None

        if not image_path.is_file():
            return None

        try:
            image_bytes = image_path.read_bytes()
        except OSError:
            return None

        if not image_bytes:
            return None

        return image_bytes, str(mime_type)
