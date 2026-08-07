import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

from app.config import (
    KNOWLEDGE_DIR,
    PROJECT_ROOT,
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
)
from app.database.connection import database_connection
from app.database.knowledge_repository import KnowledgeRepository
from app.knowledge.chunking import TextChunk, chunk_text
from app.knowledge.embedding_service import (
    TextEmbeddingService,
    create_text_embedding_service,
)


SCHEMA_PATH = PROJECT_ROOT / "db_postgre" / "003_customer_care_rag.sql"
SUPPORTED_SUFFIXES = {".md", ".txt"}
CATEGORY_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,99}$"
)

def normalize_category(value: str) -> str:
    category = value.strip().casefold().replace(" ", "_")

    if not CATEGORY_PATTERN.fullmatch(category):
        raise ValueError(
            "tên catarogy chỉ đđượcchuws chữ thường không dấu,số, dấu gạch dưới hoặc ngang"
        )

    return category

def initialize_schema() -> None:
    with database_connection() as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def discover_files(source: Path) -> list[Path]:
    if source.is_file():
        if source.suffix.casefold() not in SUPPORTED_SUFFIXES:
            raise ValueError("RAG hiện chỉ hỗ trợ file .txt và .md")
        return [source]
    if not source.exists():
        raise FileNotFoundError(f"Không tìm thấy nguồn tài liệu: {source}")
    return sorted(
        path
        for path in source.rglob("*")
        if (
            path.is_file()
            and path.suffix.casefold() in SUPPORTED_SUFFIXES
            and path.name.casefold() != "readme.md"
            and not any(part.startswith(".") for part in path.parts)
        )
    )


def document_title(path: Path, content: str) -> str:
    match = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    return path.stem.replace("_", " ").replace("-", " ").strip()


def document_category(
    path: Path,
    source: Path,
    explicit: str | None,
) -> str:
    if explicit:
        return normalize_category(explicit)

    try:
        relative_path = path.resolve().relative_to(
            KNOWLEDGE_DIR.resolve()
        )

        # Ví dụ:
        # knowledge/shipping/file.txt
        # → relative_path.parts[0] = shipping
        if len(relative_path.parts) >= 2:
            return normalize_category(
                relative_path.parts[0]
            )

    except ValueError:
        pass

    return "customer_care"

def source_key(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return path.name


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embedding_text(title: str, chunk: TextChunk) -> str:
    parts = [title]
    if chunk.heading:
        parts.append(chunk.heading)
    parts.append(chunk.content)
    return "\n\n".join(parts)


def embed_in_batches(
    embedding_service: TextEmbeddingService,
    texts: list[str],
    batch_size: int = 50,
) -> list[list[float]]:
    result: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        result.extend(
            embedding_service.embed_documents(
                texts[start:start + batch_size]
            )
        )
    return result


def import_file(
    *,
    path: Path,
    source: Path,
    category: str | None,
    force: bool,
    repository: KnowledgeRepository,
    embedding_service: TextEmbeddingService,
) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return {"path": str(path), "status": "empty", "chunks": 0}

    key = source_key(path)
    source_checksum = checksum(content)
    state = repository.document_state(key)
    if (
        not force
        and state
        and state["source_checksum"] == source_checksum
        and state["embedding_provider"] == (
            embedding_service.provider_name
        )
        and state["embedding_model"] == embedding_service.model
        and state["embedding_dimension"] == embedding_service.dimension
        and state["is_active"]
    ):
        return {"path": str(path), "status": "unchanged", "chunks": 0}

    title = document_title(path, content)
    chunks = chunk_text(
        content,
        max_chars=RAG_CHUNK_SIZE,
        overlap_chars=RAG_CHUNK_OVERLAP,
    )
    if not chunks:
        return {"path": str(path), "status": "empty", "chunks": 0}

    embeddings = embed_in_batches(
        embedding_service,
        [embedding_text(title, chunk) for chunk in chunks],
    )
    stored_chunks = [
        {
            "chunk_index": chunk.index,
            "heading": chunk.heading,
            "content": chunk.content,
            "content_checksum": checksum(chunk.content),
            "embedding": embedding,
        }
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    resolved_category = document_category(path, source, category)
    repository.replace_document(
        source_key=key,
        title=title,
        category=resolved_category,
        source_checksum=source_checksum,
        embedding_provider=embedding_service.provider_name,
        embedding_model=embedding_service.model,
        embedding_dimension=embedding_service.dimension,
        metadata={"file_name": path.name},
        chunks=stored_chunks,
    )
    return {
        "path": str(path),
        "status": "imported",
        "category": resolved_category,
        "chunks": len(stored_chunks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Nạp tài liệu chính sách/CSKH vào PostgreSQL pgvector"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=KNOWLEDGE_DIR,
        help="File .txt/.md hoặc thư mục tài liệu",
    )
    parser.add_argument(
        "--category",
        help=(
            "Gán category cho nguồn. "
            "Ví dụ: shipping, size_guide, payment"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Tạo lại embedding dù nội dung chưa thay đổi",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ kiểm tra file và chia đoạn, không gọi API/ghi DB",
    )
    args = parser.parse_args()

    source = args.source.resolve()
    files = discover_files(source)
    if not files:
        raise RuntimeError(f"Không tìm thấy file .txt/.md trong {source}")

    if args.dry_run:
        for path in files:
            content = path.read_text(encoding="utf-8").strip()
            chunks = chunk_text(
                content,
                max_chars=RAG_CHUNK_SIZE,
                overlap_chars=RAG_CHUNK_OVERLAP,
            ) if content else []
            print(f"DRY-RUN {path}: {len(chunks)} chunks")
        return

    initialize_schema()
    repository = KnowledgeRepository()
    embedding_service = create_text_embedding_service()
    print(
        "Embedding: "
        f"provider={embedding_service.provider_name}, "
        f"model={embedding_service.model}, "
        f"dimension={embedding_service.dimension}"
    )
    imported = unchanged = skipped = 0
    for path in files:
        result = import_file(
            path=path,
            source=source,
            category=args.category,
            force=args.force,
            repository=repository,
            embedding_service=embedding_service,
        )
        print(
            f"{result['status'].upper()} {result['path']} "
            f"chunks={result['chunks']}"
        )
        imported += result["status"] == "imported"
        unchanged += result["status"] == "unchanged"
        skipped += result["status"] == "empty"

    print(
        "Hoàn tất: "
        f"imported={imported}, unchanged={unchanged}, skipped={skipped}"
    )


if __name__ == "__main__":
    main()
