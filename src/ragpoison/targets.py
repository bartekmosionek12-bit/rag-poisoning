"""Retrieval targets the attack can be measured against.

LocalTarget   the bundled ChromaDB store (ingest + vector search + optional rerank).
RemoteTarget  any RAG service reachable over HTTP that speaks a small JSON contract:

    POST {base_url}{query_path}   {"query": "...", "k": 10}
      ->  {"results": [{"doc_id": "...", "score": 0.42, "text": "..."}, ...]}   best first

    POST {base_url}{ingest_path}  {"doc_id": "...", "text": "..."}   (optional)
      ->  any 2xx

Field names are configurable (results_key / id_key / score_key / text_key), so
most existing services can be pointed at without a shim. `ragpoison serve`
exposes this same contract over its own local store at /rag/query and
/rag/ingest, so one instance can serve as the remote target of another.

Both targets return document-level rankings in the shape metrics.py expects:
[{"doc_id", "rank", "score", "poison", "text"}], best first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from .config import Settings
from .metrics import Ranking

POISON_MARKER = "LAB-MARKER"


class Target(Protocol):
    kind: str

    def retrieve(self, query: str, k: int = 0, use_rerank: bool = False) -> Ranking: ...

    def inject(self, docs: dict[str, str]) -> dict: ...

    def info(self) -> dict: ...


@dataclass
class LocalTarget:
    s: Settings
    collection: str
    kind: str = "local"

    def _coll(self):
        from .store import open_collection
        return open_collection(self.s, self.collection)

    def retrieve(self, query: str, k: int = 0, use_rerank: bool = False) -> Ranking:
        from .store import retrieve
        return retrieve(self.s, self._coll(), query, k=k, use_rerank=use_rerank)

    def inject(self, docs: dict[str, str]) -> dict:
        from .store import ingest
        coll = self._coll()
        if coll.count() == 0:
            raise RuntimeError(f"collection '{self.collection}' is empty; build it first")
        chunks = {d: ingest(self.s, coll, {d: t}, poison=True, source=d) for d, t in docs.items()}
        return {"injected": chunks, "total_chunks": coll.count()}

    def info(self) -> dict:
        from .store import open_collection
        import chromadb
        client = chromadb.PersistentClient(path=self.s.db_path)
        names = sorted(c.name if hasattr(c, "name") else str(c) for c in client.list_collections())
        coll = open_collection(self.s, self.collection) if self.collection in names else None
        docs: dict[str, bool] = {}
        if coll is not None and coll.count():
            for m in coll.get(include=["metadatas"])["metadatas"]:
                docs[m["doc_id"]] = docs.get(m["doc_id"], False) or bool(m.get("poison"))
        return {"kind": "local", "collection": self.collection, "collections": names,
                "chunks": coll.count() if coll is not None else 0,
                "docs": [{"doc_id": d, "poison": p} for d, p in sorted(docs.items())]}


@dataclass
class RemoteTarget:
    s: Settings
    base_url: str
    query_path: str = "/query"
    ingest_path: str | None = "/ingest"
    headers: dict[str, str] = field(default_factory=dict)
    results_key: str = "results"
    id_key: str = "doc_id"
    score_key: str = "score"
    text_key: str = "text"
    pool: int = 10                     # how many results to request
    poison_ids: set[str] = field(default_factory=set)
    timeout: float = 30.0
    kind: str = "remote"

    def _post(self, path: str, payload: dict) -> dict:
        import httpx
        r = httpx.post(self.base_url.rstrip("/") + path, json=payload, headers=self.headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json() if r.content else {}

    def raw_hits(self, query: str, n: int) -> list[dict]:
        data = self._post(self.query_path, {"query": query, "k": n})
        items = data.get(self.results_key, []) if isinstance(data, dict) else data
        hits = []
        for it in items:
            text = it.get(self.text_key) or ""
            doc_id = str(it[self.id_key])
            hits.append({"doc_id": doc_id, "score": round(float(it.get(self.score_key, 0.0)), 4), "text": text,
                         "poison": doc_id in self.poison_ids or POISON_MARKER in text})
        return hits

    def retrieve(self, query: str, k: int = 0, use_rerank: bool = False) -> Ranking:
        """Rankings come from the remote service. With use_rerank, the returned texts are
        re-scored locally with the cross-encoder: a client-side rerank defense."""
        from .store import doc_level, rerank
        hits = self.raw_hits(query, self.pool)
        if use_rerank:
            if not all(h["text"] for h in hits):
                raise RuntimeError("client-side rerank needs the remote results to include text")
            hits = rerank(self.s, query, hits)
        docs = doc_level(hits)
        for i, d in enumerate(docs, 1):
            d["rank"] = i
        return docs[:k] if k else docs

    def inject(self, docs: dict[str, str]) -> dict:
        if not self.ingest_path:
            raise RuntimeError("this remote target has no ingest endpoint; add the documents yourself, then measure")
        for d, t in docs.items():
            self._post(self.ingest_path, {"doc_id": d, "text": t})
        self.poison_ids |= set(docs)
        return {"injected": {d: None for d in docs}}

    def info(self) -> dict:
        return {"kind": "remote", "base_url": self.base_url, "query_path": self.query_path,
                "ingest_path": self.ingest_path, "pool": self.pool}
