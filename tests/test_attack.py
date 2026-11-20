import random

import numpy as np

from ragpoison.attack import build_vocab, craft, sentence_greedy, word_greedy

# Toy objective: fraction of words that are "risk". No model needed.
def toy_score(texts):
    return np.array([t.split().count("risk") / len(t.split()) for t in texts])


def test_word_greedy_improves_keeps_marker_and_length_cap():
    c = word_greedy("cand", "one two three four five six", "[M]", ["risk", "other"], toy_score,
                    random.Random(0), max_chars=60, iters=40, samples=32)
    assert c.score > c.history[0]
    assert c.history == sorted(c.history)  # greedy never gets worse
    assert c.text.endswith(" [M]") and len(c.text) <= 60


def test_substitution_only_keeps_word_count():
    seed = "a b c d e f g"
    c = word_greedy("cand", seed, "[M]", ["risk"], toy_score, random.Random(0),
                    allow_len_change=False, iters=20, samples=16)
    assert len(c.text.split()) == len(seed.split()) + 1  # +1 for the marker


def test_sentence_greedy_only_uses_whole_bank_sentences():
    bank = ["risk risk.", "nothing here.", "risk is real."]
    c = sentence_greedy("cand", "seed text.", "[M]", bank, toy_score, max_chars=80)
    body = c.text[: -len(" [M]")]
    pieces = {"seed text."} | set(bank)
    rebuilt = body
    for p in sorted(pieces, key=len, reverse=True):
        rebuilt = rebuilt.replace(p, "")
    assert rebuilt.strip() == ""
    assert c.score > c.history[0]


def test_craft_is_deterministic_for_a_seed():
    args = (["seed one two three four five", "another seed six seven eight"], ["risk here."],
            ["risk", "x"], toy_score)
    a = craft("ABC", *args, rng_seed=7, log=lambda _: None)
    b = craft("ABC", *args, rng_seed=7, log=lambda _: None)
    assert [c.text for c in a] == [c.text for c in b]
    assert [c.name for c in a] == ["cand_A_word_greedy", "cand_B_sentence_greedy", "cand_C_word_subst_only"]
    assert all(f"poison-doc-{m}]" in c.text for m, c in zip("ABC", a))


def test_vocab_includes_query_words_lowercased():
    v = build_vocab(["What is an Index fund"], extra=["risk"])
    assert {"what", "index", "fund", "risk"} <= set(v)
