# Experimental extractive answerability reader

This local diagnostic scores a question and retrieved passages **before answer generation**. It is independent of the NLI answer-support experiment. It neither calls a generative model nor adds another answer prompt. The module is not wired into any runtime gateway or gate; a valid numeric output does not establish safe answerability classification.

## Model and reproducibility

Base model: [deepset/tinyroberta-squad2](https://huggingface.co/deepset/tinyroberta-squad2), trained for extractive question answering with SQuAD 2.0 including unanswerable examples. The [ONNX Community conversion](https://huggingface.co/onnx-community/tinyroberta-squad2-ONNX) explicitly identifies that base model. It is a community export, not an original deepset ONNX publication. The selected revision is `7c9f69b7e6228375169a4553bcfa6639152e3a69`, artifact `onnx/model_quantized.onnx` (82,141,214 bytes). The installer and reader pin and verify SHA-256 hashes for weights, tokenizer and configuration. No remote model code is executed. License: CC-BY-4.0; attribution belongs to the linked model publishers.

```sh
.venv/bin/python -m scripts.install_answerability --model-dir /tmp/agentic-answerability-reader
.venv/bin/python -m scripts.eval_answerability_reader evals/results/answerability-scored-retrieval.json --model-dir /tmp/agentic-answerability-reader --out evals/results/answerability-reader.json
.venv/bin/python -m unittest backend.tests.test_answerability_reader
```

Only the explicit installer uses network. Inference uses ONNX Runtime CPU, one intra-op/inter-op thread, sequential execution. Tested dependencies: onnxruntime 1.30.0, tokenizers 0.23.2. The cache is temporary and may require reinstalling later. No dependency or application configuration changes are made by these scripts.

## Predeclared protocol

- Input: question plus each retrieved passage's entire text. Annotation kind/split/expected facts, retrieval scores, NLI labels, and generated answers are excluded from model inputs.
- Output mapping: named `start_logits` and `end_logits` arrays; CLS/BOS token ID 0 at index 0. This is a span predictor, not a three-class classifier; there is no classification `id2label` mapping to infer.
- Legal candidates: context tokens only, start <= end, at most 30 tokens. Question and special-token positions cannot form candidate answers. Exact text and character offsets are retained for every window's best span.
- Per-window margin = maximum legal span (start logit + end logit) minus CLS (start logit + end logit). The overall margin is the maximum per-window margin across all passages. **Accept only when margin > 0**, a baseline fixed before observing evaluation results; a zero tie abstains. This is not a calibrated probability.
- Full untruncated tokenization is divided explicitly into 384-token windows, retaining question and special tokens in every window with 128 context-token overlap. Original token IDs and character offsets are preserved. The adapter processes every window. It does not rely on tokenizer overflow enumeration: a regression exposed incomplete overflow output in the installed tokenizer version on a 1,001-token synthetic context.
- Questions above 64 tokens, more than 32 passages, more than 100,000 evidence characters, or more than 64 windows per passage are errors; no partial score is accepted. Empty evidence deterministically abstains. Empty/malformed passages, nonfinite or wrong-shaped logits, bad offsets and artifact mismatches fail closed as errors. The evaluator reports errors separately, never as valid rejections or successes.

The implementation is `backend/app/kb/answerability.py`; instantiate `AnswerabilityReader(model_dir)` once and call `.score(question, passages)`. It has no singleton, automatic download, or application startup side effect. A single reader instance is intended for serial use; it mutates tokenizer configuration to enforce no truncation.

## Evidence and limitations

The initial smoke check identified `Paris` in “Alice lives in Paris” with positive margin, and abstained for an absent password with negative margin. A long-context smoke check found the answer in the fifth window, beyond the first 384 tokens. These are plumbing checks, not evidence of deployment suitability.

The reader can select a plausible span even when crucial question conditions are unmet. Maximum aggregation creates more opportunities for a spurious positive as passage/window count grows. Extractive span detection does not prove the full answer, all requested items, document scope, temporal validity, or cross-passage reasoning. Domain-specific tax and security questions differ from SQuAD. Candidate correctness has no independent span annotation in this dataset. A holdout containing only negatives cannot estimate unseen-positive false abstention.

## Actual clean-corpus evaluation

The saved report is `evals/results/answerability-reader.json`, using the clean `hybrid` retrieval rows from `evals/results/answerability-scored-retrieval.json` (source SHA-256 retained in the report). All 53 rows, 212 passage windows, were scored on CPU in 6.190 seconds. No errors or truncation occurred. The fixed threshold remained zero; no threshold was selected from evaluation labels.

| Split | Valid | Answerable false abstentions | Unanswerable rejections | Unanswerable false accepts |
| --- | --- | --- | --- | --- |
| Calibration | 46/46 | 2/33 (6.06%) | 8/13 (61.54%) | 5/13 (38.46%) |
| Holdout | 7/7 | Unmeasured: no positives | 5/7 (71.43%) | 2/7 (28.57%) |
| Total | 53/53 | 2/33 (6.06%) | 13/20 (65.00%) | 7/20 (35.00%) |

The two held-out false accepts expose semantic limitations: a driver's personal remaining hours is answered with the generic span `15`, and a mandated commercial password-manager product is answered with `Authenticator Management`. Calibration false accepts include a generic maximum forklift load (`2,400 pounds`), paid vacation (`4`), future mileage rates (the 2025 rate), and an exact bcrypt work factor (an identifier fragment `0000-0001-6195-0331`). Extractability is plainly insufficient to establish answerability.

**Keep disabled.** Although false abstention is 6.06% on the known positives, the reader falls below the requested 90% rejection target on both calibration and held-out negatives. No runtime confidence claim or scoring influence is justified. Threshold selection on these observed results would require a separate calibration procedure and further independent holdout evidence, including positives. This experiment does not change runtime behavior.
