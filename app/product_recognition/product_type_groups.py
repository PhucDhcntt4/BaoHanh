import unicodedata


# Chỉ gom những loại đã xác minh là cùng một nhóm hình ảnh.
# Giữ nguyên giá trị đúng như trong PostgreSQL để truy vấn nhanh.
PRODUCT_TYPE_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({
        "GIÀY SNEAKER (MSN)",
        "GIÀY SNEAKER (WSN)",
        "GIAY THE THAO (WTT)",
        "GIAY THE THAO (MTT)",
        "GIAY THE THAO (KTT)",

    }),
    frozenset({
        "TUI XACH NHO (TXN)",
        "VI DI TIEC (VDT)",
    }),
    # Các loại sandal rất dễ bị bộ phân loại ảnh nhầm do góc chụp chỉ thấy
    # phần quai hoặc phần gót. Cho vector tìm trong cả họ sandal, sau đó bước
    # Gemini verifier sẽ so sánh chính xác cấu trúc đế/gót/quai của từng mã.
    frozenset({
        "SANDAL CAO GOT (WSC)",
        "SANDAL DE BANG (WSD)",
        "SANDAL DE XUONG (WSX)",
        "SANDAL KEP (WSK)",
    }),
)


def normalize_product_type(value: str) -> str:
    normalized = unicodedata.normalize(
        "NFD",
        str(value or "").strip().upper(),
    )
    return "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Mn"
    ).replace("Đ", "D")


def equivalent_product_types(product_type: str | None) -> list[str]:
    """Trả về các product_type DB tương đương với loại AI nhận diện."""

    normalized = normalize_product_type(product_type or "")
    if not normalized:
        return []

    for group in PRODUCT_TYPE_GROUPS:
        if normalized in {
            normalize_product_type(value)
            for value in group
        }:
            return sorted(group)

    # Loại chưa khai báo nhóm vẫn giữ hành vi cũ.
    return [str(product_type).strip()]
