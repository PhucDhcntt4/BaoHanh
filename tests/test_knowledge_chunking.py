import unittest

from app.knowledge.chunking import chunk_text


class ChunkTextTests(unittest.TestCase):
    def test_preserves_markdown_heading(self) -> None:
        text = """# Chính sách bảo hành

Nội dung chung.

## Bong keo

Sản phẩm bong keo được tiếp nhận kiểm tra.
"""
        chunks = chunk_text(text, max_chars=220, overlap_chars=20)

        self.assertEqual(2, len(chunks))
        self.assertEqual("Chính sách bảo hành", chunks[0].heading)
        self.assertEqual("Bong keo", chunks[1].heading)

    def test_splits_long_content_with_size_limit(self) -> None:
        text = " ".join(f"từ-{index}" for index in range(300))
        chunks = chunk_text(text, max_chars=240, overlap_chars=30)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.content) <= 240 for chunk in chunks))

    def test_rejects_invalid_overlap(self) -> None:
        with self.assertRaises(ValueError):
            chunk_text("Nội dung", max_chars=200, overlap_chars=200)


if __name__ == "__main__":
    unittest.main()
