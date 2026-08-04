import json
from typing import Any

from app.database.product_repository import ProductRepository
from app.product_recognition.catalog_service import ProductCatalogService


FIELDS = (
    "product_code",
    "product_name",
    "product_type",
    "material",
    "sole",
    "height",
    "status",
    "prices",
    "colors",
    "available_sizes",
    "availability_by_color",
    "image_urls",
    "image_urls_by_color",
)


def canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: canonical(item)
            for key, item in sorted(value.items())
        }
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return sorted(value)
        return [canonical(item) for item in value]
    return value


def main() -> None:
    json_catalog = ProductCatalogService(source="json")
    repository = ProductRepository()
    codes = sorted(
        set(repository.all_product_codes())
        | set(json_catalog._by_code)
    )

    mismatch_count = 0
    for code in codes:
        expected = json_catalog.public_info(code)
        actual = repository.public_info(code)
        differences = {}

        if expected is None or actual is None:
            differences["record"] = {
                "json": expected is not None,
                "database": actual is not None,
            }
        else:
            for field in FIELDS:
                expected_value = canonical(expected.get(field))
                actual_value = canonical(actual.get(field))
                if expected_value != actual_value:
                    differences[field] = {
                        "json": expected_value,
                        "database": actual_value,
                    }

        if differences:
            mismatch_count += 1
            print(f"MISMATCH {code}")
            print(json.dumps(
                differences,
                ensure_ascii=False,
                indent=2,
            ))
        else:
            print(f"OK {code}")

    print(
        f"Compared products={len(codes)} "
        f"matched={len(codes) - mismatch_count} "
        f"mismatched={mismatch_count}"
    )
    if mismatch_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
