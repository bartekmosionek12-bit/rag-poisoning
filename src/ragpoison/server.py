"""HTTP server: REST API for every operation, the web UI, and a demo RAG endpoint.

    ragpoison serve                 # http://127.0.0.1:8765/  (OpenAPI docs at /docs)

/api/...   the tool. Long operations (build, craft, validate, canary, defend,
           run) start a background job and return {"job_id"}; poll
           GET /api/jobs/{id}?since=N for status and new log lines. Jobs run one
           at a time, in order.
/rag/...   the local store behind the small JSON contract that RemoteTarget
           speaks, so one ragpoison instance can be the "remote" target of another
           and so any client can treat it as a toy RAG service.
/          the single-page UI (static files in ragpoison/web/).

A local server that writes to a vector store is itself an attack surface, so
every request goes through `guard`: Host must be loopback (or the --host given),
which blocks DNS rebinding; state-changing requests must be application/json
and same-origin, which blocks cross-site form/fetch CSRF; and an optional
bearer token gates /api and /rag. No CORS headers are sent.
"""
from __future__ import annotations

import hmac
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, service
from .config import Settings
from .targets import POISON_MARKER, LocalTarget, RemoteTarget, Target

WEB_DIR = Path(__file__).resolve().parent / "web"
LOOPBACK = {"127.0.0.1", "localhost", "[::1]"}


def _hostname(host_header: str) -> str:
    """'127.0.0.1:8765' -> '127.0.0.1', '[::1]:8765' -> '[::1]'."""
    if host_header.startswith("["):
        return host_header.split("]")[0] + "]"
    return host_header.rsplit(":", 1)[0]


# --------------------------------------------------------------------------- jobs


class Jobs:
    """Background jobs on one worker thread, with an in-memory log per job."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ragpoison-job")
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn: Callable[[Callable[[str], None]], Any], params: dict | None = None) -> str:
        jid = uuid.uuid4().hex[:12]
        job = {"id": jid, "kind": kind, "params": params or {}, "status": "queued", "log": [], "result": None,
               "error": None, "created": time.time(), "started": None, "finished": None}
        with self._lock:
            self._jobs[jid] = job

        def log(line: str) -> None:
            job["log"].extend(str(line).splitlines() or [""])

        def run() -> None:
            job["status"], job["started"] = "running", time.time()
            try:
                job["result"] = fn(log)
                job["status"] = "done"
            except Exception as e:  # surfaced to the UI, not swallowed
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
                log(traceback.format_exc(limit=3))
            job["finished"] = time.time()

        self._pool.submit(run)
        return jid

    def get(self, jid: str, since: int = 0) -> dict:
        job = self._jobs.get(jid)
        if job is None:
            raise HTTPException(404, "no such job")
        return {**{k: v for k, v in job.items() if k != "log"}, "log": job["log"][since:], "log_len": len(job["log"])}

    def list(self) -> list[dict]:
        return [{k: j[k] for k in ("id", "kind", "status", "created", "started", "finished", "error")}
                for j in sorted(self._jobs.values(), key=lambda j: j["created"], reverse=True)]


# --------------------------------------------------------------------------- request models


class TargetSpec(BaseModel):
    kind: Literal["local", "remote"] = "local"
    collection: str | None = None          # local
    base_url: str | None = None            # remote
    query_path: str = "/query"
    ingest_path: str | None = "/ingest"
    headers: dict[str, str] = Field(default_factory=dict)
    results_key: str = "results"
    id_key: str = "doc_id"
    score_key: str = "score"
    text_key: str = "text"
    pool: int = 10
    poison_ids: list[str] | None = None    # default: names of the current poison candidates


class QueryReq(BaseModel):
    target: TargetSpec = Field(default_factory=TargetSpec)
    query: str
    k: int = 5
    rerank: bool = False


class CraftReq(BaseModel):
    methods: str = "ABC"
    marker: str = "[LAB-MARKER poison-doc-{tag}]"
    seed: int = 1234
    max_chars: int = 480


class NamesReq(BaseModel):
    names: list[str] | None = None         # default: every current poison candidate


class InjectReq(NamesReq):
    target: TargetSpec = Field(default_factory=TargetSpec)


class CanaryReq(BaseModel):
    target: TargetSpec = Field(default_factory=TargetSpec)
    rerank: bool = False
    save_as: str | None = None


class CompareReq(BaseModel):
    before: str
    after: str
    label: str


class DefendReq(NamesReq):
    defense: Literal["outlier", "topic"]
    z: float = 2.0


class RunReq(CraftReq):
    z: float = 2.0


class BuildReq(BaseModel):
    collection: str | None = None


class PoisonText(BaseModel):
    text: str = Field(max_length=20000)


class RagQuery(BaseModel):
    query: str
    k: int = 10
    collection: str | None = None


class RagIngest(BaseModel):
    doc_id: str
    text: str = Field(max_length=20000)
    collection: str | None = None


# --------------------------------------------------------------------------- app


def create_app(s: Settings, token: str | None = None, extra_hosts: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="ragpoison", version=__version__,
                  description="Measure embedding-space poisoning of a RAG store and evaluate defenses.")
    jobs = Jobs()
    allowed_hosts = LOOPBACK | {f"[{h}]" if ":" in h else h for h in (extra_hosts or []) if h not in ("0.0.0.0", "::")}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = _hostname(request.headers.get("host", ""))
        if host not in allowed_hosts:
            return JSONResponse({"detail": f"host {host!r} not allowed"}, status_code=421)
        path = request.url.path
        if path.startswith(("/api", "/rag")):
            if token:
                got = request.headers.get("authorization", "")
                if not hmac.compare_digest(got.encode(), f"Bearer {token}".encode()):
                    return JSONResponse({"detail": "missing or wrong bearer token"}, status_code=401)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    return JSONResponse({"detail": "state-changing requests must be application/json"},
                                        status_code=415)
                origin = request.headers.get("origin")
                if origin and _hostname(urlsplit(origin).netloc) not in allowed_hosts:
                    return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)

    # ------------------------------------------------------------------ helpers

    def target_of(spec: TargetSpec) -> Target:
        if spec.kind == "local":
            return LocalTarget(s, service.check_name(spec.collection or s.collection))
        if not spec.base_url or urlsplit(spec.base_url).scheme not in ("http", "https"):
            raise HTTPException(422, "remote target needs an http(s) base_url")
        ids = set(spec.poison_ids) if spec.poison_ids is not None else set(service.poison_docs(s))
        return RemoteTarget(s, spec.base_url, spec.query_path, spec.ingest_path or None, spec.headers,
                            spec.results_key, spec.id_key, spec.score_key, spec.text_key, spec.pool, ids)

    def selected_poison(names: list[str] | None) -> dict[str, str]:
        docs = service.poison_docs(s)
        if names is None:
            return docs
        missing = [n for n in names if n not in docs]
        if missing:
            raise HTTPException(404, f"unknown poison candidate(s): {missing}")
        return {n: docs[n] for n in names}

    def guarded(fn: Callable[[], Any]):
        try:
            return fn()
        except HTTPException:
            raise
        except (ValueError, KeyError, RuntimeError) as e:
            raise HTTPException(400, f"{type(e).__name__}: {e}")
        except Exception as e:  # network errors from remote targets, chroma errors
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------------ info

    @app.get("/api/status")
    def status():
        from .text import read_lines
        corpus = service.corpus(s)
        return {
            "version": __version__,
            "workdir": str(s.workdir.resolve()),
            "embed_model": s.embed_model, "rerank_model": s.rerank_model, "rerank_pool": s.rerank_pool,
            "target_doc": s.target_doc, "topic_prefix": s.topic_prefix,
            "collection": s.collection, "clean_collection": s.clean_collection,
            "corpus": [{"doc_id": d, "chars": len(t), "topic": d.startswith(s.topic_prefix),
                        "target": d == s.target_doc} for d, t in corpus.items()],
            "queries": {k: len(read_lines(p)) for k, p in [("attacker", s.attacker_queries),
                        ("canary", s.canary_queries), ("topic", s.topic_queries), ("diverse", s.diverse_queries)]},
            "poison": sorted(service.poison_docs(s)),
            "results": service.list_results(s),
            "auth": bool(token),
        }

    @app.get("/api/corpus/{doc_id}")
    def corpus_doc(doc_id: str):
        docs = service.corpus(s)
        if doc_id not in docs:
            raise HTTPException(404, "no such document")
        return {"doc_id": doc_id, "text": docs[doc_id]}

    @app.get("/api/queries")
    def queries():
        from .text import read_lines
        return {"attacker": read_lines(s.attacker_queries), "canary": read_lines(s.canary_queries),
                "topic": read_lines(s.topic_queries), "diverse": read_lines(s.diverse_queries)}

    @app.post("/api/target/info")
    def target_info(spec: TargetSpec = Body(default_factory=TargetSpec)):
        t = target_of(spec)
        return guarded(t.info)

    # ------------------------------------------------------------------ store & retrieval

    @app.post("/api/build")
    def build(req: BuildReq = Body(default_factory=BuildReq)):
        name = service.check_name(req.collection or s.collection)
        return {"job_id": jobs.submit("build", lambda log: service.build(s, name), req.model_dump())}

    @app.post("/api/query")
    def query(req: QueryReq):
        t = target_of(req.target)
        hits = guarded(lambda: t.retrieve(req.query, k=req.k, use_rerank=req.rerank))
        return {"target": t.info() if t.kind == "remote" else {"kind": "local", "collection": t.collection},
                "results": [{k: h.get(k) for k in ("rank", "doc_id", "score", "vector_score", "poison", "text")}
                            for h in hits],
                "target_doc": s.target_doc}

    @app.post("/api/inject")
    def inject(req: InjectReq):
        t = target_of(req.target)
        docs = selected_poison(req.names)
        if not docs:
            raise HTTPException(400, "no poison candidates to inject; craft or add one first")
        return guarded(lambda: t.inject(docs))

    # ------------------------------------------------------------------ poison candidates

    @app.get("/api/poison")
    def poison_list():
        log = {}
        try:
            log = service.load_result(s, "craft_log.json")
        except (FileNotFoundError, ValueError):
            pass
        return {n: {"text": t, "craft": log.get(n)} for n, t in service.poison_docs(s).items()}

    @app.put("/api/poison/{name}")
    def poison_put(name: str, body: PoisonText):
        guarded(lambda: service.save_poison(s, name, body.text))
        return {"saved": name}

    @app.delete("/api/poison/{name}")
    def poison_delete(name: str):
        guarded(lambda: service.delete_poison(s, name))
        return {"deleted": name}

    @app.post("/api/craft")
    def craft(req: CraftReq = Body(default_factory=CraftReq)):
        return {"job_id": jobs.submit("craft", lambda log: service.craft(s, req.methods, req.marker, req.seed,
                                                                          req.max_chars, log), req.model_dump())}

    @app.post("/api/validate")
    def validate(req: NamesReq = Body(default_factory=NamesReq)):
        docs = selected_poison(req.names)
        if not docs:
            raise HTTPException(400, "no poison candidates to validate")

        def fn(log):
            out = service.validate(s, docs, log)
            service.save_result(s, "validation.json", out)
            return out
        return {"job_id": jobs.submit("validate", fn, req.model_dump())}

    # ------------------------------------------------------------------ measurement & defenses

    @app.post("/api/canary")
    def canary(req: CanaryReq):
        t = target_of(req.target)
        if req.save_as:
            service.check_name(req.save_as)

        def fn(log):
            out = service.canary(s, t, req.rerank, log)
            if req.save_as:
                service.save_result(s, req.save_as, out)
            return out
        return {"job_id": jobs.submit("canary", fn, req.model_dump())}

    @app.post("/api/compare")
    def compare(req: CompareReq):
        from .metrics import diff_markdown

        def fn():
            d = service.compare(s, service.load_result(s, req.before), service.load_result(s, req.after), req.label)
            return {**d, "markdown": diff_markdown(d, req.label)}
        try:
            return guarded(fn)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))

    @app.post("/api/defend")
    def defend(req: DefendReq):
        docs = selected_poison(req.names)

        def fn(log):
            out = service.defend(s, req.defense, docs, req.z)
            service.save_result(s, f"defense_{req.defense}.json", out)
            return out
        return {"job_id": jobs.submit(f"defend:{req.defense}", fn, req.model_dump())}

    @app.post("/api/run")
    def run(req: RunReq = Body(default_factory=RunReq)):
        return {"job_id": jobs.submit("run", lambda log: service.run_all(s, req.methods, req.marker, req.seed,
                                                                          req.max_chars, req.z, log),
                                      req.model_dump())}

    # ------------------------------------------------------------------ jobs & results

    @app.get("/api/jobs")
    def job_list():
        return jobs.list()

    @app.get("/api/jobs/{jid}")
    def job_get(jid: str, since: int = 0):
        return jobs.get(jid, since)

    @app.get("/api/results")
    def results():
        return service.list_results(s)

    @app.get("/api/results/{name}")
    def result(name: str):
        try:
            path = s.results_dir / service.check_name(name)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not path.is_file():
            raise HTTPException(404, "no such result")
        return FileResponse(path)

    # ------------------------------------------------------------------ demo RAG contract

    @app.post("/rag/query")
    def rag_query(req: RagQuery):
        t = LocalTarget(s, service.check_name(req.collection or s.collection))
        hits = guarded(lambda: t.retrieve(req.query, k=req.k))
        return {"results": [{"doc_id": h["doc_id"], "score": h["score"], "text": h["text"]} for h in hits]}

    @app.post("/rag/ingest")
    def rag_ingest(req: RagIngest):
        from .store import ingest, open_collection
        name = service.check_name(req.collection or s.collection)
        n = guarded(lambda: ingest(s, open_collection(s, name), {service.check_name(req.doc_id): req.text},
                                   poison=POISON_MARKER in req.text, source="rag-api"))
        return {"doc_id": req.doc_id, "chunks": n}

    # ------------------------------------------------------------------ UI

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app
