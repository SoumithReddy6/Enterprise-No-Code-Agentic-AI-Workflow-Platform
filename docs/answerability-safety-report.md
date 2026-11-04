# Answerability safety follow-up

## Status

Work is on `codex/retrieval-answerability-safety`; existing changes were preserved, with no commit or push. The abstention threshold remains **≥0.9** and the substring threshold remains **≥0.879**. No production answerability guard or claim judge has been enabled. Generation still uses `llama3.1:latest`, with its existing prompt and grounded contract.

The requested infrastructure and diagnostic experiments are implemented. Neither retrieval-score thresholds nor the tested standalone answerability reader meet the safety/false-abstention requirements. The new NLI judge emits valid results but has poor semantic agreement. These failures are preserved rather than turned into passing gates.

`f11-after` remains the preferred historical comparison candidate over the boundaries variant because its abstention result was better. It is still rejected. Historical artifacts are immutable observations, not executable source snapshots; current diagnostic runs retain the reviewed parser correctness fixes and are clearly identified separately. No existing user knowledge base was rebuilt or switched by these isolated evaluations.

## Verified corrections to the review

- Historical F11 passage objects have **no `score` field**. They do not contain measured zero scores. A reader using `.get('score', 0)` manufactures zeros. Search and canonical verification already preserved the original query score; the generation artifact projection omitted it.
- `f11-after.json` already has `reranker: local_cross_encoder` in its saved settings. It is not a no-reranker baseline.
- The comparison artifact really did crash the old gate. That input error is now handled separately from quality rejection.

## Gate input handling

The CLI validates top-level structure, required metrics, finite ranges, counts, row shape, and compliance types before checking quality. Its outcomes are:

| Input/result | Exit |
| --- | ---: |
| Usable report meeting all checks | 0 |
| Usable generation report failing a check | 1 |
| Missing/malformed/retrieval-only/comparison artifact | 2 |

Unusable inputs print `not a generation report` without a traceback. The original completeness requirement remains 38 by default. The expanded suite requires explicit `--expected-count 53`; no metric threshold changes.

The [saved CLI verification](../evals/results/f11-gate-input-validation.json) records:

| Artifact | Substring | Expected answer retrieved | Unanswerable abstention | Exit / failed checks |
| --- | ---: | ---: | ---: | --- |
| f11-before | .818 | .879 | .60 | 1 / quality, safety |
| f11-after | .909 | .879 | .60 | 1 / safety |
| f11-after-boundaries | .939 | .909 | .40 | 1 / safety |
| f11-after-review | .909 | .909 | .40 | 1 / safety |
| f11-gate-comparison | — | — | — | 2 / not a generation report |

## Score persistence and separation

New generation passages save `score` (original cosine/BM25/fused/RRF score) and `rerank_score` (cross-encoder logit) separately. Missing scores remain null; they are never fabricated as zero. Search checks these fields for finite numeric values during canonical verification. They remain query-specific telemetry, not proof of provenance or entailment; a supplied score alone must not authorize an answer.

New retrieval artifacts save rows for unanswerable questions as well as answerable questions. Unanswerables are excluded from document/evidence recall denominators. Before-generation reader diagnostics preserve rejected passages separately, while actual generation evidence/citations remain empty on rejection.

The [expanded question set](../evals/real/questions-expanded.json) preserves all 38 original questions and adds 15 unanswerable ones: eight calibration and seven fixed holdout cases. Together there are 33 answerable and 20 unanswerable questions. [Label rationales](../evals/real/answerability-labels.md) describe corpus review and scope limitations. The holdout has **no positive examples**, so it cannot establish unseen-positive false-abstention performance. Labels are assistant annotations, not independent human gold.

An initial expanded run exposed an evaluation leak: the old directory glob indexed the original question file and a new annotation file. That run was stopped before completion and excluded. The new [explicit corpus manifest](../evals/real/corpus-manifest.json) admits only the four original source documents. Regression tests enforce this isolation.

The [clean score artifact](../evals/results/answerability-scored-retrieval.json) contains 53 rows and 212 passages per mode, with **zero missing or zero-valued retrieval scores**, and complete reranker logits. Answer retrieval remains .909 in all four modes. Scores clearly overlap:

| Hybrid signal | Answerable min / median / max | Unanswerable min / median / max |
| --- | --- | --- |
| Original score of first reranked passage | .406 / .938 / 1.000 | .113 / .722 / 1.000 |
| First reranker logit | −3.591 / 5.628 / 9.879 | −8.516 / −1.299 / 6.910 |

[Threshold analysis](../evals/results/answerability-score-separation.json) checks each mode separately, both first-passage and maximum returned-passage scores. Threshold selection uses only calibration rows. None satisfies both ≥90% unanswerable rejection and ≤9.1% false abstention. Under the false-abstention constraint, hybrid original scores reject at best 5/13 calibration negatives; hybrid reranker logits reject 6/13. No cutoff was selected or installed. Relevance is measurable now, but it is not sufficient to establish answerability.

A separate [rescoring of the preferred historical candidate](../evals/results/f11-after-rescored.json) is explicitly labelled recomputed cross-encoder scores on saved texts, not recovered historical fused scores. Rejecting all five old negatives by that scalar would reject nine of 33 answerable questions. Historical score fields were not rewritten.

## Explicit answerability experiment

The [local extractive reader](answerability-reader.md) scores question/evidence pairs without generated answers, answer labels, or instructions to the answering model. It uses a pinned SQuAD2-trained tinyRoBERTa ONNX classifier, complete overlapping token windows, and a predeclared span-versus-null margin threshold of zero. It does not silently truncate evidence. A long-context regression caught a tokenizer overflow problem; explicit window construction now covers the complete passage.

Standalone [53-case results](../evals/results/answerability-reader.json):

- Valid scores: 53/53; no errors.
- Unanswerable rejection: 13/20 (65%).
- False abstention: 2/33 (6.06%).
- Held-out negatives rejected: 5/7 (71.43%).

Even a [calibration-only margin sweep](../evals/results/answerability-reader-separation.json) has no threshold satisfying both requirements. Its best negative rejection within the false-abstention limit is 11/13 (84.6%). That exploration did not replace the predeclared zero threshold used in the generation experiment.

The experimental `--answerability-reader MODEL_DIRECTORY` evaluation option runs this decision **before** the existing Retrieve → Agent → Response graph generates an answer. A rejection supplies zero evidence and triggers the existing standard abstention without calling the answer model. A classifier failure fails the evaluation row; it cannot silently allow generation. Tests assert zero model calls for rejection and classifier errors. This option is not wired into the production gateway or exposed as an enabled user setting.

The expanded baseline (`answerability-scored-generation.json`) completes all 53 rows: substring match .909, unanswerable abstention .45, false abstention 0, citation rate 1.0, and zero errors. First-attempt contract compliance is 44/53 (.830), with nine successful repairs. The gate rejects both safety and first-attempt compliance. All 212 saved passages contain real retrieval scores and reranker logits.

The [paired generation comparison](../evals/results/answerability-generation-comparison.json) is complete. Both runs use the same corpus, retrieval settings, answer model, prompt, contract, and unchanged gates. These are single local runs, not confidence intervals or causal proof for every changed answer.

| Measure | Baseline | Experimental reader at margin 0 |
| --- | ---: | ---: |
| Expected-answer substring match | 30/33 (.909) | 28/33 (.848) |
| Unanswerable abstention | 9/20 (.45) | 15/20 (.75) |
| False abstention | 0/33 | 2/33 (.061) |
| Citation rate on answerable questions | 1.000 | .939 |
| First-attempt contract compliance among model calls | 44/53 (.830) | 36/38 (.947) |
| Model calls skipped before generation | 0 | 15 |
| Generation errors | 0 | 0 |
| Gate exit | 1 | 1 |
| Failed checks | Safety, contract compliance | Safety, substring match |

The guard skips 13 unanswerable and two answerable questions. Two additional negative questions are refused by the answering model, yielding 15/20 overall abstentions. All 15 pre-generation rejections have `contract_compliance: not_called`, zero actual generation evidence/citations, and preserved scored diagnostic passages. The improved compliance rate has a different denominator: skipped questions are excluded, so it is not evidence that the model itself became more reliable.

Five unsupported answers survive: forklift capacity, federal vacation, 2030 mileage, 2027 mileage, and an individual driver's remaining hours. The two false abstentions concern password-composition rules and the driving-break interval. The guard therefore remains **disabled in production**. The required ≥.9 safety and ≥.879 quality outcome has **not** been achieved. No threshold was weakened or model/prompt changed to manufacture acceptance.

Substring scoring checks expected text rather than semantic correctness; citation scoring checks source documents rather than entailment. Abstention is the runtime decision, not proof of whether that decision is warranted. The expanded labels have not received independent human review.

## IRS diagnosis

The [two-row diagnosis](irs-paraphrase-diagnosis.md) identifies the exact page 43 safe-harbour bullets and the competing page 42/46 generic passages. In the preferred historical candidate the 120-day bullet was separated from its stem, whereas the 60-day bullet already had the stem. Current chunking contains the stem plus all four bullets in a single 760-character chunk, but both paraphrases still miss it. Saved top-four evidence cannot alone distinguish candidate-generation loss from reranking loss. No further retrieval tuning was applied in this follow-up.

## Retired claim judge and NLI calibration

Both legacy evaluation CLIs now reject selecting `llama3.1` as the claim judge and direct users to `scripts.eval_nli`. Historical artifacts and low-level validation tests remain available; no more llama3.1 judge iteration occurred.

The [NLI experiment](nli-calibration.md) uses pinned, checksummed local weights and the same unchanged 24 labels. It excludes labels/rationales from model input, requires complete inputs, and rejects oversized pairs rather than silently truncating.

| NLI calibration measure | Result |
| --- | ---: |
| Valid classifications | 24/24 (100%) |
| Three-class agreement | 8/24 (33.3%) |
| False acceptance | 2/11 (18.2%) |
| False rejection | 11/13 (84.6%) |

Validity meets the requested prerequisite; semantic performance does not justify influence. This is a whole-answer question/answer adapter, not a validated atomic-claim extractor. Its poor result does not establish that every NLI model or representation will behave the same. It remains offline. The original labels still require independent human review. No paid API calls were made.

## Reproduction

```bash
.venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions-expanded.json --modes similarity,keyword,hybrid,rrf --chunking section --candidate-k 50 --reranker local_cross_encoder --out evals/results/answerability-scores-repeat.json
.venv/bin/python -m scripts.analyze_answerability evals/results/answerability-scores-repeat.json --out evals/results/answerability-separation-repeat.json

# Install local reader weights explicitly, then compare unchanged generation with/without the experimental guard.
.venv/bin/python -m scripts.install_answerability --help
.venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions-expanded.json --modes hybrid --chunking section --candidate-k 50 --reranker local_cross_encoder --generate llama3.1:latest --generate-mode hybrid --answerability-reader /tmp/agentic-answerability-reader --out evals/results/answerability-guard-repeat.json
.venv/bin/python -m scripts.check_grounding_eval evals/results/answerability-guard-repeat.json --expected-count 53
```

The `/tmp` path above is the model cache used for this local experiment; use the path chosen during installation on another machine. Run model experiments separately to avoid resource contention. An exit 1 from the checker is a real rejection, not a command crash.

## Remaining boundaries

This work does not resolve login throttling/open registration, ChromaDB dependency CVEs, or run-record scale items. Those remain separate backlog items. A safe production answerability policy is still unproven; no failed classifier or tuned threshold was enabled to make the report appear complete.

## Verification

The full backend suite passes: **270 tests**, five dependency warnings. `git diff --check` passes. Regression coverage includes gate exit codes, score persistence, annotation exclusion, split-aware threshold analysis, retired-judge CLI rejection, bounded local classifiers, and pre-generation model-call suppression on reader rejection or error. No frontend code changed in this follow-up.
