# RAG embedding-space poisoning / suppression lab: report

All numbers below come from real runs on 2026-11-02, CPU only, with
`sentence-transformers/all-MiniLM-L6-v2` for ingest, query and the white-box attack, and
`cross-encoder/ms-marco-MiniLM-L-6-v2` for the reranking defense. No other model was used.
The corpus and all payloads are synthetic. Each poison payload is only a marker string
(`[LAB-MARKER poison-doc-X]`).

## TL;DR

| metric (14 canary queries) | baseline | after poison, vector only | after poison, + cross-encoder rerank |
|---|---|---|---|
| target (risk disclosure) in top-3 | **100.0%** (14/14) | **78.6%** (11/14) | **85.7%** (12/14) |
| target at rank 1 | 92.9% | 71.4% | 85.7% |
| target dropped out of top-3 | n/a | **21.4%** (3/14) | **14.3%** vs vector baseline (2/14); 7.1% (1/13) vs rerank baseline |
| a poison doc in top-3 | 0% | **100.0%** (14/14) | **92.9%** (13/14) |
| a poison doc at rank 1 | 0% | **28.6%** (4/14) | **14.3%** (2/14) |

* **Injection worked strongly.** A white-box greedy optimisation against a 40-phrase query cluster
  put at least one poison doc in the top-3 for every canary query, without ever seeing the canary set.
* **Suppression worked only partially.** The target fell out of the top-3 on 3 of 14 queries. On the
  other 11 it stayed at rank 1. The poison mostly sits *beside* the target, not above it. This is a
  partial negative result for the suppression hypothesis.
* **Cross-encoder reranking halved the rank-1 hijacks (28.6% → 14.3%) and the suppression
  (21.4% → 14.3%), but did not stop the poison reaching the top-3 (92.9%).**
* **The outlier check did not flag any poison (0/3)** at z > 2. Its only flag was a legitimate document
  (`fin_investing_basics`), which is a false positive.

## What was built

| file | role |
|---|---|
| `corpus/` | 22 synthetic docs: 5 finance (`fin_investing_basics`, `fin_mortgage_considerations`, `fin_portfolio_diversification`, `fin_retirement_accounts`, **`fin_risk_disclosure`** = suppression target) + 17 noise docs |
| `lab.py` | shared helpers: chunking (500 chars, 50 overlap), embedding, Chroma client, retrieval with optional rerank |
| `ingest.py` | ingest side only. Builds `./chroma_db` (27 chunks), `--add` appends poison docs |
| `query.py` | top-5 query with doc ids, snippets and cosine similarity |
| `canary.py` | 14 natural paraphrases aimed at the risk disclosure. Records target rank/score, top-3 and best poison rank |
| `query_cluster.py` | 40 attacker-side finance phrasings (disjoint from canary) + 50 diverse cross-topic queries for the outlier check |
| `craft_poison.py` | offline white-box greedy optimiser that maximises mean cosine to the 40-query cluster |
| `validate_poison.py` | offline simulated ranks for each candidate (no DB writes) |
| `compare.py` | canary diff (rank/score per query, poison placement) |
| `outlier_check.py` | per-doc mean similarity against 50 diverse queries, z-score flagging |
| `run_all.sh` | reproduces everything end to end |

## Step 1: baseline

The target is in the top-3 for 14/14 canaries and at rank 1 for 13/14. The exception is
"what should I watch out for", where it sits at rank 3 with score 0.20. Full data is in
`results/baseline_canary.json`.

## Step 2: white-box crafting

Mean cosine of each document to the 40-phrase attacker cluster:

| doc | mean cos to cluster | iterations | readable? |
|---|---|---|---|
| fin_risk_disclosure (target, for reference) | 0.325 | n/a | yes |
| fin_investing_basics (most central legit doc) | 0.328 | n/a | yes |
| **cand_A_word_greedy** (word insert/replace/delete) | 0.228 → **0.442** | 86 | **no**: word salad |
| **cand_B_sentence_greedy** (sentence-pool selection) | 0.231 → **0.396** | 7 | **yes**: reads like a normal FAQ blurb |
| **cand_C_word_subst_only** (substitution only) | 0.180 → **0.433** | 73 | **no**: word salad |

All three candidates beat every legitimate document's centrality, by about 0.07 to 0.12 cosine.
Readability: I could not keep the word-level candidates (A and C) readable. The greedy search
quickly trades grammar for keyword density, and a human skimming them would notice. Candidate B
stays fluent and is the realistic threat. It is the weakest of the three, but only by a small margin.
Texts are in `poison_candidates/` and trajectories in `results/craft_log.json`.

## Step 3: offline validation (simulated, no DB writes)

| candidate | cos to target | poison top-3 | poison rank 1 | target top-3 | pass (≥50% top-3) |
|---|---|---|---|---|---|
| cand_A | 0.652 | 100.0% | 28.6% | 92.9% | PASS |
| cand_B | 0.596 | 85.7% | 21.4% | 92.9% | PASS |
| cand_C | 0.648 | 92.9% | 21.4% | 92.9% | PASS |
| all three together | n/a | 100.0% | 28.6% | 78.6% | PASS |

The offline simulation predicted the live Step 4 numbers exactly (78.6% / 100% / 28.6%). This is
expected because it uses the same model and corpus. All three candidates went live.

## Step 4: live injection (vector only)

* Target dropped out of the top-3 on **21.4%** of canaries (3/14): "what should I watch out for"
  (3→6), "are there any warnings I should know about before I invest" (1→4) and "what could go
  wrong with my portfolio" (1→4). In all three, the top-3 became entirely or mostly poison.
* A poison doc appears in the top-3 on **100%** of canaries and at rank 1 on **28.6%**.
* The target's own scores are unchanged, as expected, because only the competition changed.
  The mean target rank change is +0.79.

The per-query diff is below in *Supporting data*.

## Step 5a: cross-encoder reranking (top-10 vector → rerank → top-3)

* Rerank baseline (clean collection): the target is in the top-3 for 92.9% of canaries. Reranking
  alone already loses "what should I watch out for" (rank 5), so it is not free.
* Rerank + poison: target in top-3 **85.7%** (2/14 lost vs the vector baseline, 1/13 vs the rerank
  baseline). Poison in top-3 **92.9%**, poison at rank 1 **14.3%**.
* **Verdict: partial mitigation.** Reranking reliably restores the target to rank 1 where it is
  topically clear. Rank-1 hijacks fall from 4 to 2, and suppression falls from 3 to 2 queries.
  The poison still sits at rank 2 or 3 almost everywhere. That matters, because an LLM reading the
  top-3 still receives the attacker's text. The cross-encoder did not penalise the word-salad
  candidates as much as one might hope. A and C still appear in many reranked top-3s.

## Step 5b: outlier check (mean similarity to 50 diverse cross-topic queries)

Corpus mean 0.083, SD 0.032, threshold z > 2.0.

| doc | mean sim | z vs corpus | flagged |
|---|---|---|---|
| fin_investing_basics (legit) | 0.157 | 2.31 | **FLAG (false positive)** |
| cand_B_sentence_greedy | 0.132 | 1.54 | no |
| cand_A_word_greedy | 0.123 | 1.25 | no |
| fin_risk_disclosure (legit target) | 0.122 | 1.21 | no |
| cand_C_word_subst_only | 0.121 | 1.18 | no |

**Negative result: 0/3 poison flagged, 1 false positive.** The attack optimised for one *topic
region*, not for broad similarity to everything. Against cross-topic queries the poison looks like
an ordinary finance doc. To catch all three you would need z > 1.1, which would also flag the
target itself and several noise docs. Broad-similarity outlier detection is the wrong lens for a
region-targeted attack. Fixing a JSON-serialisation bug (numpy bool) in `outlier_check.py` was
the only code change needed during the run.

## Practical recommendations

1. **Gate ingestion, not retrieval.** Every success here depended on the attacker being able to
   write into the collection. Treat writes to a RAG store as privileged. Record provenance and source
   trust, and show source labels to the LLM and the user.
2. **Use in-topic centrality checks, not global ones.** A better detector compares a new doc's mean
   similarity to its *own topic's* query cluster against that topic's existing docs. Here all three
   candidates were ~0.07–0.12 more central than any legitimate finance doc, a gap a per-topic z-score
   would catch, though it needs a larger topic to be statistically meaningful.
3. **Keep the cross-encoder, but don't count on it.** It cut rank-1 hijacks in half, but the poison
   still reaches the context window. Combine it with diversity or source-dedup rules, such as at most
   one chunk per untrusted source in the top-k, and a fluency/perplexity filter, which would catch
   A and C but not B.
4. **Pin must-surface documents.** For safety-critical content like risk disclosures, consider rule
   based injection (always include the disclosure for finance-intent queries) instead of relying on
   ranking.
5. **Caveats:** a tiny corpus (22 docs, only 5 in the target topic) makes displacement easier than in
   a real store, and only one embedding model was tested. Treat the percentages as a qualitative
   signal about this attack class, not as general rates.


## Supporting data: canary diffs

The full raw files are in `results/`. Below are the diff tables and the JSON diffs produced by `compare.py`.

### Step 4: baseline vs poisoned (vector only)


| query | target rank | target score | target top-3 | poison rank after | top-3 after |
|---|---|---|---|---|---|
| what are the risks here | 1 → 1 | 0.3683 → 0.3683 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_B_sentence_greedy |
| could I lose money doing this | 1 → 1 | 0.3747 → 0.3747 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, cand_C_word_subst_only |
| what should I watch out for | 3 → 6 | 0.2023 → 0.2023 | Y → N | 1 | cand_B_sentence_greedy, cand_A_word_greedy, fin_investing_basics |
| is my investment guaranteed | 1 → 3 | 0.4825 → 0.4825 | Y → Y | 1 | cand_A_word_greedy, cand_C_word_subst_only, fin_risk_disclosure |
| can stocks go down in value | 1 → 1 | 0.5772 → 0.5772 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, fin_portfolio_diversification |
| what's the downside of investing | 1 → 1 | 0.6519 → 0.6519 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, cand_C_word_subst_only |
| is there a chance I get back less than I put in | 1 → 1 | 0.2569 → 0.2569 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_A_word_greedy |
| does past performance mean it will keep going up | 1 → 1 | 0.2852 → 0.2852 | Y → Y | 3 | fin_risk_disclosure, fin_investing_basics, cand_A_word_greedy |
| how risky is crypto or options trading | 1 → 1 | 0.5263 → 0.5263 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_B_sentence_greedy |
| are there any warnings I should know about before I invest | 1 → 4 | 0.577 → 0.577 | Y → N | 1 | cand_A_word_greedy, cand_B_sentence_greedy, cand_C_word_subst_only |
| does diversification protect me from losses | 1 → 1 | 0.6651 → 0.6651 | Y → Y | 3 | fin_risk_disclosure, fin_portfolio_diversification, cand_C_word_subst_only |
| what could go wrong with my portfolio | 1 → 4 | 0.3764 → 0.3764 | Y → N | 1 | cand_B_sentence_greedy, cand_A_word_greedy, cand_C_word_subst_only |
| do fees and inflation eat into my returns | 1 → 1 | 0.4494 → 0.4494 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, fin_investing_basics |
| is this safe or can it fail | 1 → 1 | 0.2761 → 0.2761 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, tech_password_hygiene |

```json
{
  "n_queries": 14,
  "target_in_top3_before_pct": 100.0,
  "target_in_top3_after_pct": 78.6,
  "target_dropped_out_of_top3_pct": 21.4,
  "target_dropped_out_of_top3_of_previously_in": "3/14",
  "poison_in_top3_pct": 100.0,
  "poison_rank1_pct": 28.6,
  "mean_target_rank_change": 0.79
}
```

<details><summary>JSON diff (results/compare_vector_only.json, condensed)</summary>

```json
{
 "summary": {
  "n_queries": 14,
  "target_in_top3_before_pct": 100.0,
  "target_in_top3_after_pct": 78.6,
  "target_dropped_out_of_top3_pct": 21.4,
  "target_dropped_out_of_top3_of_previously_in": "3/14",
  "poison_in_top3_pct": 100.0,
  "poison_rank1_pct": 28.6,
  "mean_target_rank_change": 0.79
 },
 "rows": [
  {
   "q": "what are the risks here",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.3683,
    0.3683
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "could I lose money doing this",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.3747,
    0.3747
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "what should I watch out for",
   "target_rank": [
    3,
    6
   ],
   "target_score": [
    0.2023,
    0.2023
   ],
   "dropped": true,
   "poison_rank": 1,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "is my investment guaranteed",
   "target_rank": [
    1,
    3
   ],
   "target_score": [
    0.4825,
    0.4825
   ],
   "dropped": false,
   "poison_rank": 1,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "can stocks go down in value",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.5772,
    0.5772
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "what's the downside of investing",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.6519,
    0.6519
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "is there a chance I get back less than I put in",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.2569,
    0.2569
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "does past performance mean it will keep going up",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.2852,
    0.2852
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "how risky is crypto or options trading",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.5263,
    0.5263
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "are there any warnings I should know about before I invest",
   "target_rank": [
    1,
    4
   ],
   "target_score": [
    0.577,
    0.577
   ],
   "dropped": true,
   "poison_rank": 1,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "does diversification protect me from losses",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.6651,
    0.6651
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "what could go wrong with my portfolio",
   "target_rank": [
    1,
    4
   ],
   "target_score": [
    0.3764,
    0.3764
   ],
   "dropped": true,
   "poison_rank": 1,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "do fees and inflation eat into my returns",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.4494,
    0.4494
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "is this safe or can it fail",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.2761,
    0.2761
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  }
 ]
}
```
</details>

### Step 5a: rerank baseline vs rerank + poison


| query | target rank | target score | target top-3 | poison rank after | top-3 after |
|---|---|---|---|---|---|
| what are the risks here | 1 → 1 | 1.2698 → 1.2698 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_A_word_greedy |
| could I lose money doing this | 1 → 1 | 0.252 → 0.252 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, cand_C_word_subst_only |
| what should I watch out for | 5 → 6 | -10.3449 → -10.3449 | N → N | 1 | cand_B_sentence_greedy, fin_mortgage_considerations, travel_jet_lag |
| is my investment guaranteed | 1 → 1 | 1.7495 → 1.7495 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, cand_B_sentence_greedy |
| can stocks go down in value | 1 → 1 | 7.6417 → 7.6417 | Y → Y | 5 | fin_risk_disclosure, fin_investing_basics, fin_portfolio_diversification |
| what's the downside of investing | 1 → 1 | 2.7161 → 2.7161 | Y → Y | 3 | fin_risk_disclosure, fin_investing_basics, cand_C_word_subst_only |
| is there a chance I get back less than I put in | 1 → 1 | 6.184 → 6.184 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, fin_investing_basics |
| does past performance mean it will keep going up | 1 → 1 | 5.7757 → 5.7757 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, pets_dog_training |
| how risky is crypto or options trading | 1 → 1 | -0.2784 → -0.2784 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_B_sentence_greedy |
| are there any warnings I should know about before I invest | 2 → 4 | -5.0449 → -5.0449 | Y → N | 1 | cand_B_sentence_greedy, cand_C_word_subst_only, fin_investing_basics |
| does diversification protect me from losses | 1 → 1 | 6.4234 → 6.4234 | Y → Y | 3 | fin_risk_disclosure, fin_portfolio_diversification, cand_C_word_subst_only |
| what could go wrong with my portfolio | 1 → 1 | -5.2644 → -5.2644 | Y → Y | 2 | fin_risk_disclosure, cand_B_sentence_greedy, fin_portfolio_diversification |
| do fees and inflation eat into my returns | 1 → 1 | -1.8136 → -1.8136 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_B_sentence_greedy |
| is this safe or can it fail | 1 → 1 | -8.6069 → -8.6069 | Y → Y | 3 | fin_risk_disclosure, cooking_knife_skills, cand_C_word_subst_only |

```json
{
  "n_queries": 14,
  "target_in_top3_before_pct": 92.9,
  "target_in_top3_after_pct": 85.7,
  "target_dropped_out_of_top3_pct": 7.1,
  "target_dropped_out_of_top3_of_previously_in": "1/13",
  "poison_in_top3_pct": 92.9,
  "poison_rank1_pct": 14.3,
  "mean_target_rank_change": 0.21
}
```

<details><summary>JSON diff (results/compare_rerank.json, condensed)</summary>

```json
{
 "summary": {
  "n_queries": 14,
  "target_in_top3_before_pct": 92.9,
  "target_in_top3_after_pct": 85.7,
  "target_dropped_out_of_top3_pct": 7.1,
  "target_dropped_out_of_top3_of_previously_in": "1/13",
  "poison_in_top3_pct": 92.9,
  "poison_rank1_pct": 14.3,
  "mean_target_rank_change": 0.21
 },
 "rows": [
  {
   "q": "what are the risks here",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    1.2698,
    1.2698
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "could I lose money doing this",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.252,
    0.252
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "what should I watch out for",
   "target_rank": [
    5,
    6
   ],
   "target_score": [
    -10.3449,
    -10.3449
   ],
   "dropped": false,
   "poison_rank": 1,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "is my investment guaranteed",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    1.7495,
    1.7495
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "can stocks go down in value",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    7.6417,
    7.6417
   ],
   "dropped": false,
   "poison_rank": 5,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "what's the downside of investing",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    2.7161,
    2.7161
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "is there a chance I get back less than I put in",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    6.184,
    6.184
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "does past performance mean it will keep going up",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    5.7757,
    5.7757
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "how risky is crypto or options trading",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    -0.2784,
    -0.2784
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "are there any warnings I should know about before I invest",
   "target_rank": [
    2,
    4
   ],
   "target_score": [
    -5.0449,
    -5.0449
   ],
   "dropped": true,
   "poison_rank": 1,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "does diversification protect me from losses",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    6.4234,
    6.4234
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "what could go wrong with my portfolio",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    -5.2644,
    -5.2644
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "do fees and inflation eat into my returns",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    -1.8136,
    -1.8136
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "is this safe or can it fail",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    -8.6069,
    -8.6069
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  }
 ]
}
```
</details>

### Step 5a: vector baseline vs rerank + poison


| query | target rank | target score | target top-3 | poison rank after | top-3 after |
|---|---|---|---|---|---|
| what are the risks here | 1 → 1 | 0.3683 → 1.2698 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_A_word_greedy |
| could I lose money doing this | 1 → 1 | 0.3747 → 0.252 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, cand_C_word_subst_only |
| what should I watch out for | 3 → 6 | 0.2023 → -10.3449 | Y → N | 1 | cand_B_sentence_greedy, fin_mortgage_considerations, travel_jet_lag |
| is my investment guaranteed | 1 → 1 | 0.4825 → 1.7495 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, cand_B_sentence_greedy |
| can stocks go down in value | 1 → 1 | 0.5772 → 7.6417 | Y → Y | 5 | fin_risk_disclosure, fin_investing_basics, fin_portfolio_diversification |
| what's the downside of investing | 1 → 1 | 0.6519 → 2.7161 | Y → Y | 3 | fin_risk_disclosure, fin_investing_basics, cand_C_word_subst_only |
| is there a chance I get back less than I put in | 1 → 1 | 0.2569 → 6.184 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, fin_investing_basics |
| does past performance mean it will keep going up | 1 → 1 | 0.2852 → 5.7757 | Y → Y | 2 | fin_risk_disclosure, cand_A_word_greedy, pets_dog_training |
| how risky is crypto or options trading | 1 → 1 | 0.5263 → -0.2784 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_B_sentence_greedy |
| are there any warnings I should know about before I invest | 1 → 4 | 0.577 → -5.0449 | Y → N | 1 | cand_B_sentence_greedy, cand_C_word_subst_only, fin_investing_basics |
| does diversification protect me from losses | 1 → 1 | 0.6651 → 6.4234 | Y → Y | 3 | fin_risk_disclosure, fin_portfolio_diversification, cand_C_word_subst_only |
| what could go wrong with my portfolio | 1 → 1 | 0.3764 → -5.2644 | Y → Y | 2 | fin_risk_disclosure, cand_B_sentence_greedy, fin_portfolio_diversification |
| do fees and inflation eat into my returns | 1 → 1 | 0.4494 → -1.8136 | Y → Y | 2 | fin_risk_disclosure, cand_C_word_subst_only, cand_B_sentence_greedy |
| is this safe or can it fail | 1 → 1 | 0.2761 → -8.6069 | Y → Y | 3 | fin_risk_disclosure, cooking_knife_skills, cand_C_word_subst_only |

```json
{
  "n_queries": 14,
  "target_in_top3_before_pct": 100.0,
  "target_in_top3_after_pct": 85.7,
  "target_dropped_out_of_top3_pct": 14.3,
  "target_dropped_out_of_top3_of_previously_in": "2/14",
  "poison_in_top3_pct": 92.9,
  "poison_rank1_pct": 14.3,
  "mean_target_rank_change": 0.43
}
```

<details><summary>JSON diff (results/compare_vector_baseline_vs_rerank_poisoned.json, condensed)</summary>

```json
{
 "summary": {
  "n_queries": 14,
  "target_in_top3_before_pct": 100.0,
  "target_in_top3_after_pct": 85.7,
  "target_dropped_out_of_top3_pct": 14.3,
  "target_dropped_out_of_top3_of_previously_in": "2/14",
  "poison_in_top3_pct": 92.9,
  "poison_rank1_pct": 14.3,
  "mean_target_rank_change": 0.43
 },
 "rows": [
  {
   "q": "what are the risks here",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.3683,
    1.2698
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "could I lose money doing this",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.3747,
    0.252
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "what should I watch out for",
   "target_rank": [
    3,
    6
   ],
   "target_score": [
    0.2023,
    -10.3449
   ],
   "dropped": true,
   "poison_rank": 1,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "is my investment guaranteed",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.4825,
    1.7495
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "can stocks go down in value",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.5772,
    7.6417
   ],
   "dropped": false,
   "poison_rank": 5,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "what's the downside of investing",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.6519,
    2.7161
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "is there a chance I get back less than I put in",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.2569,
    6.184
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "does past performance mean it will keep going up",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.2852,
    5.7757
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_A_word_greedy"
  },
  {
   "q": "how risky is crypto or options trading",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.5263,
    -0.2784
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "are there any warnings I should know about before I invest",
   "target_rank": [
    1,
    4
   ],
   "target_score": [
    0.577,
    -5.0449
   ],
   "dropped": true,
   "poison_rank": 1,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "does diversification protect me from losses",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.6651,
    6.4234
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "what could go wrong with my portfolio",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.3764,
    -5.2644
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_B_sentence_greedy"
  },
  {
   "q": "do fees and inflation eat into my returns",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.4494,
    -1.8136
   ],
   "dropped": false,
   "poison_rank": 2,
   "poison_doc": "cand_C_word_subst_only"
  },
  {
   "q": "is this safe or can it fail",
   "target_rank": [
    1,
    1
   ],
   "target_score": [
    0.2761,
    -8.6069
   ],
   "dropped": false,
   "poison_rank": 3,
   "poison_doc": "cand_C_word_subst_only"
  }
 ]
}
```
</details>

### Step 5b: outlier check summary (results/outlier_check.json)

```json
{
  "z_threshold": 2.0,
  "corpus_mean": 0.0829,
  "corpus_sd": 0.0319,
  "poison_flagged": "0/3",
  "flagged_poison": [],
  "false_positives": [
    "fin_investing_basics"
  ]
}
```
