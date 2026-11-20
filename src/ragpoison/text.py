"""Plain-text helpers. No ML dependencies, so these are cheap to test."""
from __future__ import annotations

from pathlib import Path


def chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    """Character-based chunking with overlap, after collapsing whitespace."""
    if overlap >= size:
        raise ValueError("overlap must be smaller than size")
    text = " ".join(text.split())
    if len(text) <= size:
        return [text]
    step = size - overlap
    chunks = []
    for start in range(0, len(text), step):
        chunks.append(text[start:start + size])
        if start + size >= len(text):
            break
    return chunks


def load_docs(directory: Path) -> dict[str, str]:
    """All *.txt files in a directory, keyed by file stem, sorted by name."""
    return {p.stem: p.read_text().strip() for p in sorted(Path(directory).glob("*.txt"))}


def read_lines(path: Path) -> list[str]:
    """Non-empty lines of a file, skipping '#' comments."""
    lines = (ln.strip() for ln in Path(path).read_text().splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]
