import unittest
from unittest.mock import patch

from documents.embeddings import EmbeddingError, LocalEmbeddingClient


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": self.data}


class LocalEmbeddingClientTests(unittest.TestCase):
    def test_embeddings_are_normalized_and_returned_in_input_order(self):
        response = FakeResponse([
            {"index": 1, "embedding": [0.0, 2.0]},
            {"index": 0, "embedding": [3.0, 4.0]},
        ])
        with patch("documents.embeddings.requests.post", return_value=response):
            vectors = LocalEmbeddingClient().embed_texts(["first", "second"])

        self.assertEqual(vectors[0], [0.6, 0.8])
        self.assertEqual(vectors[1], [0.0, 1.0])

    def test_large_batches_are_split_and_reassembled(self):
        def fake_post(_url, json, timeout):
            return FakeResponse([
                {"index": index, "embedding": [1.0, 0.0]}
                for index, _text in enumerate(json["input"])
            ])

        with patch("documents.embeddings.requests.post", side_effect=fake_post) as post:
            vectors = LocalEmbeddingClient().embed_texts(["passage"] * 33)

        self.assertEqual(len(vectors), 33)
        self.assertEqual(post.call_count, 2)

    def test_malformed_embedding_response_is_reported(self):
        response = FakeResponse([{"index": 0, "embedding": [0.0, 0.0]}])
        with patch("documents.embeddings.requests.post", return_value=response):
            with self.assertRaises(EmbeddingError):
                LocalEmbeddingClient().embed_texts(["passage"])


if __name__ == "__main__":
    unittest.main()
