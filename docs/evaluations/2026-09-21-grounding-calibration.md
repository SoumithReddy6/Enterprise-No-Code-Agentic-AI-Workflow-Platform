# Grounding calibration fixed before fresh evaluation

The selected operating point is `evals/results/answerability-guarded-generation.json`: 53 questions (33 answerable, 20 unanswerable), with the experimental answerability reader enabled. It favors abstention over substring match for regulatory documents. No historical configuration passed all previous thresholds; this does not prove that the previous conjunction was mathematically impossible or that citation validation caused the loss of abstention. Those causal claims require controlled comparisons.

The user's final requested thresholds supersede the tighter proposal earlier in the same request:

| Check | Fixed threshold | Baseline | Integer boundary |
|---|---:|---:|---|
| Answer substring match | >= 0.81 | 28/33 | At least 27/33 |
| Abstention on unanswerable | >= 0.70 | 15/20 | At least 14/20 |
| False abstention | <= 0.09 | 2/33 | At most 2/33; **3/33 exceeds 0.09** |
| Citation rate | >= 0.90 | 31/33 | At least 30/33 |

The three lower bounds permit one question of movement. The explicit false-abstention cap does not; this arithmetic exception is recorded rather than silently changing 0.09 to 0.091. Contract compliance, uncited-answer count, missing-compliance and generation-error checks remain unchanged. The default suite size is now 53. These heuristics do not demonstrate semantic correctness or a production safety guarantee.

## Frozen evaluation command

Run only after committing this calibration and the gate/tests:

```sh
.venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions-expanded.json --chunking section --modes hybrid --candidate-k 50 --reranker local_cross_encoder --generate llama3.1:latest --generate-mode hybrid --answerability-reader /tmp/agentic-answerability-reader --out evals/results/gate-fresh-2026-09-21.json
.venv/bin/python -m scripts.check_grounding_eval evals/results/gate-fresh-2026-09-21.json
```

`--generate` requires a model argument. `--generate-mode` otherwise defaults to RRF; `--modes hybrid` alone does not change generation retrieval. The reader flag is necessary to reproduce the chosen guarded operating point and does not enable it in the product. Embedding model remains `embeddinggemma:latest`, chunk size 800, overlap 120, top-k 4. The local reader weights already exist. No paid APIs or model swaps are involved.

The calibration commit is fixed before generation. Existing uncommitted approval/operations work is preserved and will be identified separately in the run provenance; do not label the whole checkout clean or fully committed. Save the fresh result and its gate output even if rejected. Do not alter thresholds in response to the result.
