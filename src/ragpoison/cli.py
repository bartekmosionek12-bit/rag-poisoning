"""Command-line interface. Run `ragpoison -h` or `ragpoison <command> -h`."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings

# --------------------------------------------------------------------------- helpers


def _embed_fn(s: Settings):
    from .models import embed
    return lambda texts: embed(texts, s.embed_model, batch_size=128)


def _chunk_fn(s: Settings):
    from .text import chunk_text
    return lambda text: chunk_text(text, s.chunk_size, s.chunk_overlap)


def _save(s: Settings, name: str, obj) -> Path:
    s.results_dir.mkdir(parents=True, exist_ok=True)
    path = s.results_dir / name
    if isinstance(obj, str):
        path.write_text(obj)
    else:
        path.write_text(json.dumps(obj, indent=2))
    return path


def _load_json(s: Settings, name_or_path: str) -> dict:
    p = Path(name_or_path)
    if not p.exists():
        p = s.results_dir / name_or_path
    return json.loads(p.read_text())


def _poison_docs(s: Settings, files: list[str] | None = None) -> dict[str, str]:
    from .text import load_docs
    if files:
        return {Path(f).stem: Path(f).read_text().strip() for f in files}
    return load_docs(s.poison_dir)


# --------------------------------------------------------------------------- commands


def cmd_build(s: Settings, a) -> None:
    from .store import ingest, open_collection
    from .text import load_docs
    name = a.collection or s.collection
    coll = open_collection(s, name, reset=True)
    n = ingest(s, coll, load_docs(s.corpus_dir), poison=False, source="corpus")
    print(f"built collection '{name}' from {s.corpus_dir}: {n} chunks")


def cmd_inject(s: Settings, a) -> None:
    from .store import ingest, open_collection
    name = a.collection or s.collection
    coll = open_collection(s, name)
    if coll.count() == 0:
        sys.exit(f"collection '{name}' is empty; run `ragpoison build` first")
    for doc_id, text in _poison_docs(s, a.files).items():
        n = ingest(s, coll, {doc_id: text}, poison=True, source=doc_id)
        print(f"injected {doc_id}: {n} chunk(s)")
    print(f"collection '{name}' now holds {coll.count()} chunks")


def cmd_query(s: Settings, a) -> None:
    from .store import open_collection, retrieve
    coll = open_collection(s, a.collection or s.collection)
    for h in retrieve(s, coll, a.query, k=a.n, use_rerank=a.rerank):
        flag = "  <-- POISON" if h["poison"] else ""
        print(f"{h['rank']}. {h['doc_id']:<32} score={h['score']:.4f}{flag}\n   {h['text'][:110]}...")


def cmd_craft(s: Settings, a) -> None:
    from .attack import build_vocab, centroid_scorer, craft
    from .text import read_lines
    embed_fn = _embed_fn(s)
    cluster = read_lines(s.attacker_queries)
    score_fn = centroid_scorer(embed_fn(cluster), embed_fn)
    seeds = [Path(p).read_text().strip() for p in s.seed_texts]
    print(f"attacker cluster: {len(cluster)} queries from {s.attacker_queries}")
    cands = craft(a.methods, seeds, read_lines(s.sentence_bank), build_vocab(cluster), score_fn,
                  marker=a.marker, rng_seed=a.seed, max_chars=a.max_chars)
    s.poison_dir.mkdir(parents=True, exist_ok=True)
    for c in cands:
        (s.poison_dir / f"{c.name}.txt").write_text(c.text + "\n")
    _save(s, "craft_log.json", {c.name: {"mean_cos_to_cluster": round(c.score, 4), "iterations": len(c.history) - 1,
                                         "trajectory": c.history, "chars": len(c.text), "text": c.text}
                                for c in cands})
    print(f"wrote {len(cands)} candidate(s) to {s.poison_dir}")


def validate(s: Settings, candidates: dict[str, str]) -> dict:
    from .metrics import score_canaries
    from .simulate import Index
    from .text import load_docs, read_lines
    embed_fn, chunk_fn = _embed_fn(s), _chunk_fn(s)
    corpus = load_docs(s.corpus_dir)
    base = Index.build(corpus, embed_fn, chunk_fn)
    canaries = read_lines(s.canary_queries)
    qvecs = dict(zip(canaries, embed_fn(canaries)))
    centroid = embed_fn(read_lines(s.attacker_queries)).mean(axis=0)
    target_vecs = embed_fn(chunk_fn(corpus[s.target_doc]))

    def run(index: Index) -> dict:
        return score_canaries(canaries, lambda q: index.rank(qvecs[q]), s.target_doc, s.top_k, "offline simulation")

    print("centrality of trusted topic docs (mean cos to attacker cluster):")
    for d in (k for k in corpus if k.startswith(s.topic_prefix)):
        print(f"  {d:<32} {float((embed_fn(chunk_fn(corpus[d])) @ centroid).max()):.4f}")
    out = {"baseline": run(base)["summary"], "candidates": {}}
    sets = {n: {n: t} for n, t in candidates.items()}
    if len(candidates) > 1:
        sets["ALL_CANDIDATES"] = dict(candidates)
    for name, extra in sets.items():
        res = run(base.plus(Index.build(extra, embed_fn, chunk_fn, poison=True)))
        info = dict(res["summary"])
        if name in candidates:
            e = embed_fn(chunk_fn(extra[name]))
            info["mean_cos_to_cluster"] = round(float((e @ centroid).max()), 4)
            info["cos_to_target"] = round(float((e @ target_vecs.T).max()), 4)
        info["passes"] = info["poison_in_top3_pct"] >= 50
        out["candidates"][name] = info
    print(f"\n{'set':<26}{'cos→cluster':>12}{'cos→target':>11}{'poison top3':>12}{'poison #1':>10}"
          f"{'target top3':>12}  pass")
    b = out["baseline"]
    print(f"{'baseline (no poison)':<26}{'':>12}{'':>11}{'-':>12}{'-':>10}{b['target_in_top3_pct']:>11}%")
    for name, i in out["candidates"].items():
        print(f"{name:<26}{i.get('mean_cos_to_cluster', ''):>12}{i.get('cos_to_target', ''):>11}"
              f"{i['poison_in_top3_pct']:>11}%{i['poison_rank1_pct']:>9}%{i['target_in_top3_pct']:>11}%  "
              f"{'PASS' if i['passes'] else 'FAIL'}")
    return out


def cmd_validate(s: Settings, a) -> None:
    cands = _poison_docs(s, a.files)
    if not cands:
        sys.exit(f"no candidates in {s.poison_dir}; run `ragpoison craft` first")
    _save(s, "validation.json", validate(s, cands))


def canary(s: Settings, collection: str, rerank: bool) -> dict:
    from .metrics import score_canaries
    from .store import open_collection, retrieve
    from .text import read_lines
    coll = open_collection(s, collection)
    mode = f"vector + cross-encoder rerank (pool={s.rerank_pool})" if rerank else "vector only"
    return score_canaries(read_lines(s.canary_queries), lambda q: retrieve(s, coll, q, use_rerank=rerank),
                          s.target_doc, s.top_k, f"{mode} [{collection}]")


def _print_canary(res: dict) -> None:
    for r in res["results"]:
        p = r["best_poison"]
        print(f"{r['query'][:52]:<52} target rank={str(r['target_rank']):>4}  "
              f"top3={'Y' if r['target_in_top3'] else 'N'}  {'poison@' + str(p['rank']) if p else '-'}")
    print(json.dumps(res["summary"], indent=2))


def cmd_canary(s: Settings, a) -> None:
    res = canary(s, a.collection or s.collection, a.rerank)
    _print_canary(res)
    print(f"saved {_save(s, a.out, res)}")


def compare(s: Settings, before: dict, after: dict, label: str) -> dict:
    from .metrics import diff, diff_markdown
    d = diff(before, after, s.top_k)
    _save(s, f"compare_{label}.json", d)
    _save(s, f"compare_{label}.md", diff_markdown(d, label))
    return d


def cmd_compare(s: Settings, a) -> None:
    from .metrics import diff_markdown
    print(diff_markdown(compare(s, _load_json(s, a.before), _load_json(s, a.after), a.label), a.label))


def defend(s: Settings, which: str, incoming: dict[str, str], z: float) -> dict:
    from .defenses import global_outlier, topic_centrality
    from .text import load_docs, read_lines
    embed_fn, chunk_fn = _embed_fn(s), _chunk_fn(s)
    trusted = load_docs(s.corpus_dir)
    if which == "outlier":
        qv = embed_fn(read_lines(s.diverse_queries))
        return global_outlier(trusted, incoming, qv, embed_fn, chunk_fn, z, s.topic_prefix)
    topic = {d: t for d, t in trusted.items() if d.startswith(s.topic_prefix)}
    qv = embed_fn(read_lines(s.topic_queries))
    return topic_centrality(topic, incoming, qv, embed_fn, chunk_fn, z)


def cmd_defend(s: Settings, a) -> None:
    from .defenses import table
    res = defend(s, a.defense, _poison_docs(s, a.files), a.z)
    print(table(res))
    print(json.dumps(res["summary"], indent=2))
    _save(s, f"defense_{a.defense}.json", res)


def cmd_run(s: Settings, a) -> None:
    """Every stage end to end, in a fresh workdir state."""
    ns = argparse.Namespace
    print("## 1/6 build victim store + baseline canary")
    cmd_build(s, ns(collection=s.collection))
    base = canary(s, s.collection, rerank=False)
    _save(s, "baseline_canary.json", base)

    print("\n## 2/6 craft poison (offline, white-box)")
    cmd_craft(s, ns(methods=a.methods, marker=a.marker, seed=a.seed, max_chars=a.max_chars))

    print("\n## 3/6 validate offline (no DB writes)")
    _save(s, "validation.json", validate(s, _poison_docs(s)))

    print("\n## 4/6 inject into the live store + canary")
    cmd_inject(s, ns(collection=s.collection, files=None))
    post = canary(s, s.collection, rerank=False)
    _save(s, "post_poison_canary.json", post)
    vec = compare(s, base, post, "vector_only")

    print("\n## 5/6 defense: cross-encoder rerank")
    cmd_build(s, ns(collection=s.clean_collection))
    base_rr = canary(s, s.clean_collection, rerank=True)
    post_rr = canary(s, s.collection, rerank=True)
    _save(s, "baseline_canary_rerank.json", base_rr)
    _save(s, "post_poison_canary_rerank.json", post_rr)
    rr = compare(s, base_rr, post_rr, "rerank")
    rr_vs_vec = compare(s, base, post_rr, "vector_baseline_vs_rerank_poisoned")

    print("\n## 6/6 defenses: global outlier + in-topic centrality")
    poison = _poison_docs(s)
    outl = defend(s, "outlier", poison, a.z)
    topic = defend(s, "topic", poison, a.z)
    _save(s, "defense_outlier.json", outl)
    _save(s, "defense_topic.json", topic)

    md = summary_markdown(s, base, post, post_rr, vec, rr, rr_vs_vec, outl, topic)
    _save(s, "summary.md", md)
    print("\n" + md)
    print(f"all results in {s.results_dir}")


def summary_markdown(s, base, post, post_rr, vec, rr, rr_vs_vec, outl, topic) -> str:
    b, p, r = base["summary"], post["summary"], post_rr["summary"]
    n = b["n_queries"]
    o, t = outl["summary"], topic["summary"]
    return "\n".join([
        f"# ragpoison summary ({n} canary queries, target `{s.target_doc}`)", "",
        f"Embedding model `{s.embed_model}`, reranker `{s.rerank_model}`.", "",
        "| metric | baseline | poisoned, vector only | poisoned, + rerank |",
        "|---|---|---|---|",
        f"| target in top-{s.top_k} | {b['target_in_top3_pct']}% | {p['target_in_top3_pct']}% | {r['target_in_top3_pct']}% |",
        f"| target at rank 1 | {b['target_rank1_pct']}% | {p['target_rank1_pct']}% | {r['target_rank1_pct']}% |",
        f"| target dropped out of top-{s.top_k} | - | {vec['summary']['target_dropped_out_of_top3_pct']}% | "
        f"{rr_vs_vec['summary']['target_dropped_out_of_top3_pct']}% vs vector baseline, "
        f"{rr['summary']['target_dropped_out_of_top3_pct']}% vs rerank baseline |",
        f"| poison in top-{s.top_k} | {b['poison_in_top3_pct']}% | {p['poison_in_top3_pct']}% | {r['poison_in_top3_pct']}% |",
        f"| poison at rank 1 | {b['poison_rank1_pct']}% | {p['poison_rank1_pct']}% | {r['poison_rank1_pct']}% |",
        "",
        f"Global outlier check (z > {o['z_threshold']}): poison flagged {o['incoming_flagged']}, "
        f"false positives {o['false_positives'] or 'none'}.",
        f"In-topic centrality check (z > {t['z_threshold']}): poison flagged {t['incoming_flagged']}, "
        f"false positives {t['false_positives'] or 'none'}.", ""])


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    d = Settings()
    p = argparse.ArgumentParser(prog="ragpoison", description="Measure embedding-space poisoning of a RAG "
                                "store and evaluate defenses against it, on a corpus you control.")
    p.add_argument("--workdir", type=Path, default=d.workdir, help="where the DB, poison and results go (%(default)s)")
    p.add_argument("--corpus", type=Path, default=d.corpus_dir, help="directory of *.txt docs (bundled demo corpus)")
    p.add_argument("--target", default=d.target_doc, help="doc id the canaries should surface (%(default)s)")
    p.add_argument("--topic-prefix", default=d.topic_prefix, help="doc-id prefix of the target topic (%(default)s)")
    p.add_argument("--attacker-queries", type=Path, default=d.attacker_queries)
    p.add_argument("--canary-queries", type=Path, default=d.canary_queries)
    p.add_argument("--diverse-queries", type=Path, default=d.diverse_queries)
    p.add_argument("--topic-queries", type=Path, default=d.topic_queries)
    p.add_argument("--embed-model", default=d.embed_model)
    p.add_argument("--rerank-model", default=d.rerank_model)
    p.add_argument("--rerank-pool", type=int, default=d.rerank_pool)
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    def craft_opts(sp):
        sp.add_argument("--methods", default="ABC", help="A=word greedy, B=sentence greedy (readable), "
                        "C=word substitution only (default %(default)s)")
        sp.add_argument("--marker", default="[LAB-MARKER poison-doc-{tag}]",
                        help="fixed payload appended to each candidate; {tag} = method letter")
        sp.add_argument("--seed", type=int, default=1234, help="RNG seed (%(default)s)")
        sp.add_argument("--max-chars", type=int, default=480, help="keep each candidate to one chunk")

    def files_opt(sp):
        sp.add_argument("files", nargs="*", help="poison docs (default: everything in <workdir>/poison)")

    sp = add("build", cmd_build, "(re)build a collection from the corpus")
    sp.add_argument("--collection")

    sp = add("inject", cmd_inject, "append poison docs to a live collection")
    files_opt(sp)
    sp.add_argument("--collection")

    sp = add("query", cmd_query, "run one query against a collection")
    sp.add_argument("query")
    sp.add_argument("-n", type=int, default=5)
    sp.add_argument("--rerank", action="store_true")
    sp.add_argument("--collection")

    craft_opts(add("craft", cmd_craft, "craft poison candidates against the attacker query cluster (offline)"))

    files_opt(add("validate", cmd_validate, "simulate candidates against the canaries without writing to the DB"))

    sp = add("canary", cmd_canary, "score a collection against the canary queries")
    sp.add_argument("--out", required=True, help="result file name inside <workdir>/results")
    sp.add_argument("--rerank", action="store_true")
    sp.add_argument("--collection")

    sp = add("compare", cmd_compare, "diff two canary result files")
    sp.add_argument("before")
    sp.add_argument("after")
    sp.add_argument("--label", required=True)

    sp = add("defend", cmd_defend, "evaluate a detection defense against poison docs")
    sp.add_argument("defense", choices=["outlier", "topic"],
                    help="outlier = global breadth check; topic = in-topic centrality z-score")
    files_opt(sp)
    sp.add_argument("--z", type=float, default=2.0, help="flag threshold (%(default)s)")

    sp = add("run", cmd_run, "run every stage end to end and write results/summary.md")
    craft_opts(sp)
    sp.add_argument("--z", type=float, default=2.0)
    return p


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    s = replace(Settings(), workdir=a.workdir, corpus_dir=a.corpus, target_doc=a.target,
                topic_prefix=a.topic_prefix, attacker_queries=a.attacker_queries, canary_queries=a.canary_queries,
                diverse_queries=a.diverse_queries, topic_queries=a.topic_queries, embed_model=a.embed_model,
                rerank_model=a.rerank_model, rerank_pool=a.rerank_pool)
    a.fn(s, a)


if __name__ == "__main__":
    main()
