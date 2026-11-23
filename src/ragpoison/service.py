"""The tool's operations as plain functions, shared by the CLI and the HTTP server.

Each function takes Settings, returns JSON-serialisable data and writes its
artefacts under <workdir>. Progress goes through a `log` callable so the CLI can
print it and the server can stream it to the UI.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from .config import Settings
from .targets import LocalTarget, Target

Log = Callable[[str], None]
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _nolog(_: str) -> None:
    pass


def embed_fn(s: Settings):
    from .models import embed
    return lambda texts: embed(texts, s.embed_model, batch_size=128)


def chunk_fn(s: Settings):
    from .text import chunk_text
    return lambda text: chunk_text(text, s.chunk_size, s.chunk_overlap)


def check_name(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise ValueError(f"invalid name {name!r}: use letters, digits, '_', '-', '.'")
    return name


# --------------------------------------------------------------------------- results


def save_result(s: Settings, name: str, obj) -> Path:
    s.results_dir.mkdir(parents=True, exist_ok=True)
    path = s.results_dir / check_name(name)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=2))
    return path


def load_result(s: Settings, name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = s.results_dir / check_name(name_or_path)
    return json.loads(p.read_text())


def list_results(s: Settings) -> list[str]:
    return sorted(p.name for p in s.results_dir.glob("*")) if s.results_dir.exists() else []


# --------------------------------------------------------------------------- corpus & poison


def corpus(s: Settings) -> dict[str, str]:
    from .text import load_docs
    return load_docs(s.corpus_dir)


def poison_docs(s: Settings, files: list[str] | None = None) -> dict[str, str]:
    from .text import load_docs
    if files:
        return {Path(f).stem: Path(f).read_text().strip() for f in files}
    return load_docs(s.poison_dir)


def save_poison(s: Settings, name: str, text: str) -> None:
    s.poison_dir.mkdir(parents=True, exist_ok=True)
    (s.poison_dir / f"{check_name(name)}.txt").write_text(text.strip() + "\n")


def delete_poison(s: Settings, name: str) -> None:
    (s.poison_dir / f"{check_name(name)}.txt").unlink(missing_ok=True)


# --------------------------------------------------------------------------- store


def build(s: Settings, collection: str | None = None) -> dict:
    from .store import ingest, open_collection
    name = collection or s.collection
    coll = open_collection(s, name, reset=True)
    n = ingest(s, coll, corpus(s), poison=False, source="corpus")
    return {"collection": name, "chunks": n, "corpus": str(s.corpus_dir)}


# --------------------------------------------------------------------------- attack


def craft(s: Settings, methods: str = "ABC", marker: str = "[LAB-MARKER poison-doc-{tag}]", seed: int = 1234,
          max_chars: int = 480, log: Log = print) -> dict:
    from .attack import build_vocab, centroid_scorer, craft as run_craft
    from .text import read_lines
    e = embed_fn(s)
    cluster = read_lines(s.attacker_queries)
    log(f"attacker cluster: {len(cluster)} queries from {s.attacker_queries.name}")
    seeds = [Path(p).read_text().strip() for p in s.seed_texts]
    cands = run_craft(methods, seeds, read_lines(s.sentence_bank), build_vocab(cluster),
                      centroid_scorer(e(cluster), e), marker=marker, rng_seed=seed, max_chars=max_chars, log=log)
    out = {}
    for c in cands:
        save_poison(s, c.name, c.text)
        out[c.name] = {"mean_cos_to_cluster": round(c.score, 4), "iterations": len(c.history) - 1,
                       "trajectory": c.history, "chars": len(c.text), "text": c.text}
    save_result(s, "craft_log.json", out)
    log(f"wrote {len(cands)} candidate(s) to {s.poison_dir}")
    return out


def validate(s: Settings, candidates: dict[str, str], log: Log = print) -> dict:
    """Offline simulation: rank each candidate (and all together) against the canaries, no DB writes."""
    from .metrics import score_canaries
    from .simulate import Index
    from .text import read_lines
    e, ch = embed_fn(s), chunk_fn(s)
    docs = corpus(s)
    base = Index.build(docs, e, ch)
    canaries = read_lines(s.canary_queries)
    qvecs = dict(zip(canaries, e(canaries)))
    centroid = e(read_lines(s.attacker_queries)).mean(axis=0)
    target_vecs = e(ch(docs[s.target_doc]))

    def run(index: Index) -> dict:
        return score_canaries(canaries, lambda q: index.rank(qvecs[q]), s.target_doc, s.top_k, "offline simulation")

    reference = {d: round(float((e(ch(docs[d])) @ centroid).max()), 4) for d in docs if d.startswith(s.topic_prefix)}
    log("centrality of trusted topic docs (mean cos to attacker cluster):")
    for d, v in reference.items():
        log(f"  {d:<32} {v:.4f}")
    out = {"baseline": run(base)["summary"], "trusted_topic_centrality": reference, "candidates": {}}
    sets = {n: {n: t} for n, t in candidates.items()}
    if len(candidates) > 1:
        sets["ALL_CANDIDATES"] = dict(candidates)
    for name, extra in sets.items():
        res = run(base.plus(Index.build(extra, e, ch, poison=True)))
        info = dict(res["summary"])
        if name in candidates:
            v = e(ch(extra[name]))
            info["mean_cos_to_cluster"] = round(float((v @ centroid).max()), 4)
            info["cos_to_target"] = round(float((v @ target_vecs.T).max()), 4)
        info["passes"] = info["poison_in_top3_pct"] >= 50
        info["per_query"] = [{"query": r["query"], "target_rank": r["target_rank"],
                              "poison_rank": r["best_poison"]["rank"] if r["best_poison"] else None}
                             for r in res["results"]]
        out["candidates"][name] = info
        log(f"{name:<26} poison top-3 {info['poison_in_top3_pct']}%  rank-1 {info['poison_rank1_pct']}%  "
            f"target top-3 {info['target_in_top3_pct']}%  {'PASS' if info['passes'] else 'FAIL'}")
    return out


# --------------------------------------------------------------------------- measurement


def canary(s: Settings, target: Target, rerank: bool = False, log: Log = _nolog) -> dict:
    from .metrics import score_canaries
    from .text import read_lines
    if target.kind == "local":
        mode = f"vector + cross-encoder rerank (pool={s.rerank_pool})" if rerank else "vector only"
        mode += f" [{target.collection}]"
    else:
        mode = f"remote {target.base_url}" + (" + client-side rerank" if rerank else "")
    queries = read_lines(s.canary_queries)

    def rank(q: str):
        r = target.retrieve(q, use_rerank=rerank)
        log(f"  {q[:60]}")
        return r
    return score_canaries(queries, rank, s.target_doc, s.top_k, mode)


def compare(s: Settings, before: dict, after: dict, label: str) -> dict:
    from .metrics import diff, diff_markdown
    d = diff(before, after, s.top_k)
    save_result(s, f"compare_{check_name(label)}.json", d)
    save_result(s, f"compare_{label}.md", diff_markdown(d, label))
    return d


def defend(s: Settings, which: str, incoming: dict[str, str], z: float = 2.0) -> dict:
    from .defenses import global_outlier, topic_centrality
    from .text import read_lines
    e, ch = embed_fn(s), chunk_fn(s)
    trusted = corpus(s)
    if which == "outlier":
        return global_outlier(trusted, incoming, e(read_lines(s.diverse_queries)), e, ch, z, s.topic_prefix)
    if which == "topic":
        topic = {d: t for d, t in trusted.items() if d.startswith(s.topic_prefix)}
        return topic_centrality(topic, incoming, e(read_lines(s.topic_queries)), e, ch, z)
    raise ValueError(f"unknown defense {which!r}")


# --------------------------------------------------------------------------- pipeline


def run_all(s: Settings, methods: str = "ABC", marker: str = "[LAB-MARKER poison-doc-{tag}]", seed: int = 1234,
            max_chars: int = 480, z: float = 2.0, log: Log = print) -> dict:
    """Every stage end to end on the local store, from a freshly rebuilt collection."""
    lab, clean = LocalTarget(s, s.collection), LocalTarget(s, s.clean_collection)
    log("## 1/6 build victim store + baseline canary")
    log(str(build(s, s.collection)))
    base = canary(s, lab)
    save_result(s, "baseline_canary.json", base)

    log("## 2/6 craft poison (offline, white-box)")
    craft(s, methods, marker, seed, max_chars, log)

    log("## 3/6 validate offline (no DB writes)")
    save_result(s, "validation.json", validate(s, poison_docs(s), log))

    log("## 4/6 inject into the live store + canary")
    log(str(lab.inject(poison_docs(s))))
    post = canary(s, lab)
    save_result(s, "post_poison_canary.json", post)
    vec = compare(s, base, post, "vector_only")

    log("## 5/6 defense: cross-encoder rerank")
    build(s, s.clean_collection)
    base_rr, post_rr = canary(s, clean, rerank=True), canary(s, lab, rerank=True)
    save_result(s, "baseline_canary_rerank.json", base_rr)
    save_result(s, "post_poison_canary_rerank.json", post_rr)
    rr = compare(s, base_rr, post_rr, "rerank")
    rr_vs_vec = compare(s, base, post_rr, "vector_baseline_vs_rerank_poisoned")

    log("## 6/6 defenses: global outlier + in-topic centrality")
    poison = poison_docs(s)
    outl, topic = defend(s, "outlier", poison, z), defend(s, "topic", poison, z)
    save_result(s, "defense_outlier.json", outl)
    save_result(s, "defense_topic.json", topic)

    md = summary_markdown(s, base, post, post_rr, vec, rr, rr_vs_vec, outl, topic)
    save_result(s, "summary.md", md)
    log(md)
    return {"baseline": base["summary"], "poisoned": post["summary"], "poisoned_rerank": post_rr["summary"],
            "vector_only": vec["summary"], "rerank": rr["summary"], "rerank_vs_vector": rr_vs_vec["summary"],
            "outlier": outl["summary"], "topic": topic["summary"], "markdown": md}


def summary_markdown(s, base, post, post_rr, vec, rr, rr_vs_vec, outl, topic) -> str:
    b, p, r = base["summary"], post["summary"], post_rr["summary"]
    o, t = outl["summary"], topic["summary"]
    k = s.top_k
    return "\n".join([
        f"# ragpoison summary ({b['n_queries']} canary queries, target `{s.target_doc}`)", "",
        f"Embedding model `{s.embed_model}`, reranker `{s.rerank_model}`.", "",
        "| metric | baseline | poisoned, vector only | poisoned, + rerank |",
        "|---|---|---|---|",
        f"| target in top-{k} | {b['target_in_top3_pct']}% | {p['target_in_top3_pct']}% | {r['target_in_top3_pct']}% |",
        f"| target at rank 1 | {b['target_rank1_pct']}% | {p['target_rank1_pct']}% | {r['target_rank1_pct']}% |",
        f"| target dropped out of top-{k} | - | {vec['summary']['target_dropped_out_of_top3_pct']}% | "
        f"{rr_vs_vec['summary']['target_dropped_out_of_top3_pct']}% vs vector baseline, "
        f"{rr['summary']['target_dropped_out_of_top3_pct']}% vs rerank baseline |",
        f"| poison in top-{k} | {b['poison_in_top3_pct']}% | {p['poison_in_top3_pct']}% | {r['poison_in_top3_pct']}% |",
        f"| poison at rank 1 | {b['poison_rank1_pct']}% | {p['poison_rank1_pct']}% | {r['poison_rank1_pct']}% |",
        "",
        f"Global outlier check (z > {o['z_threshold']}): poison flagged {o['incoming_flagged']}, "
        f"false positives {o['false_positives'] or 'none'}.",
        f"In-topic centrality check (z > {t['z_threshold']}): poison flagged {t['incoming_flagged']}, "
        f"false positives {t['false_positives'] or 'none'}.", ""])
