import unittest

from app.knowledge.service import KnowledgeSearchService


class FakeEmbeddingService:
    provider_name = "fake"
    model = "fake-model"
    dimension = 3

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


class FakeRepository:
    def __init__(self, rows):
        self.rows = rows
        self.arguments = None

    def search(self, **kwargs):
        self.arguments = kwargs
        return self.rows


class KnowledgeSearchServiceTests(unittest.TestCase):
    def test_returns_context_and_source_scores(self) -> None:
        repository = FakeRepository([{
            "source_key": "knowledge/warranty/policy.md",
            "title": "Chính sách bảo hành",
            "category": "warranty",
            "metadata": {},
            "chunk_index": 2,
            "heading": "Bong keo",
            "content": "Tiếp nhận sản phẩm để kiểm tra.",
            "similarity": 0.81234,
        }])
        service = KnowledgeSearchService(
            embedding_service=FakeEmbeddingService(),
            repository=repository,
            top_k=5,
            min_similarity=0.45,
            max_context_chars=6000,
        )

        result = service.search(
            "Giày bị bong keo có bảo hành không?",
            categories=["warranty"],
        )

        self.assertTrue(result["success"])
        self.assertEqual("knowledge_found", result["status"])
        self.assertIn("Bong keo", result["content"])
        self.assertEqual(0.8123, result["sources"][0]["similarity"])
        self.assertEqual(["warranty"], repository.arguments["categories"])
        self.assertEqual(
            "fake",
            repository.arguments["embedding_provider"],
        )

    def test_returns_not_found_without_context(self) -> None:
        service = KnowledgeSearchService(
            embedding_service=FakeEmbeddingService(),
            repository=FakeRepository([]),
            top_k=5,
            min_similarity=0.45,
            max_context_chars=6000,
        )

        result = service.search("Câu hỏi chưa có dữ liệu")

        self.assertFalse(result["success"])
        self.assertEqual("knowledge_not_found", result["status"])
        self.assertEqual("", result["content"])


if __name__ == "__main__":
    unittest.main()
