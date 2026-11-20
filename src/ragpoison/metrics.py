"""Scoring a ranking against the canary set, and diffing two runs.

Everything here works on plain ranked lists (dicts with doc_id, rank, score,
poison), so it is independent of how the ranking was produced: live store,
reranked store or offline simulation.
"""
from __future__ import annotations

import json
from typing import Callable, Iterable

Ranking = list[dict]  # [{"doc_id", "rank", "score", "poison"}], best first


def pct(flags: Iterable[bool]) -> float:
    flags = list(flags)
    return round(100 * sum(map(bool, flags)) / len(flags), 1) if flags else 0.0


def score_canaries(queries: list[str], rank_fn: Callable[[str], Ranking], target: str,
                   top_k: int = 3, mode: str = "") -> dict:
    rows = []
    for q in queries:
        docs = rank_fn(q)
        tgt = next((d for d in docs if d["doc_id"] == target), None)
        poison = next((d for d in docs if d["poison"]), None)
        rows.append({
            "query": q,
            "target_in_top3": bool(tgt and tgt["rank"] <= top_k),
            "target_rank": tgt["rank"] if tgt else None,  # None = not even in the rerank pool
            "target_score": tgt["score"] if tgt else None,
            "top3": [{"doc_id": d["doc_id"], "score": d["score"], "poison": d["poison"]} for d in docs[:top_k]],
            "best_poison": ({"doc_id": poison["doc_id"], "rank": poison["rank"], "score": poison["score"]}
                            if poison else None),
        })
    summary = {
        "n_queries": len(rows),
        "mode": mode,
        "target_in_top3_pct": pct(r["target_in_top3"] for r in rows),
        "target_rank1_pct": pct(r["target_rank"] == 1 for r in rows),
        "poison_in_top3_pct": pct(r["best_poison"] and r["best_poison"]["rank"] <= top_k for r in rows),
        "poison_rank1_pct": pct(r["best_poison"] and r["best_poison"]["rank"] == 1 for r in rows),
    }
    return {"summary": summary, "results": rows}


def diff(before: dict, after: dict, top_k: int = 3) -> dict:
    """Per-query comparison of two score_canaries() outputs over the same queries."""
    rows = []
    for b, a in zip(before["results"], after["results"]):
        if b["query"] != a["query"]:
            raise ValueError(f"query mismatch: {b['query']!r} vs {a['query']!r}")
        ap = a["best_poison"]
        rows.append({
            "query": b["query"],
            "target_rank": [b["target_rank"], a["target_rank"]],
            "target_score": [b["target_score"], a["target_score"]],
            "target_in_top3": [b["target_in_top3"], a["target_in_top3"]],
            "dropped_out_of_top3": b["target_in_top3"] and not a["target_in_top3"],
            "poison_rank_after": ap["rank"] if ap else None,
            "poison_doc_after": ap["doc_id"] if ap else None,
            "poison_in_top3_after": bool(ap and ap["rank"] <= top_k),
            "top3_after": [d["doc_id"] for d in a["top3"]],
        })
    n = len(rows)
    was_in = sum(r["target_in_top3"][0] for r in rows)
    dropped = sum(r["dropped_out_of_top3"] for r in rows)
    missing = 11  # rank used for "not in the pool" when averaging rank change (pool=10)
    summary = {
        "n_queries": n,
        "target_in_top3_before_pct": pct(r["target_in_top3"][0] for r in rows),
        "target_in_top3_after_pct": pct(r["target_in_top3"][1] for r in rows),
        "target_dropped_out_of_top3_pct": pct(r["dropped_out_of_top3"] for r in rows),
        "target_dropped_out_of_top3_of_previously_in": f"{dropped}/{was_in}",
        "poison_in_top3_pct": pct(r["poison_in_top3_after"] for r in rows),
        "poison_rank1_pct": pct(r["poison_rank_after"] == 1 for r in rows),
        "mean_target_rank_change": round(sum((r["target_rank"][1] or missing) - (r["target_rank"][0] or missing)
                                             for r in rows) / n, 2) if n else 0.0,
    }
    return {"summary": summary, "rows": rows}


def diff_markdown(d: dict, label: str) -> str:
    def fmt(x):
        return "-" if x is None else str(x)

    yn = {True: "Y", False: "N"}
    lines = [f"### Canary diff: {label}", "",
             "| query | target rank | target score | target top-3 | poison rank after | top-3 after |",
             "|---|---|---|---|---|---|"]
    for r in d["rows"]:
        lines.append(f"| {r['query']} | {fmt(r['target_rank'][0])} → {fmt(r['target_rank'][1])} | "
                     f"{fmt(r['target_score'][0])} → {fmt(r['target_score'][1])} | "
                     f"{yn[r['target_in_top3'][0]]} → {yn[r['target_in_top3'][1]]} | "
                     f"{fmt(r['poison_rank_after'])} | {', '.join(r['top3_after'])} |")
    lines += ["", "```json", json.dumps(d["summary"], indent=2), "```", ""]
    return "\n".join(lines)
