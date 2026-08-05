import hashlib
import io
import os
import threading

import open_clip # type: ignore
import torch # type: ignore
from PIL import Image


class ImageEmbeddingService:
    def __init__(self) -> None:
        self.model_name = os.getenv(
            "IMAGE_EMBEDDING_MODEL",
            "ViT-B-32",
        )
        self.pretrained_name = os.getenv(
            "IMAGE_EMBEDDING_PRETRAINED",
            "laion2b_s34b_b79k",
        )

        self.device = (
            "cuda"
            if torch.cuda.is_available()
            else "cpu"
        )

        self.model, _, self.preprocess = (
            open_clip.create_model_and_transforms(
                self.model_name,
                pretrained=self.pretrained_name,
            )
        )

        self.model = self.model.to(self.device)
        self.model.eval()
        self._lock = threading.Lock()

    def embed_bytes(
        self,
        image_bytes: bytes,
    ) -> list[float]:
        if not image_bytes:
            raise ValueError("Ảnh không có dữ liệu")

        image = Image.open(
            io.BytesIO(image_bytes)
        ).convert("RGB")

        tensor = (
            self.preprocess(image)
            .unsqueeze(0)
            .to(self.device)
        )

        with self._lock:
            with torch.inference_mode():
                features = self.model.encode_image(
                    tensor
                )

                features = features / features.norm(
                    dim=-1,
                    keepdim=True,
                )

        return (
            features[0]
            .detach()
            .cpu()
            .float()
            .tolist()
        )

    @staticmethod
    def checksum(
        image_bytes: bytes,
    ) -> str:
        return hashlib.sha256(
            image_bytes
        ).hexdigest()