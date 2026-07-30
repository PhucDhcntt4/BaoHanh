from typing import Any

from app.product_recognition.catalog_service import (
    ProductCatalogService,
)


catalog_service = ProductCatalogService()


def search_products(
    query: str,
) -> dict[str, Any]:
    """
    Bắt buộc dùng hàm này khi khách hỏi cửa hàng có một loại sản phẩm
    hay không, kể cả khi trước đó đang trao đổi về một sản phẩm khác.

    Trước khi gọi, rút gọn câu nói tự nhiên thành nhu cầu sản phẩm chính.
    Ví dụ:
    - "e có túi k" -> query="túi"
    - "shop còn giày thể thao nam không" -> query="giày thể thao nam"

    Không được dùng sản phẩm trong lịch sử để thay thế việc tìm kiếm catalog.
    """

    products = catalog_service.search(
        query=query,
        active_only=True,
        limit=5,
    )
    if not products:
        return {
            "success": False,
            "status": "products_not_found",
            "products": [],
        }
    return {
        "success": True,
        "status": "products_found",
        "count": len(products),
        "products": products,
    }


def get_product_info(
    product_code: str,
) -> dict[str, Any]:
    """
    Lấy thông tin sản phẩm chính thức theo mã đã được xác định.

    Dùng khi khách hỏi tiếp về tên, giá, màu, size, trạng thái hoặc
    khả năng còn hàng của sản phẩm đã nhận diện từ ảnh.

    Args:
        product_code:
            Mã sản phẩm đã được xác định từ kết quả nhận diện hoặc
            do khách cung cấp.
    """

    product = catalog_service.public_info(product_code)
    if not product:
        return {
            "success": False,
            "status": "product_not_found",
        }
    return {
        "success": True,
        "status": "product_found",
        "product": product,
    }
