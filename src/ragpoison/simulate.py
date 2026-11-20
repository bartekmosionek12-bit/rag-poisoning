"""Offline simulation of the store: same chunker and model, numpy instead of Chroma.

Lets you predict what injecting a candidate would do to the canary rankings
without writing anything to the database. In the reference run the simulated
numbers matched the live injection exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from .metrics import Ranking

EmbedFn = Callable[[list[str]], np.ndarray]
ChunkFn = Callable[[str], list[str]]


@dataclass
class Index:
    doc_ids: list[str]  # one entry per chunk
    vecs: np.ndarray    # unit-norm chunk embeddings, shape (n_chunks, dim)
    poison: set[str]

    @classmethod
    def build(cls, docs: dict[str, str], embed_fn: EmbedFn, chunk_fn: ChunkFn, poison: bool = False) -> "Index":
        ids, texts = [], []
        for d, t in docs.items():
            for c in chunk_fn(t):
                ids.append(d)
                texts.append(c)
        vecs = embed_fn(texts) if texts else np.zeros((0, 0))
        return cls(ids, vecs, set(docs) if poison else set())

    def plus(self, other: "Index") -> "Index":
        if not other.doc_ids:
            return self
        return Index(self.doc_ids + other.doc_ids, np.vstack([self.vecs, other.vecs]), self.poison | other.poison)

    def rank(self, query_vec: np.ndarray) -> Ranking:
        """Document-level ranking (best chunk per doc), same shape as store.retrieve()."""
        best: dict[str, float] = {}
        for d, s in zip(self.doc_ids, self.vecs @ query_vec):
            best[d] = max(best.get(d, -1.0), float(s))
        order = sorted(best, key=best.get, reverse=True)
        return [{"doc_id": d, "rank": i, "score": round(best[d], 4), "poison": d in self.poison}
                for i, d in enumerate(order, 1)]


def doc_centrality(text: str, query_vecs: np.ndarray, embed_fn: EmbedFn, chunk_fn: ChunkFn) -> np.ndarray:
    """Per query: similarity of the document's best chunk. Shape (n_queries,)."""
    return (embed_fn(chunk_fn(text)) @ query_vecs.T).max(axis=0)
