# F11 retrieval and claim-judge calibration

## Decision

The retrieval implementation is available as an opt-in experiment, **not an accepted quality release**. The final measured candidate retrieves expected answer text for 30/33 answerable questions (90.9%), below the requested 95%. Generation substring matches improve to 30/33 (90.9%), but only 2/5 unanswerable questions are rejected. The strengthened release gate rejects the candidate.

Generation uses the same `llama3.1:latest`, prompt, structured contract, temperature 0, and 400-token limit throughout. Embeddings remain `embeddinggemma:latest`. No existing quality threshold was lowered. The only gate addition is the requested ≥90% abstention rate on unanswerable questions. A separate MiniLM cross-encoder scores retrieval candidates; it does not generate answers or judge claims.

## Implemented changes

- Section-aware paragraph chunking preserves heading paths in metadata and prepends them to embedded, keyword-indexed, and returned text. Inline headings, cross-page headings, list items, and short regulatory sibling clauses receive explicit handling.
- Search optionally reranks up to 50 candidates before returning top four in these experiments. All four search modes, KB defaults, node configuration, and the evaluation CLI support the option. Missing local weights produce an actionable error; search never downloads weights implicitly.
- The search database migrates existing chunks to an empty heading path without changing their canonical source identity. Rebuilding is required to add structural metadata to existing documents.
- Saved generation rows include every retrieved passage's citation, filename, page, and full text, including uncited distractors. Offline re-analysis no longer needs Ollama or retrieval replay.
- The release gate now checks `heuristic_abstention_on_unanswerable >= 0.9`. The pre-change candidate fails both substring match and abstention checks; the final retrieval candidate fails abstention.
- A frozen, assistant-annotated 24-answer calibration set includes the four prior generation failures. Judge calibration remains separate from runtime and release acceptance.

Configuration, architecture, model installation, and commands: [section-aware retrieval](section-retrieval.md).

## Controlled settings and artifacts

All runs use the same four documents, 38 questions (33 answerable, five unanswerable), character size 800, overlap 120, top-k four, and hybrid retrieval for generation. All four modes are evaluated separately for retrieval.

| Run | Chunking | Chunks | Candidate pool | Reranker | Artifact |
| --- | --- | ---: | ---: | --- | --- |
| Fresh before | Paragraph | 2,923 | 20 | None | [f11-before.json](../evals/results/f11-before.json) |
| First after | Section | 2,176 | 50 | Local cross-encoder | [f11-after.json](../evals/results/f11-after.json) |
| Boundary correction | Section | 1,632 | 50 | Local cross-encoder | [f11-after-boundaries.json](../evals/results/f11-after-boundaries.json) |
| Final reviewed scope fix | Section | 1,632 | 50 | Local cross-encoder | [f11-after-review.json](../evals/results/f11-after-review.json) |

The before artifact predates CLI fields for candidate pool/reranker; its code used a hardcoded pool of 20 and no reranking. The second implementation corrects inline heading boundaries and packs related regulatory clauses; earlier results are retained rather than hidden. A separate code review then found ambiguous alphabetic/Roman markers inheriting the preceding sibling’s scope. The conservative fix drops uncertain ancestors; two additional regression tests and the final full evaluation cover that change. The reviewer confirmed the concrete issue was resolved. This is a before/after bundle, not an ablation proving which component caused each improvement. Changes were inspected against this development corpus; an untouched holdout is still needed.

## Retrieval results

`answer@4` checks expected answer substrings in retrieved text. It does not prove that the complete governing rule or all exceptions were retrieved.

| Mode | Before | First after | Boundary correction | Final reviewed |
| --- | ---: | ---: | ---: | ---: |
| similarity | 81.8% | 87.9% | 90.9% | 90.9% |
| keyword | 81.8% | 87.9% | 90.9% | 90.9% |
| hybrid | 87.9% | 87.9% | 90.9% | 90.9% |
| rrf | 90.9% | 87.9% | 90.9% | 90.9% |

The fresh baseline does not reproduce 87.9% for every mode: RRF already reaches 90.9%. The final hybrid configuration gains one net answer-bearing retrieval, while RRF is unchanged overall.

| Named F11 miss | Before hybrid | Final hybrid |
| --- | --- | --- |
| IRS return excess within 120 days, paraphrase | Miss | Still missing |
| IRS account within 60 days, paraphrase | Miss | Still missing |
| NIST password-hint prohibition | Miss | Recovered |
| §395.3 eleven-hour driving limit | Miss | Recovered |

The final run additionally misses the expected NIST password-composition passage for “force users to include a digit and a symbol.” The model nevertheless returns a matching “No” answer; that does not make the missing retrieval successful. The IRS reasonable-period definition and its numerical bullets now share a chunk, but the reranker still favors other relevant reimbursement passages for the two paraphrases. Structural repair alone does not resolve their ranking.

## Generation and safety

| Metric | Before | First after | Boundary correction | Final reviewed |
| --- | ---: | ---: | ---: | ---: |
| Answer substring match | 81.8% | 90.9% | 93.9% | 90.9% |
| Expected answer retrieved | 87.9% | 87.9% | 90.9% | 90.9% |
| Substring match when answer retrieved | 93.1% | 96.6% | 100.0% | 96.7% |
| False abstention on answerable questions | 0.0% | 0.0% | 0.0% | 0.0% |
| Abstention on unanswerable questions | 60.0% | 60.0% | 40.0% | 40.0% |
| Citation rate on answerable questions | 100.0% | 100.0% | 100.0% | 100.0% |
| Citation document match | 100.0% | 100.0% | 100.0% | 100.0% |
| First-attempt contract compliance | 36/38 (94.7%) | 35/38 (92.1%) | 36/38 (94.7%) | 36/38 (94.7%) |
| Generation errors | 0 | 0 | 0 | 0 |

**Limitations:** substring matching is not semantic accuracy; document matching is not claim entailment; runtime abstention flags identify decisions, not whether those decisions are correct. The unanswerable set has only five questions, so one answer changes its rate by 20 percentage points. Single runs do not establish statistical significance. Timing is not a controlled benchmark: an initial calibration attempt briefly overlapped the second retrieval run and was cancelled; the completed calibration and final reviewed evaluation run separately.

The final reviewed run matches 30/33 expected answer substrings, compared with 27/33 before. The driver weekly-clock restart answer regresses from “34 or more consecutive hours” in the boundary experiment to “More than 15 hours following 8 consecutive hours off-duty,” although an expected passage is retrieved. This is a generation failure with evidence available, distinct from the two IRS retrieval misses. The generation model and prompt are unchanged.

Final unsafe answers to unanswerable questions remain visible in the artifact:

- A general forklift load-limit question receives “2,400 pounds,” taken from a particular load-center example.
- Annual paid vacation receives “21/2 days,” taken from a travel meal-allowance example.
- The future 2030 mileage rate receives the 2025 rate of $0.70.

All three have valid source labels. Citation validity cannot establish that a passage answers the question. `check_grounding_eval` reports `accepted: false`, with `abstention_on_unanswerable: false`; every other existing check passes. The requested ≥95% retrieval target also remains unmet and is reported separately from that generation gate. The [saved gate comparison](../evals/results/f11-gate-comparison.json) records checks for all four runs.

## Claim-judge calibration

The [24-case dataset](../evals/claim-calibration-labelled.json) contains 13 supported, five contradicted, and six insufficient-evidence annotations with rationales. Sixteen answers are from the fresh before run; eight are counterfactual negative examples paired with unchanged real passages. Labels were frozen before the judge ran. These are **assistant annotations, not independently human-reviewed ground truth**. A [readable annotation review sheet](claim-judge-label-review.md) lists each question, answer, label, and rationale.

The four prior failures are included: the 80-hour rule, eight-hour restart, 30,000-pound forklift limit, and 2½-day vacation answer. The isolated passage literally supports “80 hours,” while omitting its limiting context; that case is labelled textually supported, with the scope failure documented. A claim-support judge cannot reconstruct missing context or establish overall answer correctness. The existing judge receives answer and passages, not the original question; that is especially limiting for bare numerical answers.

The [completed calibration artifact](../evals/results/claim-judge-labelled-calibration.json) records the unchanged `llama3.1:latest` judge against all 24 cases, run separately from generation:

| Calibration measure | Result |
| --- | ---: |
| Valid judgments / contract compliance | 5/24 (20.8%) |
| Invalid judgments | 19/24 |
| Supported-label coverage | 1/13 (7.7%) |
| Negative-label coverage | 4/11 (36.4%) |
| False acceptance among valid negative judgments | 0/4 (0%) |
| False rejection among valid supported judgments | 0/1 (0%) |

The 19 invalid outputs comprise ten non-exact or duplicate claim quotes, five malformed JSON responses, three invalid rationales, and one invalid claim shape. All four prior batch failures produced invalid judgments; their claim support was **not successfully assessed**. Only one naturally generated answer produced a valid judgment; the other four valid judgments were counterfactual examples.

**Finding: the current judge is not reliable enough for a reported quality guarantee or a blocking rule.** Zero observed false acceptance/rejection on this tiny valid subset is not evidence of safety. Invalid output and empty-claim results remain unjudged, never passes. Conditional rates must be read with coverage. The aborted overlapping attempt is excluded from this completed calibration. Independent human review of the annotations is still pending. Evaluating a stronger judge would be a separate future experiment; no model was swapped here.

## Verification and remaining work

Local checks: 236 backend tests passed (five dependency deprecation warnings); 24 frontend tests, TypeScript checking, lint, and production build passed. Regression coverage includes heading/list boundaries (including ambiguous alphabetic/Roman markers), metadata migration, filtering, reranking before top-k, document retirement during reranking, safety-gate failure, passage persistence, and calibration denominator handling. Local checks do not establish hosted CI success.

Do not promote this retrieval configuration as meeting the requested gate. The next retrieval investigation is candidate/reranker discrimination for the remaining IRS paraphrases and the NIST composition rule, followed by a holdout evaluation. The three scope/time-related unsafe answers remain explicit safety failures. Reliable calibration results and independent annotation review must precede any decision to use judge output as a reported quality signal; it is not wired into execution or acceptance.
