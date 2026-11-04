# Grounding and integrity verification — September 18, 2026

The requested code changes are implemented in the working tree. **Batch A is not accepted for release:** the real-document answer substring target is missed. No commit or push was performed. Database integrity and workflow compatibility changes pass their regression tests. Hosted CI and live PostgreSQL validation remain unverified.

## Implementation audit

| Request | Implementation | Verification / status |
| --- | --- | --- |
| 1. Grounded contract | `agent_runtime.finalize_grounded_answer` validates the four-field JSON object, evidence-scoped labels, empty abstentions, and supported-answer shape. One shared repair helper permits exactly one repair. Agent and Query share the helper. Phrase-sniffing was removed. `grounding` output exposes reason and compliance. | Hedged partial answer preserved; invalid JSON or missing/unknown citations repair then fail; trailing guesses cannot escape through the answer field; clean abstentions cite nothing. **Model quality gate failed**, below. |
| 2. Empty evidence | Grounding follows envelopes, retrieval attachments, and evidence/context returned by tools or specialists, including empty results. Empty evidence can only produce an abstention. Its displayed reason is deterministic to prevent guesses in that field. Response always checks labels. | P3, empty retrieval mid-loop, empty-registry Response, and reason-field guess tests pass. |
| 3. Citation grammar | Grouped labels expand to canonical `[S1][S2]`; invalid groups become unsupported references. The frontend accepts the server's positive-integer label domain, including S120. | Individual, spaced, grouped, duplicate, mixed, unknown, and unrelated-bracket cases pass. Backend retrieval/response retains 120 passages; frontend parsing and the real SourcePanel server-render test retain all 120 source cards and links. |
| 16. README | Metrics use substring/document-match terminology with limitations beside the figures; baseline and candidate results are identified separately. | The harness's `citation_document_match` measures answers whose cited documents all match, not individual passages. README preserves that exact denominator. |
| 4. Retired exports | Model accepts `store`; graph splitting excludes it from tool/specialist execution and reports the retired VectorDB migration error. Save/import reject legacy nodes actionably. | Representative pre-removal JSON fixture, API validation/save/run tests, and frontend import test pass. The fixture is constructed, not a recovered user backup. |
| 5. KB names | `name_key` column and database unique index `(tenant_id, name_key)`. Trim, collapse whitespace, casefold. Create/rename populate it; startup migrates restored legacy data. RPC and gateway preserve 409 and submitted name. | Case/whitespace/Unicode variants, tenant separation, direct DB bypass rejection, concurrent creates/renames, rename rollback, migration, and HTTP conflicts pass on SQLite. PostgreSQL advisory-lock path is implemented but not exercised against a live PostgreSQL server. |
| 6. Workflow concurrency | Atomic SQL compare-and-swap on tenant, ID, and `updated_at`. Missing/stale versions return 409 with current server document. Editor tracks versions and preserves edits, offering export/reload/keep editing. | Fresh/stale/missing/invalid tokens, frozen clock, concurrent PUT winner, and API error preservation tests pass. |
| D. CI | GitHub Actions runs backend tests plus frontend tests/typecheck/lint/build on push and PR. A separate local evaluation checker exits nonzero on unmet model gates. | Local commands pass. First GitHub-hosted run is pending. |

## Real-document evaluation

Same corpus and settings as the supplied baseline: 38 questions (33 answerable, 5 unanswerable), 4 documents, 2,923 chunks, `embeddinggemma:latest`, paragraph chunks of 800 characters with 120 overlap, hybrid search, top 4, candidate pool 20, `llama3.1:latest`, temperature 0, maximum 400 generated tokens. No expected answers, questions, or scoring rules were changed.

| Metric | User baseline | First candidate | Final candidate | Required |
| --- | ---: | ---: | ---: | ---: |
| First-attempt contract compliance | Not measured | 35/38 (92.1%) | 36/38 (94.7%) | ≥90% |
| Valid after one repair | Not measured | 2/38 (5.3%) | 2/38 (5.3%) | Report |
| Failed contract | Not measured | 1/38 (2.6%) | 0/38 | Report |
| Answer substring match | 29/33 (87.9%) | 28/33 (84.8%) | **27/33 (81.8%)** | ≥87.9% |
| False abstention | 3/33 (9.1%) | 0/33 | 0/33 | ≤9.1% |
| Citation rate on answerable questions | 26/33 (78.8%) | 33/33 (100%) | 33/33 (100%) | ≥84.8% |
| Uncited answerable rows | 7 | 0 | 0 | ≤5 |
| Citation document match | 96.2% | 100% | 100% | Report |
| Abstention on unanswerable questions | 5/5 | 3/5 | **3/5** | Report |
| Generation errors | 0 | 1 | 0 | 0 |

The final candidate removed conflicting prose instructions and clarified empty fields for abstention, and required matching the passage's subject/timeframe/condition. Compliance improved, but factual quality did not. Both runs are retained, including the regression; no best-run-only result is published.

Four final substring misses lacked the full expected answer in retrieval. Two missed even with the answer retrieved: the model answered **80 hours instead of 70 hours**, and **8 hours instead of 34 hours**. It also answered two unanswerable questions using unrelated numbers: forklift maximum load and federal paid vacation. Existing passage labels and matching documents do not prove that a claim follows from the text. This needs separate claim-support evaluation and model comparison before adoption; weakening the gate or restoring phrase-sniffing would hide the problem.

Limitations: substring scoring does not prove semantic correctness; citation scoring checks document identity, not entailment; machine-readable abstention expresses the model's decision, not whether that decision is warranted. Legacy baseline abstention is phrase-based. This is one small corpus evaluated locally, not production validation.

Artifacts:

- [Supplied baseline](../evals/results/real-generation-recheck-2026-09-18.json)
- [First candidate](../evals/results/real-generation-grounded-contract-2026-09-18.json)
- [Final candidate](../evals/results/real-generation-grounded-contract-final-2026-09-18.json)

## Reproduce

```bash
.venv/bin/python -m pytest backend/tests -q
cd frontend
npm test
npm run typecheck
npm run lint
npm run build
cd ..
.venv/bin/python -m scripts.eval_retrieval --corpus evals/real --questions evals/real/questions.json --modes hybrid --generate llama3.1:latest --generate-mode hybrid --out evals/results/real-generation-candidate.json
.venv/bin/python -m scripts.check_grounding_eval evals/results/real-generation-candidate.json
```

Local result: **221 backend tests, 24 frontend tests**, TypeScript, lint, and production build pass. Backend emits five dependency deprecation warnings; the build notes that some routes cannot be statically classified. The saved final evaluation checker returns **exit 1**, specifically for `answer_substring_match_rate`. It is intentionally not marked release-ready.

## Migration cautions

Existing duplicate KB names cause startup migration to stop with the tenant, normalized name, and IDs. Back up the management database and assign distinct `state.name` values to those active records before restarting. The migration never deletes documents or silently renames a KB. Deleted names are reusable. There is no independent KB-import endpoint; restored legacy databases go through migration.

Old workflow clients must send `updated_at` on PUT. The request remains the workflow fields plus that token, not a nested `workflow` object. Response 409 contains the current document but does not overwrite local edits. The browser conflict dialog uses the existing accessible native dialog; automated API and state tests were run, but a live multi-tab browser session was not exercised.
