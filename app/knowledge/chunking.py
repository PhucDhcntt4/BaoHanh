import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    heading: str | None
    content: str


HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*$")


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()


def _blocks(text: str) -> list[tuple[str | None, str]]:
    blocks: list[tuple[str | None, str]] = []
    heading: str | None = None
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            content = "\n".join(paragraph).strip()
            if content:
                blocks.append((heading, content))
            paragraph.clear()

    for line in _clean_text(text).splitlines():
        match = HEADING_PATTERN.match(line.strip())
        if match:
            flush()
            heading = match.group(1).strip()
            continue
        if not line.strip():
            flush()
            continue
        paragraph.append(line)
    flush()
    return blocks


def _tail_at_word_boundary(text: str, length: int) -> str:
    if length <= 0 or len(text) <= length:
        return text if length > 0 else ""
    tail = text[-length:]
    first_space = tail.find(" ")
    if first_space >= 0:
        tail = tail[first_space + 1:]
    return tail.strip()


def chunk_text(
    text: str,
    max_chars: int = 1200,
    overlap_chars: int = 180,
) -> list[TextChunk]:
    if max_chars < 200:
        raise ValueError("max_chars phải từ 200 trở lên")
    if overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("overlap_chars phải từ 0 đến nhỏ hơn max_chars")

    chunks: list[TextChunk] = []
    current_heading: str | None = None
    current = ""

    def emit() -> None:
        nonlocal current
        content = current.strip()
        if content:
            chunks.append(
                TextChunk(
                    index=len(chunks),
                    heading=current_heading,
                    content=content,
                )
            )
        current = _tail_at_word_boundary(content, overlap_chars)

    for heading, block in _blocks(text):
        if heading != current_heading and current.strip():
            emit()
            current = ""
        current_heading = heading

        remaining = block
        while remaining:
            separator = "\n\n" if current else ""
            available = max_chars - len(current) - len(separator)
            if available <= 0:
                emit()
                continue

            if len(remaining) <= available:
                current = f"{current}{separator}{remaining}".strip()
                remaining = ""
                continue

            split_at = remaining.rfind(" ", 0, available)
            if split_at < max(1, available // 2):
                split_at = available
            part = remaining[:split_at].strip()
            current = f"{current}{separator}{part}".strip()
            remaining = remaining[split_at:].strip()
            emit()

    emit()
    return chunks
