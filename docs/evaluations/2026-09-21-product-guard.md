# Opt-in product answerability guard

The product setting is `answerability_guard: false` by default on Retrieve and Query. The operator provides `ANSWERABILITY_MODEL_DIR`; no user-controlled filesystem path enters workflow configuration. The runtime verifies the pinned manifest and SHA256 files before loading, serializes tokenizer access, and scores before registering the accepted evidence. A refusal removes evidence and short-circuits Agent/Query generation through the existing no-evidence contract, including attached retrieval tools. Generation nodes depending on a rejected retrieval through a prompt template also refuse.

Missing weights, integrity failures and reader failures skip gracefully with distinct reason codes; invalid weights never reach an inference session. Each failure reason warns once per process. Every attempted check logs a content-free decision. A transient Retrieve/Query decision event is persisted before generation, so later generation failure does not erase it. Compact SQL decision rows populate operator metrics and exclude cached replay. As with all journals, loss of a worker lease or a crash before event persistence can leave an attempt unrecorded.

The existing schema-driven editor exposes the checkbox and displays skipped-check/rejection notices. Existing workflows retain their prior behavior until explicitly enabled. This is an opt-in heuristic with known false acceptance/abstention, not semantic verification.

The five known unanswerable failures have analyst reason tags in `questions-expanded.json`; only their questions enter model prompts. Two have temporal mismatch, one generalizes a load-center example, one selects an unrelated travel-allowance number, and one requires driver-specific history absent from the corpus. Tags are assistant analysis, not independent human labels.

## Verification plan

Run the full backend suite, frontend checks and independent review. Then run all 53 questions using `--product-answerability-guard`, with `--answerability-reader` absent, fixed existing local models, and the unchanged gate committed in `33f7da4`. Record source/input hashes, model digests, reader manifest, command, exit codes, decisions (including skips), passage texts and gate output. Do not infer a product-path pass from the historical eval-only artifact.

## Fresh product-path result

**All nine unchanged release-gate checks passed** on 53 questions. Product decisions: **38 allow, 15 abstain, 0 skip**. The historical `--answerability-reader` wrapper was absent; the Retrieve node's `answerability_guard` config was true.

| Metric | Result |
|---|---:|
| Answer substring match | 0.848 (28/33) |
| Abstention on unanswerable | 0.750 (15/20) |
| False abstention | 0.061 (2/33) |
| Citation rate | 0.939 (31/33) |
| First-attempt contract compliance | 0.947 (36/38 calls) |
| Generation errors | 0 |

Per-question pass/abstention/retrieval/compliance outcomes match the prior eval-only guarded run. Fifteen refusal strings changed to name the actual answerability reason; their grounding metadata now identifies `abstention_source`. The five remaining false acceptances are unchanged. These remain heuristic scores on a calibration corpus, not a promise of 75% abstention on arbitrary documents.

- [Fresh report, passage texts and runtime decisions](../../evals/results/product-guard-fresh-2026-09-21.json)
- [Accepted gate output](../../evals/results/product-guard-fresh-2026-09-21-check.json)
- [Commit, source/input hashes, model digests and execution provenance](../../evals/results/product-guard-fresh-2026-09-21-provenance.json)
- [Comparison and remaining failures](../../evals/results/product-guard-fresh-2026-09-21-comparison.json)

Verification: **383 backend tests**, **26 frontend tests**, frontend typecheck/lint/build passed. Independent review found malformed-metadata and transformed-envelope bypasses; both were fixed and regression-tested before generation. Evaluated source files did not change during the run and the gate still matches calibration commit `33f7da4`. The working tree contains uncommitted changes, explicitly recorded in provenance. Existing running services were not restarted, and no workflow is silently opted in.
