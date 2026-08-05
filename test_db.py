import argparse
import sys
import time
from pathlib import Path

from app.database.product_embedding_repository import (
    ProductEmbeddingRepository,
)
from app.product_recognition.image_embedding_service import (
    ImageEmbeddingService,
)
from app.product_recognition.image_crop import crop_product_region
from app.product_recognition.vector_decision import (
    decide_vector_match,
    group_vector_results,
)


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_IMAGE = (
    PROJECT_ROOT
    / "data"
    / "product_images"
    / "FE04"
    / "test.jpg"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test nhận diện ảnh sản phẩm bằng pgvector",
    )
    parser.add_argument(
        "image",
        nargs="?",
        type=Path,
        default=DEFAULT_IMAGE,
        help="Đường dẫn ảnh cần kiểm tra",
    )
    parser.add_argument(
        "--expected",
        help="Mã sản phẩm mong đợi, ví dụ FE02",
    )
    parser.add_argument(
        "--product-type",
        help="Chỉ tìm trong một product_type; mặc định tìm toàn bộ",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Số ảnh gần nhất lấy từ pgvector",
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=int,
        metavar=("YMIN", "XMIN", "YMAX", "XMAX"),
        help=(
            "Crop vùng sản phẩm theo tọa độ 0..1000, "
            "cùng định dạng với bounding_box của Telegram"
        ),
    )
    return parser.parse_args()


def resolve_image_path(path: Path) -> Path:
    resolved = path if path.is_absolute() else PROJECT_ROOT / path
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Không tìm thấy ảnh: {resolved}")
    return resolved


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    image_path = resolve_image_path(args.image)
    expected = str(args.expected or "").strip().upper()
    limit = max(1, args.limit)

    print("========== VECTOR IMAGE TEST ==========")
    print(f"Ảnh: {image_path}")
    print(f"Expected: {expected or '(không khai báo)'}")
    print(f"Product type: {args.product_type or '(toàn bộ)'}")
    print(f"Bounding box: {args.bbox or '(không crop)'}")

    started_at = time.perf_counter()
    service = ImageEmbeddingService()
    repository = ProductEmbeddingRepository()
    model_ready_at = time.perf_counter()

    original_image_bytes = image_path.read_bytes()
    image_bytes, _, crop_applied = crop_product_region(
        original_image_bytes,
        args.bbox,
    )
    print(
        "Crop applied: "
        f"{crop_applied} "
        f"({len(original_image_bytes)} -> {len(image_bytes)} bytes)"
    )
    embedding = service.embed_bytes(image_bytes)
    embedded_at = time.perf_counter()

    results = repository.search(
        embedding=embedding,
        model_name=service.model_name,
        pretrained_name=service.pretrained_name,
        product_type=args.product_type,
        limit=limit,
    )
    searched_at = time.perf_counter()

    decision = decide_vector_match(results)
    grouped_by_color = group_vector_results(results)
    completed_at = time.perf_counter()

    print("\n========== ẢNH GẦN NHẤT ==========")
    if not results:
        print("Database không trả về kết quả vector.")
    for index, result in enumerate(results[:10], start=1):
        print(
            f"{index:02d}. "
            f"code={result['product_code']} "
            f"color={result.get('color') or '-'} "
            f"similarity={float(result['similarity']):.4f} "
            f"path={result.get('local_path') or '-'}"
        )

    print("\n========== ĐÃ GOM THEO MÀU ==========")
    if not grouped_by_color:
        print("Không có ứng viên sau khi gom.")
    for index, candidate in enumerate(grouped_by_color[:10], start=1):
        print(
            f"{index:02d}. "
            f"code={candidate['product_code']} "
            f"color={candidate.get('color') or '-'} "
            f"similarity={candidate['similarity']:.4f} "
            f"path={candidate.get('local_path') or '-'}"
        )

    print("\n========== QUYẾT ĐỊNH ==========")
    print(f"Status: {decision.status}")
    print(f"Top similarity: {decision.top_similarity:.4f}")
    print(f"Second similarity: {decision.second_similarity:.4f}")
    print(f"Margin: {decision.margin:.4f}")

    if decision.best_candidate:
        predicted = decision.best_candidate["product_code"]
        print(f"Predicted product: {predicted}")
        print(
            "Predicted color: "
            f"{decision.best_candidate.get('color') or '-'}"
        )
        if expected:
            print(
                "Test result: "
                f"{'PASS' if predicted == expected else 'FAIL'}"
            )
    elif expected:
        print("Test result: FAIL")

    print("\n========== THỜI GIAN ==========")
    print(f"Load model: {model_ready_at - started_at:.3f}s")
    print(f"Create embedding: {embedded_at - model_ready_at:.3f}s")
    print(f"Vector search: {searched_at - embedded_at:.3f}s")
    print(f"Decision: {completed_at - searched_at:.3f}s")
    print(f"Total: {completed_at - started_at:.3f}s")


if __name__ == "__main__":
    main()
