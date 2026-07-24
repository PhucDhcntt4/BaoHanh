from datetime import datetime, timezone, timedelta
from typing import Any
from uuid import uuid4

from app.services.json_store import JsonStore


VN_TIMEZONE = timezone(timedelta(hours=7))


class WarrantyService:
    def __init__(
        self,
        warranty_path: str = "data/warranties.json",
        order_path: str = "data/orders.json",
    ) -> None:
        self.warranty_store = JsonStore(warranty_path)
        self.order_store = JsonStore(order_path)

    def activate(
        self,
        order: dict[str, Any],
        customer_id: str,
    ) -> dict[str, Any]:

        warranties = self.warranty_store.read(default=[])

        existing = next(
            (
                item
                for item in warranties
                if item.get("order_code") == order.get("order_code")
                and item.get("status") == "activated"
            ),
            None,
        )

        if existing:
            return {
                **existing,
                "already_activated": True,
            }

        now = datetime.now(VN_TIMEZONE).isoformat()

        warranty = {
            "warranty_id": str(uuid4()),
            "order_code": order["order_code"],
            "phone": order["phone"],
            "customer_id": customer_id,
            "products": order.get("products", []),
            "status": "activated",
            "activated_at": now,
        }

        warranties.append(warranty)

        self.warranty_store.write(warranties)

        self._mark_order_activated(
            order_code=order["order_code"],
            activated_at=now,
        )

        return {
            **warranty,
            "already_activated": False,
        }

    def _mark_order_activated(
        self,
        order_code: str,
        activated_at: str,
    ) -> None:

        orders = self.order_store.read(default=[])

        for order in orders:
            if order.get("order_code") == order_code:
                order["warranty_status"] = "activated"
                order["warranty_activated_at"] = activated_at
                break

        self.order_store.write(orders)