from typing import Any

from app.services.order_service import OrderService
from app.services.warranty_service import WarrantyService


order_service = OrderService()
warranty_service = WarrantyService()


def search_order(
    phone: str,
    order_code: str | None = None,
) -> dict[str, Any]:
    """
    Tìm đơn hàng của khách theo số điện thoại.

    Args:
        phone:
            Số điện thoại khách dùng khi đặt hàng.

        order_code:
            Mã đơn hàng nếu khách đã cung cấp.
            Tham số này có thể để trống.

    Returns:
        Kết quả tìm kiếm đơn hàng gồm trạng thái,
        số lượng đơn và danh sách đơn phù hợp.
    """

    try:
        orders = order_service.search(
            phone=phone,
            order_code=order_code,
        )

        if not orders:
            return {
                "success": False,
                "status": "order_not_found",
                "message": (
                    "Không tìm thấy đơn hàng phù hợp "
                    "với số điện thoại và mã đơn được cung cấp."
                ),
                "orders": [],
            }

        safe_orders = []

        for order in orders:
            safe_orders.append(
                {
                    "order_code": order.get("order_code"),
                    "order_status": order.get("order_status"),
                    "warranty_status": order.get(
                        "warranty_status",
                        "not_activated",
                    ),
                    "products": order.get("products", []),
                }
            )

        return {
            "success": True,
            "status": "order_found",
            "count": len(safe_orders),
            "orders": safe_orders,
        }

    except Exception as error:
        return {
            "success": False,
            "status": "search_error",
            "message": str(error),
            "orders": [],
        }


def activate_warranty(
    order_code: str,
    customer_id: str,
) -> dict[str, Any]:
    """
    Kích hoạt bảo hành cho một đơn hàng cụ thể.

    Chỉ gọi hàm này khi đã xác định duy nhất một mã đơn hàng.

    Args:
        order_code:
            Mã đơn hàng cần kích hoạt bảo hành.

        customer_id:
            Mã định danh người đang nhắn tin, ví dụ
            Facebook PSID, Zalo user ID hoặc ID website.

    Returns:
        Trạng thái kích hoạt bảo hành.
    """

    try:
        order = order_service.get_by_order_code(
            order_code=order_code
        )

        if not order:
            return {
                "success": False,
                "status": "order_not_found",
                "message": "Không tìm thấy mã đơn hàng.",
            }

        order_status = order.get("order_status")

        if order_status != "completed":
            return {
                "success": False,
                "status": "order_not_eligible",
                "order_code": order.get("order_code"),
                "message": (
                    "Đơn hàng chưa ở trạng thái đủ điều kiện "
                    "để kích hoạt bảo hành."
                ),
            }

        result = warranty_service.activate(
            order=order,
            customer_id=customer_id,
        )

        if result.get("already_activated"):
            return {
                "success": True,
                "status": "already_activated",
                "order_code": order.get("order_code"),
                "warranty_id": result.get("warranty_id"),
                "activated_at": result.get("activated_at"),
                "message": (
                    "Đơn hàng đã được kích hoạt bảo hành trước đó."
                ),
            }

        return {
            "success": True,
            "status": "activated",
            "order_code": order.get("order_code"),
            "warranty_id": result.get("warranty_id"),
            "activated_at": result.get("activated_at"),
            "message": "Kích hoạt bảo hành thành công.",
        }

    except Exception as error:
        return {
            "success": False,
            "status": "activation_error",
            "message": str(error),
        }