# Fresh guarded grounding evaluation — 2026-09-21

**Accepted by all nine checks.** Calibration commit `33f7da4` was created before generation. Its thresholds were not changed after the run. Gate/evaluation unit tests passed (28 tests).

| Metric | Fresh measurement | Fixed requirement |
|---|---:|---:|
| Answer substring match | 28/33 = 0.848 | >= 0.81 |
| Abstention on unanswerable | 15/20 = 0.750 | >= 0.70 |
| False abstention | 2/33 = 0.061 | <= 0.09 |
| Citation rate | 31/33 = 0.939 | >= 0.90 |
| First-attempt contract compliance | 36/38 = 0.947 | >= 0.90 |
| Uncited answerable rows | 2 | <= 5 |
| Generation errors | 0 | 0 |

The full 53 cases were evaluated. Fifteen guard rejections avoided generation; two generated answers required contract repair. The fresh run used section chunking, 800-character chunks, 120-character overlap, candidate pool 50, top-k 4, local cross-encoder reranking, hybrid search, `embeddinggemma:latest`, `llama3.1:latest` and the same local answerability reader as the selected baseline.

All recorded quality summary metrics match `answerability-guarded-generation.json`. Per-question heuristic pass, abstention, answer retrieval and contract-compliance outcomes also match; mean per-question time changed from 6.000 to 5.534 seconds. No claim of statistically established equivalence follows from one repeated run.

## Evidence

- [Fresh report with passage text](../../evals/results/gate-fresh-2026-09-21.json)
- [Gate output: accepted](../../evals/results/gate-fresh-2026-09-21-check.json)
- [Baseline comparison and remaining unanswerable failures](../../evals/results/gate-fresh-2026-09-21-comparison.json)
- [Commit, commands, model digests and source/input hashes](../../evals/results/gate-fresh-2026-09-21-provenance.json)
- [Pre-run calibration rationale](2026-09-21-grounding-calibration.md)

The source/input hashes were rechecked after completion and no evaluated files changed during the run. The gate matches the pre-run calibration commit. The working checkout included the existing uncommitted approval/operations changes; its status and evaluated file hashes are recorded rather than calling it a clean committed checkout.

## Scope and remaining risk

This accepts the **guarded evaluation configuration** under the explicitly chosen thresholds. The answerability guard remains an offline harness option (`production_enabled: false`); this is not evidence that the default unguarded application passes. The serial Input → Retrieve → Agent → Response evaluation does not exercise agent budget exhaustion, approval recovery, or high-volume evidence eviction.

Five of twenty unanswerable cases still did not abstain. The comparison artifact includes their questions, answers and citation labels for investigation. Substring matching and source-document matching do not validate semantic correctness or claim entailment. The gate was calibrated on this same question suite, so the fresh run is a repeatability check, not an independent held-out safety evaluation. No thresholds were loosened after observing this result, and no production guard or model was changed.
