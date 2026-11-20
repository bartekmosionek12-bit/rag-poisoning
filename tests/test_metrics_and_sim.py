import numpy as np
import pytest

from ragpoison.defenses import global_outlier, topic_centrality
from ragpoison.metrics import diff, score_canaries
from ragpoison.simulate import Index


def unit(*v):
    v = np.array(v, dtype=float)
    return v / np.linalg.norm(v)


# Toy embedding: each doc text is a key into a fixed table of 2-D vectors.
VECS = {"target": unit(1, 0), "other": unit(0, 1), "poison": unit(1, 0.2), "q": unit(1, 0.05)}
embed = lambda texts: np.stack([VECS[t] for t in texts])
chunk = lambda text: [text]


def ranking(*doc_ids, poison=()):
    return [{"doc_id": d, "rank": i, "score": 1.0 - i / 10, "poison": d in poison} for i, d in enumerate(doc_ids, 1)]


def test_score_canaries_counts_target_and_poison():
    rankings = {"q1": ranking("t", "a", "b", "p", poison={"p"}), "q2": ranking("p", "a", "b", "t", poison={"p"})}
    s = score_canaries(["q1", "q2"], rankings.get, "t")["summary"]
    assert s["target_in_top3_pct"] == 50.0 and s["target_rank1_pct"] == 50.0
    assert s["poison_in_top3_pct"] == 50.0 and s["poison_rank1_pct"] == 50.0


def test_diff_reports_suppression():
    before = score_canaries(["q"], lambda q: ranking("t", "a", "b"), "t")
    after = score_canaries(["q"], lambda q: ranking("p", "a", "b", "t", poison={"p"}), "t")
    s = diff(before, after)["summary"]
    assert s["target_dropped_out_of_top3_pct"] == 100.0
    assert s["target_dropped_out_of_top3_of_previously_in"] == "1/1"
    assert s["mean_target_rank_change"] == 3.0


def test_diff_rejects_mismatched_queries():
    a = score_canaries(["x"], lambda q: ranking("t"), "t")
    b = score_canaries(["y"], lambda q: ranking("t"), "t")
    with pytest.raises(ValueError):
        diff(a, b)


def test_index_rank_and_poison_flag():
    base = Index.build({"target": "target", "other": "other"}, embed, chunk)
    full = base.plus(Index.build({"poison": "poison"}, embed, chunk, poison=True))
    r = full.rank(VECS["q"])
    assert [d["doc_id"] for d in r] == ["target", "poison", "other"]
    assert [d["poison"] for d in r] == [False, True, False]


def test_topic_centrality_flags_a_doc_far_more_central_than_trusted_ones():
    # trusted docs have similarity 0.60-0.64 to the topic query; the incoming one has 1.0
    sims = [0.60, 0.62, 0.61, 0.63, 0.64]
    e = {f"t{i}": np.array([c, np.sqrt(1 - c * c)]) for i, c in enumerate(sims)} | {"inc": unit(1, 0)}
    emb = lambda texts: np.stack([e[t] for t in texts])
    trusted = {f"t{i}": f"t{i}" for i in range(5)}
    res = topic_centrality(trusted, {"inc": "inc"}, unit(1, 0)[None, :], emb, chunk, z_threshold=2.0)
    assert res["summary"]["incoming_flagged"] == "1/1"
    assert res["summary"]["false_positives"] == []


def test_global_outlier_marks_incoming():
    res = global_outlier({"target": "target", "other": "other"}, {"poison": "poison"},
                         VECS["q"][None, :], embed, chunk, z_threshold=0.5)
    assert res["docs"]["poison"]["incoming"] and not res["docs"]["target"]["incoming"]
