"""Detection-side defenses that look at document embeddings, not at queries.

global_outlier
    The naive check: a document unusually similar to queries from *every*
    topic is suspicious. z-score of its mean similarity to a diverse query set,
    against the whole trusted corpus. Expected to miss a topic-targeted poison,
    which only needs to be central within one topic.

topic_centrality
    The check the reference report recommends instead: z-score of a new
    document's mean similarity to *its own topic's* queries, against the
    trusted documents of that topic. Trusted docs are scored leave-one-out to
    estimate the false-positive rate.

Both take the incoming (untrusted) documents separately from the trusted
corpus, which mirrors how a write-gate on the store would use them.
"""
from __future__ import annotations

import numpy as np

from .simulate import ChunkFn, EmbedFn, doc_centrality


def _z(x: float, ref: list[float]) -> float:
    sd = float(np.std(ref, ddof=1)) if len(ref) > 1 else 0.0  # sample SD: topics are small
    return round((x - float(np.mean(ref))) / sd, 2) if sd > 0 else 0.0


def global_outlier(trusted: dict[str, str], incoming: dict[str, str], query_vecs: np.ndarray,
                   embed_fn: EmbedFn, chunk_fn: ChunkFn, z_threshold: float = 2.0,
                   topic_prefix: str | None = None) -> dict:
    docs = {**trusted, **incoming}
    mean_sim = {d: float(doc_centrality(t, query_vecs, embed_fn, chunk_fn).mean()) for d, t in docs.items()}
    ref = [mean_sim[d] for d in trusted]
    topic_ref = [mean_sim[d] for d in trusted if topic_prefix and d.startswith(topic_prefix)]
    rows = {}
    for d in sorted(docs, key=mean_sim.get, reverse=True):
        z = _z(mean_sim[d], ref)
        rows[d] = {"mean_sim": round(mean_sim[d], 4), "z_vs_corpus": z,
                   "z_vs_topic_docs": _z(mean_sim[d], topic_ref) if len(topic_ref) > 1 else None,
                   "flagged": z > z_threshold, "incoming": d in incoming}
    return _summarise("global_outlier", rows, z_threshold, ref)


def topic_centrality(trusted_topic: dict[str, str], incoming: dict[str, str], topic_query_vecs: np.ndarray,
                     embed_fn: EmbedFn, chunk_fn: ChunkFn, z_threshold: float = 2.0) -> dict:
    if len(trusted_topic) < 3:
        raise ValueError("need at least 3 trusted documents in the topic to estimate a spread")
    mean_sim = {d: float(doc_centrality(t, topic_query_vecs, embed_fn, chunk_fn).mean())
                for d, t in {**trusted_topic, **incoming}.items()}
    ref = [mean_sim[d] for d in trusted_topic]
    rows = {}
    for d in sorted(mean_sim, key=mean_sim.get, reverse=True):
        if d in trusted_topic:  # leave-one-out, so a trusted doc is not compared against itself
            z = _z(mean_sim[d], [mean_sim[o] for o in trusted_topic if o != d])
        else:
            z = _z(mean_sim[d], ref)
        rows[d] = {"mean_sim": round(mean_sim[d], 4), "z_vs_topic_docs": z,
                   "flagged": z > z_threshold, "incoming": d in incoming}
    return _summarise("topic_centrality", rows, z_threshold, ref)


def _summarise(name: str, rows: dict, z_threshold: float, ref: list[float]) -> dict:
    incoming = [d for d, r in rows.items() if r["incoming"]]
    caught = [d for d in incoming if rows[d]["flagged"]]
    false_pos = [d for d, r in rows.items() if r["flagged"] and not r["incoming"]]
    return {"summary": {"defense": name, "z_threshold": z_threshold,
                        "reference_mean": round(float(np.mean(ref)), 4), "reference_sd": round(float(np.std(ref, ddof=1)), 4),
                        "incoming_flagged": f"{len(caught)}/{len(incoming)}", "flagged_incoming": caught,
                        "false_positives": false_pos},
            "docs": rows}


def table(result: dict) -> str:
    rows = result["docs"]
    zkey = "z_vs_corpus" if result["summary"]["defense"] == "global_outlier" else "z_vs_topic_docs"
    out = [f"{'doc':<34}{'mean_sim':>9}{'z':>8}  flag"]
    for d, r in rows.items():
        out.append(f"{d:<34}{r['mean_sim']:>9.4f}{r[zkey]:>8.2f}  "
                   f"{'FLAG' if r['flagged'] else '    '}{'  (incoming)' if r['incoming'] else ''}")
    return "\n".join(out)
