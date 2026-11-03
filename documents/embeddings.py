"""Client for an optional local llama.cpp embeddings server."""
from __future__ import annotations

import math
from typing import Protocol, Sequence

import requests

from app_config import API_REQUEST_TIMEOUT_SECONDS, EMBEDDINGS_URL


class EmbeddingError(RuntimeError):
    """Raised when the configured local embedding service cannot encode text."""


class EmbeddingProvider(Protocol):
    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one normalized vector for each input string."""


class LocalEmbeddingClient:
    """Request normalized vectors from a loopback llama.cpp embeddings endpoint."""

    def __init__(self, url: str = EMBEDDINGS_URL, timeout: float = API_REQUEST_TIMEOUT_SECONDS):
        self.url = url
        self.timeout = timeout

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        dimension: int | None = None
        for start in range(0, len(texts), 32):
            batch = texts[start:start + 32]
            try:
                response = requests.post(
                    self.url, json={"input": list(batch)}, timeout=self.timeout
                )
                response.raise_for_status()
                payload = response.json()
            except (requests.RequestException, ValueError) as error:
                raise EmbeddingError("The local embeddings service did not return a valid response.") from error

            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                raise EmbeddingError("The local embeddings service returned an unexpected response.")

            ordered: list[list[float] | None] = [None] * len(batch)
            for item in data:
                if not isinstance(item, dict):
                    raise EmbeddingError("The local embeddings service returned an invalid vector.")
                index = item.get("index")
                embedding = item.get("embedding")
                if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(batch):
                    raise EmbeddingError("The local embeddings service returned an invalid vector index.")
                if ordered[index] is not None or not isinstance(embedding, list) or not embedding:
                    raise EmbeddingError("The local embeddings service returned an invalid vector.")
                try:
                    vector = [float(value) for value in embedding]
                except (TypeError, ValueError) as error:
                    raise EmbeddingError("The local embeddings service returned a non-numeric vector.") from error
                if not all(math.isfinite(value) for value in vector):
                    raise EmbeddingError("The local embeddings service returned a non-finite vector.")
                norm = math.sqrt(sum(value * value for value in vector))
                if norm == 0:
                    raise EmbeddingError("The local embeddings service returned a zero-length vector.")
                if dimension is None:
                    dimension = len(vector)
                if len(vector) != dimension:
                    raise EmbeddingError("The local embeddings service returned vectors with inconsistent dimensions.")
                ordered[index] = [value / norm for value in vector]

            if any(vector is None for vector in ordered):
                raise EmbeddingError("The local embeddings service omitted one or more vectors.")
            vectors.extend(vector for vector in ordered if vector is not None)
        return vectors
