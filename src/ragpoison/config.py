"""Run settings: where data lives, which models to use, which document is the target.

Every stage (ingest, query, crafting, validation, defenses) reads the same
Settings, so the attacker has exact white-box access to the embedding model in
use. That is the threat model this tool measures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass
class Settings:
    workdir: Path = Path("ragpoison-work")
    corpus_dir: Path = DATA_DIR / "corpus"
    target_doc: str = "fin_risk_disclosure"
    topic_prefix: str = "fin_"  # doc-id prefix of the target topic (used by the defenses)

    attacker_queries: Path = DATA_DIR / "queries" / "attacker_cluster.txt"
    canary_queries: Path = DATA_DIR / "queries" / "canary.txt"
    diverse_queries: Path = DATA_DIR / "queries" / "diverse.txt"
    topic_queries: Path = DATA_DIR / "queries" / "topic_finance.txt"
    sentence_bank: Path = DATA_DIR / "attack" / "sentence_bank.txt"
    seed_texts: tuple[Path, ...] = field(default_factory=lambda: (
        DATA_DIR / "attack" / "seed_1.txt", DATA_DIR / "attack" / "seed_2.txt"))

    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunk_size: int = 500
    chunk_overlap: int = 50
    rerank_pool: int = 10
    top_k: int = 3

    collection: str = "lab"          # the collection that gets poisoned
    clean_collection: str = "clean"  # untouched copy, for the rerank baseline

    @property
    def db_path(self) -> str:
        return str(self.workdir / "chroma_db")

    @property
    def poison_dir(self) -> Path:
        return self.workdir / "poison"

    @property
    def results_dir(self) -> Path:
        return self.workdir / "results"
