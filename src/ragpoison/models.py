"""Lazy model loading. Imports torch/sentence-transformers only when first used."""
from __future__ import annotations

from functools import lru_cache

import numpy as np


@lru_cache(maxsize=2)
def _embedder(name: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(name, device="cpu")


@lru_cache(maxsize=2)
def _cross_encoder(name: str):
    from sentence_transformers import CrossEncoder
    return CrossEncoder(name, device="cpu")


def embed(texts: list[str], model: str, batch_size: int = 64) -> np.ndarray:
    """Unit-normalised embeddings, so a dot product equals cosine similarity."""
    return _embedder(model).encode(list(texts), batch_size=batch_size,
                                   normalize_embeddings=True, convert_to_numpy=True)


def cross_scores(pairs: list[tuple[str, str]], model: str) -> np.ndarray:
    return np.asarray(_cross_encoder(model).predict(pairs))
