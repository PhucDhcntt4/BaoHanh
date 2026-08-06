import argparse
import json

from app.knowledge.service import KnowledgeSearchService


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kiểm tra kết quả truy xuất RAG từ terminal"
    )
    parser.add_argument("question", help="Câu hỏi cần tìm")
    parser.add_argument(
        "--category",
        action="append",
        dest="categories",
        help="Lọc category; có thể truyền nhiều lần",
    )
    args = parser.parse_args()

    result = KnowledgeSearchService().search(
        args.question,
        categories=args.categories,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()