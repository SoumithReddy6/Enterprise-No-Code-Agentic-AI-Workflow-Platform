# Offline NLI calibration

The generative `llama3.1` claim judge should be retired from scoring decisions. This independent local NLI experiment produces valid outputs reliably, but **is not accurate enough to influence answer scores or gates**. No application/runtime/gate code imports it. Passing the requested 90% valid-output prerequisite is necessary, not sufficient, for future consideration.

## Reproduce

From the repository root with the existing virtual environment (tested with onnxruntime 1.30.0, tokenizers 0.23.2):

```sh
.venv/bin/python -m scripts.install_nli --model-dir /tmp/agentic-nli-minilm
.venv/bin/python -m scripts.eval_nli evals/claim-calibration-labelled.json --model-dir /tmp/agentic-nli-minilm --out evals/results/nli-labelled-calibration.json
.venv/bin/python -m unittest backend.tests.test_nli_judge
```

Only installation downloads artifacts. Inference uses one ONNX CPU session with one intra-op/inter-op thread, sequential execution, no Ollama generation, paid calls, or network. The temporary model cache may be removed by the operating system; install again if needed. The selected quantized artifact targets ARM64 CPU; this calibration does not establish equivalent results on other architectures.

## Pinned artifacts and semantics

Official publisher: [cross-encoder/nli-MiniLM2-L6-H768](https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768). Revision `b95119ce93d3e065de6214e38cd4a97b0f2f2c6d`; [official ONNX artifacts](https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768/tree/b95119ce93d3e065de6214e38cd4a97b0f2f2c6d/onnx). The installer pins `onnx/model_qint8_arm64.onnx`, `tokenizer.json`, and `config.json`, and verifies hardcoded SHA-256 values; the evaluator rechecks both manifest and local bytes. The saved report includes all three hashes and dataset SHA-256. The [pinned configuration](https://huggingface.co/cross-encoder/nli-MiniLM2-L6-H768/blob/b95119ce93d3e065de6214e38cd4a97b0f2f2c6d/config.json) maps output 0=contradiction, 1=entailment, 2=neutral, mapped here to contradicted/supported/insufficient_evidence. Softmax scores are model scores, not calibrated truth probabilities.

Inputs contain only question, answer and explicitly cited passage text. Expected labels and annotation rationales never enter inference. The dataset's original 24 frozen assistant labels remain unchanged (13 supported, 5 contradicted, 6 insufficient evidence). These are not independently human-reviewed gold labels.

Each cited passage is the premise; the full citation-stripped answer plus `Question: ...` context is the hypothesis. This avoids dropping uncited answer sentences, but **does not extract atomic claims or prove completeness**. Question concatenation preserves fragment context without inventing a declarative rewrite; an NLI model trained on declarative sentence pairs may nevertheless handle this badly. Across passages, any contradiction wins; otherwise any entailment yields supported, otherwise neutral. This conservative conflict rule can reject correct answers; the method cannot combine multi-hop evidence and does not verify sentence-level citation attachment. Missing citations, malformed input, duplicate or unknown references, nonfinite/wrong-shaped logits, oversized input or >512-token pairs produce errors, never passes. Tokenizer truncation is explicitly disabled. A single overlength pair makes the entire answer unjudged; partial evidence is never reported as a complete judgment. The 50,000-character input bound covers cited text and hypothesis.

## Actual frozen-label results

Saved in `evals/results/nli-labelled-calibration.json`. One bounded run, with no label-driven template tuning:

| Measure | Result |
| --- | --- |
| Valid judgments | 24/24 (100%) |
| Errors | 0 |
| Supported/contradicted/insufficient label coverage | 100% each |
| Three-class agreement | 8/24 (33.33%) |
| False accepts among valid negative labels | 2/11 (18.18%) |
| False rejects among valid supported labels | 11/13 (84.62%) |
| Runtime influence permitted | No |

Confusion matrix; rows expected, columns predicted:

| Expected | Supported | Contradicted | Insufficient evidence |
| --- | --- | --- | --- |
| Supported | 2 | 1 | 10 |
| Contradicted | 1 | 1 | 3 |
| Insufficient evidence | 1 | 0 | 5 |

False accepts are `failure-24` and `counter-return`. Most supported cases become neutral, consistent with poor fit between fragment/Q&A hypotheses and the classifier's sentence-pair task; this is a diagnosis hypothesis, not a proven causal attribution. All 24 answers and their full cited passages were processed without token truncation, but completeness of atomic claims is not measured. Scope omitted from the saved evidence remains unknowable to this evaluator (including the separately documented scope-stripped case `failure-23`). This tiny diagnostic set, including counterfactuals, does not represent deployment prevalence. Do not promote this adapter on the strength of valid outputs. A future independently reviewed evaluation should test a general, label-blind question-to-declarative-claim method and atomic citation coverage before any scoring influence.
