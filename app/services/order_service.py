import re
from typing import Any

from app.services.json_store import JsonStore


def normalize_phone(value: str | None) -> str | None:
    if not value:
        return None
    phone = re.sub(r"\D", "", value)
    return phone or None

def normalize_order_code( value: str| None) -> str | None:

    if not value:
        return None

    return value.strip().upper()

class OrderSerrvice:
    def __init__(self, path: str = "data/orders.json") -> None:
        self.store = JsonStore(path)

    def search(
        self,
        phone: str,
        order_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Tìm đơn theo số điện thoại.

        Nếu có order_code thì kiểm tra đồng thời:
        - Đúng số điện thoại
        - Đúng mã đơn
        """

        normalize_phone = normalize_phone(phone)
        normalize_order_code = normalize_order_code(order_code)

        if not normalize_phone:
            return[]

        orders = self.store.read(default=[])
        matches: list[dict[str, Any]] = []

        for order in orders:
            orders_phone = normalize_phone(order.get("phone"))

            order_code_value = normalize_order_code(order.get("order_code"))

            same_phone = orders_phone == normalize_phone

            same_order_code = (
                normalize_order_code is None
                or order_code_value == normalize_order_code
            )

            if same_phone and same_order_code:
                matches.append(order)

        return matches

    def get_by_order_code(
        self,
        order_code: str,
    ) -> dict[str, Any] | None:
        """
        Lấy một đơn hàng theo mã đơn.

        Hàm này được tool activate_warranty sử dụng sau khi
        Gemini đã xác định chính xác mã đơn cần kích hoạt.
        """

        normalized_order_code = normalize_order_code(
            order_code
        )

        if not normalized_order_code:
            return None

        orders = self.store.read(default=[])

        for order in orders:
            current_order_code = normalize_order_code(
                order.get("order_code")
            )

            if current_order_code == normalized_order_code:
                return order

        return None