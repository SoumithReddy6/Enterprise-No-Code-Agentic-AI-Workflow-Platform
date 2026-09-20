# Answerability safety implementation plan

**Goal:** Make evaluation inputs and retrieval scores trustworthy, measure answerability on more negatives, diagnose IRS misses, and replace the failed offline JSON judge with measured local NLI classification.

**Spec:** User's pasted review request, sequence 2 → 1.1 → 1.2/1.3 → 4 → 3. No weakened abstention gate, no paid model calls. Preserve historical artifacts and use f11-after as the preferred historical candidate (it already used reranking). Existing uncommitted changes move intact onto codex/retrieval-answerability-safety.

**Architecture:** Keep retrieval relevance telemetry separate from canonical document provenance and from answer support. Persist real search and reranker scores, never substitute zero for absent data. Validate report structure before calculating gates. Measure thresholds before adding a runtime decision; if relevance cannot distinguish answerability, evaluate a separate deterministic question/evidence classification pass. Model/threshold provenance, coverage, and unsafe acceptance must be reported. NLI remains offline until calibration establishes usefulness, not merely well-formed scores.

- [x] Gate: add subprocess tests for exit 0 accepted, 1 rejected, 2 invalid/missing/retrieval-only report; validate scalar ranges and shapes in scripts/check_grounding_eval.py. Preserve existing threshold values and default 38-case completeness; expose explicit expected count for expanded suites.
- [x] Scores: test nonzero score survival through real retrieval, provenance validation, node evidence and saved generation rows; preserve reranker logits as separate telemetry, with finite-value checks. Do not fabricate historical scores.
- [x] Dataset: add 15 independently described unanswerable questions in a separate expanded suite, keeping the original 38 and recording rationale. Inspect the corpus rather than assume missing facts. Separate exploratory calibration from held-out negatives.
- [x] Measurement: run real retrieval with score persistence, summarize answerable/unanswerable distributions and threshold tradeoffs. Do not select a threshold from a claimed zero-score artifact. If there is no acceptable separation, implement/evaluate an explicit deterministic question/evidence answerability step without changing the answer prompt or pretending relevance establishes support.
- [x] IRS: inspect existing saved passages and exact current bullet placement; report two-row diagnosis before any ranking tuning.
- [x] Judge: retire the llama3.1 evaluation default; evaluate a pinned local NLI classifier on the existing 24 annotations. Report valid-output coverage, false acceptance/rejection and annotation limitations; no runtime/gate wiring merely because the output contract is valid.
- [x] Verify tests, rerun generation for the final candidate when warranted, preserve failed gates, document exact results and remaining work. Do not commit or push unrelated work.

Measured safety acceptance remains unmet. Experimental classifiers are disabled in production; completion of implementation/measurement is not release approval. See `docs/answerability-safety-report.md` for the exact failing gates.
