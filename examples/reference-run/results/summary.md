# ragpoison summary (14 canary queries, target `fin_risk_disclosure`)

Embedding model `sentence-transformers/all-MiniLM-L6-v2`, reranker `cross-encoder/ms-marco-MiniLM-L-6-v2`.

| metric | baseline | poisoned, vector only | poisoned, + rerank |
|---|---|---|---|
| target in top-3 | 100.0% | 78.6% | 85.7% |
| target at rank 1 | 92.9% | 71.4% | 85.7% |
| target dropped out of top-3 | - | 21.4% | 14.3% vs vector baseline, 7.1% vs rerank baseline |
| poison in top-3 | 0.0% | 100.0% | 92.9% |
| poison at rank 1 | 0.0% | 28.6% | 14.3% |

Global outlier check (z > 2.0): poison flagged 0/3, false positives ['fin_investing_basics'].
In-topic centrality check (z > 2.0): poison flagged 0/3, false positives none.
