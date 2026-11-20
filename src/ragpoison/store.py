"""The victim RAG store: ChromaDB collection, ingestion and retrieval.

Ingestion and querying are separate functions on purpose: the attack only
needs write access to the store, never access to the query path.
"""
from __future__ import annotations

from .config import Settings
from .models import cross_scores, embed
from .text import chunk_text


def open_collection(s: Settings, name: str, reset: bool = False):
    import chromadb
    client = chromadb.PersistentClient(path=s.db_path)
    if reset:
        try:
            client.delete_collection(name)
        except Exception:  # collection did not exist yet
            pass
    return client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})


def ingest(s: Settings, coll, docs: dict[str, str], *, poison: bool, source: str) -> int:
    """Chunk, embed and upsert documents. Returns the number of chunks written.

    The `poison` flag is bookkeeping for scoring only; retrieval never reads it.
    """
    ids, texts, metas = [], [], []
    for doc_id, text in docs.items():
        for i, chunk in enumerate(chunk_text(text, s.chunk_size, s.chunk_overlap)):
            ids.append(f"{doc_id}::{i}")
            texts.append(chunk)
            metas.append({"doc_id": doc_id, "chunk": i, "poison": poison, "source": source})
    if ids:
        coll.upsert(ids=ids, documents=texts, metadatas=metas,
                    embeddings=embed(texts, s.embed_model).tolist())
    return len(ids)


def vector_search(s: Settings, coll, query: str, n_results: int) -> list[dict]:
    """Chunk-level hits; score = 1 - cosine distance."""
    n = min(n_results, coll.count())
    if n == 0:
        return []
    res = coll.query(query_embeddings=embed([query], s.embed_model).tolist(), n_results=n,
                     include=["documents", "metadatas", "distances"])
    return [{"chunk_id": cid, "doc_id": meta["doc_id"], "poison": bool(meta.get("poison", False)),
             "text": doc, "score": round(1.0 - float(dist), 4)}
            for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                            res["metadatas"][0], res["distances"][0])]


def rerank(s: Settings, query: str, hits: list[dict]) -> list[dict]:
    """Re-score (query, chunk) pairs with the cross-encoder and re-sort."""
    if not hits:
        return hits
    scores = cross_scores([(query, h["text"]) for h in hits], s.rerank_model)
    out = [dict(h, vector_score=h["score"], score=round(float(sc), 4)) for h, sc in zip(hits, scores)]
    return sorted(out, key=lambda h: h["score"], reverse=True)


def doc_level(hits: list[dict]) -> list[dict]:
    """Collapse chunk hits to documents (best chunk wins), keeping order."""
    seen, out = set(), []
    for h in hits:
        if h["doc_id"] not in seen:
            seen.add(h["doc_id"])
            out.append(h)
    return out


def retrieve(s: Settings, coll, query: str, k: int = 0, use_rerank: bool = False) -> list[dict]:
    """Document-level ranking with a `rank` field. k=0 returns everything.

    Vector-only ranks the whole collection, so the target's rank is always
    known. With rerank, only the top `rerank_pool` chunks are re-scored, so the
    target can fall out of the pool entirely (reported as rank None).
    """
    if use_rerank:
        hits = rerank(s, query, vector_search(s, coll, query, s.rerank_pool))
    else:
        hits = vector_search(s, coll, query, coll.count())
    docs = doc_level(hits)
    for i, d in enumerate(docs, 1):
        d["rank"] = i
    return docs[:k] if k else docs
