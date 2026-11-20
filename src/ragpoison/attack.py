"""White-box poison crafting (offline; never touches the store).

Objective: maximise the mean cosine similarity between a candidate document and
a cluster of attacker-written queries about the target topic. With unit
vectors, mean_i(doc . q_i) == doc . mean_i(q_i), so the attack amounts to
"park a document on the topic centroid". A document sitting there ranks highly
for most queries in that topic, including ones the attacker never saw.

The payload is a fixed marker string that the optimiser never edits. Each
candidate is capped at `max_chars` so it ingests as a single chunk, which means
the vector being optimised is exactly the vector stored in the database.

The search functions take a `score_fn(texts) -> scores` so they can be tested
with a toy scorer, without any model.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

ScoreFn = Callable[[list[str]], np.ndarray]

DEFAULT_MARKER = "[LAB-MARKER poison-doc-{tag}]"

# Topic terms added to the attacker vocabulary on top of the words in the query cluster.
EXTRA_TERMS = ("invest investing investment investments risk risks risky money savings stocks bonds "
               "funds portfolio mortgage retirement returns loss losses market finance financial "
               "beginners advice guide safe").split()


@dataclass
class Candidate:
    name: str
    text: str
    score: float
    history: list[float] = field(default_factory=list)


def centroid_scorer(cluster_vecs: np.ndarray, embed_fn: Callable[[list[str]], np.ndarray]) -> ScoreFn:
    """Score = mean cosine to the cluster = doc . centroid (for unit-norm doc vectors)."""
    centroid = cluster_vecs.mean(axis=0)
    return lambda texts: embed_fn(texts) @ centroid


def build_vocab(queries: list[str], extra: tuple[str, ...] | list[str] = EXTRA_TERMS) -> list[str]:
    words = {w.lower() for q in queries for w in re.findall(r"[A-Za-z0-9']+", q)}
    return sorted(words | set(extra))


def word_greedy(name: str, seed: str, marker: str, vocab: list[str], score_fn: ScoreFn,
                rng: random.Random, *, allow_len_change: bool = True, max_chars: int = 480,
                iters: int = 150, samples: int = 256, patience: int = 12) -> Candidate:
    """Random-sample word edits each round, keep the best if it improves.

    allow_len_change=True mixes substitute (60%) / insert (25%) / delete (15%);
    False only substitutes, so the sentence skeleton keeps its length.
    """
    def compose(ws: list[str]) -> str:
        return " ".join(ws) + " " + marker

    words = seed.split()
    best = float(score_fn([compose(words)])[0])
    history = [round(best, 4)]
    stale = 0
    for _ in range(iters):
        cands = []
        for _ in range(samples):
            w = list(words)
            op = rng.random() if allow_len_change else 0.0
            i = rng.randrange(len(w))
            if op < 0.6:
                w[i] = rng.choice(vocab)
            elif op < 0.85:
                w.insert(i, rng.choice(vocab))
            elif len(w) > 5:
                del w[i]
            if len(compose(w)) <= max_chars:
                cands.append(w)
        if cands:
            s = score_fn([compose(w) for w in cands])
            j = int(np.argmax(s))
            if s[j] > best + 1e-5:
                best, words, stale = float(s[j]), cands[j], 0
            else:
                stale += 1
        else:
            stale += 1
        history.append(round(best, 4))
        if stale >= patience:
            break
    return Candidate(name, compose(words), best, history)


def sentence_greedy(name: str, seed: str, marker: str, bank: list[str], score_fn: ScoreFn, *,
                    max_chars: int = 480, iters: int = 30) -> Candidate:
    """Greedy insert/replace/delete over whole human-written sentences.

    Weaker than word-level search, but the output stays fluent, which makes it
    the realistic threat: a human skimming it sees an ordinary FAQ blurb.
    """
    def compose(ss: list[str]) -> str:
        return " ".join(ss) + " " + marker

    sents = [seed]
    best = float(score_fn([compose(sents)])[0])
    history = [round(best, 4)]
    for _ in range(iters):
        cands = []
        for b in bank:
            if b in sents:
                continue
            for pos in range(len(sents) + 1):
                cands.append(sents[:pos] + [b] + sents[pos:])
            for pos in range(len(sents)):
                cands.append(sents[:pos] + [b] + sents[pos + 1:])
        if len(sents) > 1:
            cands += [sents[:pos] + sents[pos + 1:] for pos in range(len(sents))]
        cands = [c for c in cands if len(compose(c)) <= max_chars]
        if not cands:
            break
        s = score_fn([compose(c) for c in cands])
        j = int(np.argmax(s))
        if s[j] <= best + 1e-5:
            break
        best, sents = float(s[j]), cands[j]
        history.append(round(best, 4))
    return Candidate(name, compose(sents), best, history)


# Candidate recipes. Order matters for reproducibility: A and C share one RNG stream.
METHODS = {
    "A": "cand_A_word_greedy",      # word insert/replace/delete, seed 1
    "B": "cand_B_sentence_greedy",  # sentence-level, readable, seed 1
    "C": "cand_C_word_subst_only",  # word substitution only, seed 2
}


def craft(methods: str, seeds: list[str], bank: list[str], vocab: list[str], score_fn: ScoreFn, *,
          marker: str = DEFAULT_MARKER, rng_seed: int = 1234, max_chars: int = 480,
          log: Callable[[str], None] = print) -> list[Candidate]:
    rng = random.Random(rng_seed)
    seed_a = seeds[0]
    seed_c = seeds[1] if len(seeds) > 1 else seeds[0]
    out = []
    for m in methods.upper():
        if m not in METHODS:
            raise ValueError(f"unknown method {m!r}; choose from {', '.join(METHODS)}")
        name, mk = METHODS[m], marker.format(tag=m)
        if m == "A":
            c = word_greedy(name, seed_a, mk, vocab, score_fn, rng, allow_len_change=True, max_chars=max_chars)
        elif m == "B":
            c = sentence_greedy(name, seed_a, mk, bank, score_fn, max_chars=max_chars)
        else:
            c = word_greedy(name, seed_c, mk, vocab, score_fn, rng, allow_len_change=False, max_chars=max_chars)
        log(f"{name}: mean cos {c.history[0]:.4f} -> {c.score:.4f} in {len(c.history) - 1} iters "
            f"({len(c.text)} chars)\n  {c.text}")
        out.append(c)
    return out
