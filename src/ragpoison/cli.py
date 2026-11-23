"""Command-line interface. Run `ragpoison -h` or `ragpoison <command> -h`."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from . import service
from .config import Settings
from .targets import LocalTarget

DEFAULT_MARKER = "[LAB-MARKER poison-doc-{tag}]"


def _print_canary(res: dict) -> None:
    for r in res["results"]:
        p = r["best_poison"]
        print(f"{r['query'][:52]:<52} target rank={str(r['target_rank']):>4}  "
              f"top3={'Y' if r['target_in_top3'] else 'N'}  {'poison@' + str(p['rank']) if p else '-'}")
    print(json.dumps(res["summary"], indent=2))


# --------------------------------------------------------------------------- commands


def cmd_build(s: Settings, a) -> None:
    r = service.build(s, a.collection)
    print(f"built collection '{r['collection']}' from {r['corpus']}: {r['chunks']} chunks")


def cmd_inject(s: Settings, a) -> None:
    try:
        r = LocalTarget(s, a.collection or s.collection).inject(service.poison_docs(s, a.files))
    except RuntimeError as e:
        sys.exit(str(e))
    for d, n in r["injected"].items():
        print(f"injected {d}: {n} chunk(s)")
    print(f"collection now holds {r['total_chunks']} chunks")


def cmd_query(s: Settings, a) -> None:
    for h in LocalTarget(s, a.collection or s.collection).retrieve(a.query, k=a.n, use_rerank=a.rerank):
        flag = "  <-- POISON" if h["poison"] else ""
        print(f"{h['rank']}. {h['doc_id']:<32} score={h['score']:.4f}{flag}\n   {h['text'][:110]}...")


def cmd_craft(s: Settings, a) -> None:
    service.craft(s, a.methods, a.marker, a.seed, a.max_chars)


def cmd_validate(s: Settings, a) -> None:
    cands = service.poison_docs(s, a.files)
    if not cands:
        sys.exit(f"no candidates in {s.poison_dir}; run `ragpoison craft` first")
    service.save_result(s, "validation.json", service.validate(s, cands))


def cmd_canary(s: Settings, a) -> None:
    res = service.canary(s, LocalTarget(s, a.collection or s.collection), a.rerank)
    _print_canary(res)
    print(f"saved {service.save_result(s, a.out, res)}")


def cmd_compare(s: Settings, a) -> None:
    from .metrics import diff_markdown
    d = service.compare(s, service.load_result(s, a.before), service.load_result(s, a.after), a.label)
    print(diff_markdown(d, a.label))


def cmd_defend(s: Settings, a) -> None:
    from .defenses import table
    res = service.defend(s, a.defense, service.poison_docs(s, a.files), a.z)
    print(table(res))
    print(json.dumps(res["summary"], indent=2))
    service.save_result(s, f"defense_{a.defense}.json", res)


def cmd_run(s: Settings, a) -> None:
    service.run_all(s, a.methods, a.marker, a.seed, a.max_chars, a.z)
    print(f"all results in {s.results_dir}")


def cmd_serve(s: Settings, a) -> None:
    import uvicorn
    from .server import create_app
    token = a.token or os.environ.get("RAGPOISON_TOKEN")
    if a.host not in ("127.0.0.1", "localhost", "::1") and not token:
        sys.exit("refusing to listen on a non-loopback address without --token (or RAGPOISON_TOKEN)")
    print(f"ragpoison UI on http://{a.host}:{a.port}/  (API docs at /docs)")
    app = create_app(s, token=token, extra_hosts=[a.host, *a.allow_host])
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


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
        sp.add_argument("--marker", default=DEFAULT_MARKER,
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

    sp = add("serve", cmd_serve, "start the web UI and REST API")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--token", help="require this bearer token on every API call (needed off loopback)")
    sp.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="extra Host header value to accept, e.g. the machine's LAN name (repeatable)")
    return p


def settings_from_args(a) -> Settings:
    return replace(Settings(), workdir=a.workdir, corpus_dir=a.corpus, target_doc=a.target,
                   topic_prefix=a.topic_prefix, attacker_queries=a.attacker_queries,
                   canary_queries=a.canary_queries, diverse_queries=a.diverse_queries,
                   topic_queries=a.topic_queries, embed_model=a.embed_model, rerank_model=a.rerank_model,
                   rerank_pool=a.rerank_pool)


def main(argv: list[str] | None = None) -> None:
    a = build_parser().parse_args(argv)
    a.fn(settings_from_args(a), a)


if __name__ == "__main__":
    main()
