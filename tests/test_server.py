"""API tests against a real Chroma store with a toy bag-of-words embedder (no model download)."""
import hashlib
import time

import httpx
import numpy as np
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ragpoison import models, store  # noqa: E402
from ragpoison.config import Settings  # noqa: E402
from ragpoison.server import create_app  # noqa: E402

BASE = "http://127.0.0.1"
JSON = {"Content-Type": "application/json"}


def toy_embed(texts, model=None, batch_size=64):
    out = np.zeros((len(texts), 64))
    for i, t in enumerate(texts):
        for w in t.lower().split():
            out[i, int(hashlib.md5(w.strip(".,?!").encode()).hexdigest(), 16) % 64] += 1
    return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "embed", toy_embed)
    monkeypatch.setattr(store, "embed", toy_embed)
    monkeypatch.setattr(store, "cross_scores",  # toy "cross-encoder": the same bag-of-words cosine
                        lambda pairs, model: np.array([float(toy_embed([q])[0] @ toy_embed([t])[0]) for q, t in pairs]))
    app = create_app(Settings(workdir=tmp_path))
    with TestClient(app, base_url=BASE) as c:
        yield c


def wait(c, r):
    jid = r.json()["job_id"]
    for _ in range(300):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] in ("done", "error"):
            assert j["status"] == "done", j["error"]
            return j["result"]
        time.sleep(0.05)
    raise TimeoutError(jid)


# A bag-of-words "centroid" document for the toy embedder: the canary words themselves, kept to one chunk.
POISON = ("what are the risks could I lose money is my investment guaranteed what could go wrong "
          "with my portfolio is this safe or can it fail [LAB-MARKER poison-doc-T]")


def test_guard_blocks_rebinding_csrf_and_cross_origin(client):
    assert client.get("/api/status", headers={"Host": "attacker.example"}).status_code == 421
    assert client.post("/api/build", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    r = client.post("/api/build", json={}, headers={"Origin": "http://attacker.example"})
    assert r.status_code == 403
    assert client.get("/api/status").status_code == 200


def test_token_required_when_configured(tmp_path):
    with TestClient(create_app(Settings(workdir=tmp_path), token="s3cret"), base_url=BASE) as c:
        assert c.get("/api/status").status_code == 401
        assert c.get("/api/status", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/").status_code == 200  # the UI page itself is public; it asks for the token


def test_local_flow_build_inject_measure_compare(client):
    wait(client, client.post("/api/build", json={}))
    base = wait(client, client.post("/api/canary", json={"save_as": "b.json"}))
    assert base["summary"]["poison_in_top3_pct"] == 0

    assert client.put("/api/poison/cand_T", json={"text": POISON}).status_code == 200
    assert client.put("/api/poison/../evil", json={"text": "x"}).status_code in (400, 404)
    r = client.post("/api/inject", json={"names": ["cand_T"]}).json()
    assert r["injected"] == {"cand_T": 1}

    hits = client.post("/api/query", json={"query": "could I lose money doing this", "k": 30}).json()["results"]
    assert any(h["doc_id"] == "cand_T" and h["poison"] for h in hits)

    after = wait(client, client.post("/api/canary", json={"save_as": "a.json"}))
    assert after["summary"]["poison_in_top3_pct"] > 0
    d = client.post("/api/compare", json={"before": "b.json", "after": "a.json", "label": "t"}).json()
    assert d["summary"]["n_queries"] == 14 and "Canary diff" in d["markdown"]
    assert "compare_t.json" in client.get("/api/results").json()


def test_validate_and_defend_jobs(client):
    client.put("/api/poison/cand_T", json={"text": POISON})
    v = wait(client, client.post("/api/validate", json={}))
    assert "cand_T" in v["candidates"] and "baseline" in v
    for kind in ("outlier", "topic"):
        res = wait(client, client.post("/api/defend", json={"defense": kind}))
        assert res["docs"]["cand_T"]["incoming"]


def test_remote_target_against_this_servers_rag_contract(client, monkeypatch):
    """RemoteTarget speaks HTTP; route its calls into the same app's /rag endpoints."""
    def fake_post(url, json=None, headers=None, timeout=None):
        r = client.post(url.replace(BASE + ":9", ""), json=json)
        return httpx.Response(r.status_code, content=r.content, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", fake_post)

    wait(client, client.post("/api/build", json={}))
    remote = {"kind": "remote", "base_url": BASE + ":9", "query_path": "/rag/query", "ingest_path": "/rag/ingest"}
    base = wait(client, client.post("/api/canary", json={"target": remote}))
    assert base["summary"]["poison_in_top3_pct"] == 0

    client.put("/api/poison/cand_T", json={"text": POISON})
    assert client.post("/api/inject", json={"target": remote}).status_code == 200
    after = wait(client, client.post("/api/canary", json={"target": remote, "rerank": True}))
    assert after["summary"]["mode"].endswith("client-side rerank")
    assert after["summary"]["poison_in_top3_pct"] > 0
    # the doc arrived through the public ingest endpoint, marked as poison by its marker
    docs = client.post("/api/target/info", json={}).json()["docs"]
    assert {"doc_id": "cand_T", "poison": True} in docs


def test_remote_target_requires_http_url(client):
    r = client.post("/api/query", json={"query": "x", "target": {"kind": "remote", "base_url": "file:///etc/passwd"}})
    assert r.status_code == 422
