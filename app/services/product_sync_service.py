import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.scripts.build_product_image_embeddings import (
    main as build_product_image_embeddings,
)
from app.scripts.sync_product_images import sync_product_images
from test import (
    find_products_by_sku,
    import_products_to_database,
    normalize_sku,
    save_product,
)


ProgressCallback = Callable[[str], None]


@dataclass
class SyncJob:
    id: str
    skus: list[str]
    status: str = "queued"
    current_sku: str | None = None
    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    phase: str = "queued"
    phase_label: str = "Đang chờ xử lý"
    completed_units: int = 0
    messages: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "skus": self.skus,
            "total": len(self.skus),
            "current_sku": self.current_sku,
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "phase": self.phase,
            "phase_label": self.phase_label,
            "completed_units": self.completed_units,
            "total_units": len(self.skus) * 4 + 1,
            "messages": self.messages[-100:],
            "results": self.results,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


class ProductSyncManager:
    """Chạy tuần tự để tránh nhiều job cùng ghi catalog và database."""

    def __init__(self) -> None:
        self._jobs: dict[str, SyncJob] = {}
        self._lock = threading.RLock()
        self._worker_lock = threading.Lock()

    @staticmethod
    def normalize_skus(values: list[str]) -> list[str]:
        normalized = [normalize_sku(value) for value in values]
        return list(dict.fromkeys(value for value in normalized if value))

    def create(self, skus: list[str]) -> SyncJob:
        normalized = self.normalize_skus(skus)
        if not normalized:
            raise ValueError("Danh sách không có mã sản phẩm hợp lệ.")
        if len(normalized) > 1000:
            raise ValueError("Mỗi lần chỉ được import tối đa 1.000 mã.")

        job = SyncJob(id=uuid.uuid4().hex, skus=normalized)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.public() if job else None

    def _message(self, job: SyncJob, message: str) -> None:
        with self._lock:
            job.messages.append(message)

    def _phase(self, job: SyncJob, phase: str, label: str) -> None:
        with self._lock:
            job.phase = phase
            job.phase_label = label

    def run(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]

        with self._worker_lock:
            job.status = "running"
            all_imported_products: list[dict[str, Any]] = []

            for sku_index, sku in enumerate(job.skus):
                job.current_sku = sku
                self._message(job, f"Đang lấy {sku} từ Shopify…")

                try:
                    self._phase(job, "shopify", f"Đang lấy {sku} từ Shopify")
                    products = find_products_by_sku(sku)
                    if not products:
                        raise ValueError("Không tìm thấy sản phẩm ACTIVE.")
                    job.completed_units = sku_index * 4 + 1

                    self._phase(job, "catalog", f"Đang lưu catalog {sku}")
                    for product in products:
                        save_product(product)
                    job.completed_units = sku_index * 4 + 2

                    self._phase(job, "images", f"Đang tải ảnh {sku}")
                    image_result = sync_product_images(
                        products,
                        remove_stale=False,
                    )
                    job.completed_units = sku_index * 4 + 3

                    self._phase(job, "database", f"Đang import database {sku}")
                    sync_run_id = import_products_to_database(products)
                    job.completed_units = sku_index * 4 + 4
                    all_imported_products.extend(products)

                    result = {
                        "sku": sku,
                        "success": True,
                        "shopify_products": len(products),
                        "downloaded": image_result["downloaded"],
                        "skipped_images": image_result["skipped"],
                        "failed_images": image_result["failed"],
                        "sync_run_id": sync_run_id,
                    }
                    job.results.append(result)
                    job.succeeded += 1
                    self._message(job, f"Đã import {sku} thành công.")
                except Exception as error:
                    job.failed += 1
                    job.results.append({
                        "sku": sku,
                        "success": False,
                        "error": str(error),
                    })
                    self._message(job, f"Lỗi {sku}: {error}")
                finally:
                    job.processed += 1
                    job.completed_units = max(
                        job.completed_units,
                        (sku_index + 1) * 4,
                    )

            if all_imported_products:
                self._phase(job, "embedding", "Đang tạo embedding cho ảnh mới")
                self._message(job, "Đang tạo embedding cho ảnh mới…")
                try:
                    embedding_result = build_product_image_embeddings()
                    self._message(
                        job,
                        "Embedding hoàn tất: "
                        f"created={embedding_result.get('created', 0)}, "
                        f"skipped={embedding_result.get('skipped', 0)}, "
                        f"failed={embedding_result.get('failed', 0)}.",
                    )
                except Exception as error:
                    self._message(job, f"Cảnh báo embedding: {error}")

            job.completed_units = len(job.skus) * 4 + 1
            job.current_sku = None
            job.status = "completed" if job.failed == 0 else "completed_with_errors"
            job.phase = job.status
            job.phase_label = (
                "Đã đồng bộ hoàn tất"
                if job.failed == 0
                else "Hoàn tất nhưng có mã bị lỗi"
            )
            job.completed_at = datetime.now(timezone.utc).isoformat()


product_sync_manager = ProductSyncManager()
