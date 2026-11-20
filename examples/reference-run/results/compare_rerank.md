### Canary diff: rerank

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
