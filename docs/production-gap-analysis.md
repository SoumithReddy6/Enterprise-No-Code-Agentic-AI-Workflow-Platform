# Production gap analysis — 2026-09-16

Every workflow shape Relay supports was executed for real (local `llama3.1:latest`, live knowledge services, Docker for the Python tool, one read-only public HTTP GET), then compared with what production-grade versions of the same workflows require. Reproduce with `.venv/bin/python -m scripts.check_workflows`; the run recorded here used the user's "Resumes" knowledge base, the repo script uses `evals/corpus`.

## 1. What was executed

| # | Shape | Result | Time | What actually happened |
| --- | --- | --- | --- | --- |
| 1 | Chat → Agent → Response | ✓ | 6.9 s | Sensible two-sentence answer |
| 2 | Chat → Retrieve → Agent → Response | ✓ | 10.5 s | Correct degree/school with `[S1]` citations |
| 3 | Chat → Query → Response | ✓ | 14.4 s | Same answer; Query is slower than Retrieve+Agent for the same model |
| 4 | Agent with attached Python tool (Docker) | **✗ run failed** | 2.5 s | Model sent `"15% of 240, then add 17"` to a tool that expects a Python expression; the tool raised `SyntaxError`; **the whole run failed** instead of the error being returned to the model |
| 5 | Agent with Retrieve attached as a tool | ✓ ran, **wrong answer** | 38.2 s | Agent called search, received `{'query','context','sources'}` as a raw dict, and its final answer explained "This is a JSON data format…" instead of answering. On the handbook corpus (`scripts/check_workflows.py`) the same shape answered correctly in 12.9 s — the tool path is unreliable, not broken |
| 6 | Router agent → specialists (summarizer, extraction) | ✓ | 10.3 s | Router delegated to the summarizer; correct one-line summary |
| 7 | Chat → Condition → two Responses | ✓ | 0.0 s | Only the true branch ran |
| 8 | Chat → HTTP tool (GET) → Response | ✓ | 0.2 s | Raw response body returned; no status or schema |
| 9 | Retrieve → Agent(extraction) → Response | ✓ | 8.8 s | Valid JSON with the three requested keys — **by luck; nothing validated it** |
| 10 | Memory agent, two runs | ✓ | 2 s each | Stored and recalled DuckDB; memory is workspace-key scoped, not per user or conversation |
| 11 | Retrieve → Agent(summarizer) → Agent(critic) → Response | ✓ ran, **leaky** | 45.2 s | Critic evaluated *the candidate* rather than *the summary*, and its output cited `[S4]` — a label that reached the response **unverified** because the critic's input was plain text, not an evidence envelope |
| 12 | Manual trigger → Agent → Response | ✓ | 0.6 s | OK |
| 13 | Retrieve with unknown knowledge base | ✓ 422 | — | Rejected at submission |
| 14 | HTTP POST without "allow external writes" | ✓ 422 | — | Rejected at submission |

Strengths confirmed: the durable runtime (queue, leases, checkpoints, resume, transient tool events), the validation and consent gates, tenant isolation, the Docker sandbox, grounded generation on the direct Retrieve → Agent edge, and the router/specialist mechanism.

## 2. Findings from execution

- **F1 — A tool error aborts the run.** Production agent loops return tool errors to the model as observations with a bounded retry budget; the agent decides whether to reformulate, try another tool, or give up. Relay raises and fails the whole run ([agent_runtime.py](../backend/app/agent_runtime.py), the `except Exception` around attached calls).
- **F2 — Tools have no contract the model can see.** The agent is told `{id, name, type, role}` per target and nothing about arguments. The Python tool's expectation ("a Python expression") lived only in the operator's system prompt. Production: every tool has a description and a JSON argument schema; model-supplied arguments are validated before execution; providers' native tool-calling APIs carry that schema. `llama3.1` reports the `tools` capability locally, so this is available without cloud spend.
- **F3 — Tool results are raw dicts.** Retrieval-as-tool returns an envelope-inside-JSON that an 8B model cannot use. Production: observations are rendered for the model (as the direct edge now does) and typed for the runtime.
- **F4 — Grounding is edge-local.** Citation validation, post-generation verification and abstention exist only when a Retrieve node feeds an Agent directly. Evidence gathered via the tool path has no citation contract, and `[S#]` labels pass through downstream agents unverified (scenario 11). Production: evidence is collected run-wide and citations are validated at the response boundary against everything the run retrieved.
- **F5 — Roles are presets, outputs are strings.** Extraction produced JSON because the model felt like it; the critic critiqued the wrong thing. Production: extraction/classification declare a schema and the node fails (or repairs) on invalid output; a critic has a rubric, a target and a stopping rule.
- **F6 — Latency and sequencing.** 7–45 s per workflow on local hardware, no token streaming, every step sequential even when independent (scenario 11 could not parallelise, but a typical "collect evidence from three tools" step could).
- **F7 — Condition is substring matching.** Real routing is a structured classification (schema-validated label) feeding a switch.
- **F8 — HTTP tool output is untyped text.** No status code, headers, size or content-type reach the workflow; downstream nodes cannot branch on failure.
- **F9 — Memory scope.** One conversation log per workspace key; no user, session or workflow scope; no retention.
- **F10 — No operational telemetry.** The workflow runtime has no logging, no token or cost accounting, no correlation IDs, no metrics; run events are the only record and they live in one JSON blob per run.

## 3. Capability comparison

| Capability | Relay today | Production-grade expectation | Gap |
| --- | --- | --- | --- |
| Prompt chaining, routing | Chaining ✓; routing via Condition (substring) or Router agent (model-chosen) | Structured classification → deterministic switch; model routing only where interpretation is needed | Medium |
| Parallelisation, loops, evaluator–optimiser | Rejected by the compiler | Parallel + join with merge rules; bounded loops with rubric, iteration cap, budget | Large (deliberately deferred) |
| Tool use | Six bounded adapters, Docker sandbox, SSRF pinning ✓; JSON action protocol over chat; no schemas; errors abort | Native tool calling with schemas; validated arguments; error observations; per-tool timeouts and retry budgets | Large |
| Data contracts | String ports; JSON envelope by convention | Declared input/output schemas; auto-mapping of compatible fields; runtime validation | Large |
| Grounding | Citation validation on one edge ✓; abstention ✓; source verification ✓ | Run-wide evidence collection; validation at response boundary; document-level authorization before passages reach the model | Medium |
| Human approval | Node-level `enable_writes` flag | Durable per-action approval (exact action, arguments, approver, expiry, workflow version); run pauses without holding a worker | Large |
| External actions | `mark_write` blocks unsafe resume ✓ | Action ledger (intent → attempt → remote reference → outcome); idempotency keys; reconciliation UI | Large |
| Reliability | Durable queue, leases, fencing, checkpoints, at-least-once ✓ | Plus retry/backoff on provider 429/5xx/timeouts; per-node timeouts; per-tenant concurrency and budgets; dead-letter state | Medium |
| Observability | Ordered run events ✓ | Structured logs with run/node IDs; token, latency and cost per call; traces (OpenTelemetry); metrics; alerting | Large |
| Evaluation | Labelled corpus + harness ✓ | Real-document question sets; CI gate on regressions; per-workflow eval sets; online feedback capture | Medium |
| Security | Tenant isolation, encrypted secrets, sandbox, SSRF defence ✓; injection defence is prompt-only | Tool policies enforced outside the model; document ACLs; service key rotation; secrets manager; audit log | Medium–Large |
| Versioning & release | Save overwrites; runs snapshot the JSON ✓ | Immutable published versions + drafts; runs pinned to workflow/prompt/model/KB versions; rollback | Medium |
| Triggers | Chat input, manual trigger | Webhooks with signature verification and deduplication; schedules; queues | Large |
| Streaming | Node lifecycle over SSE ✓ | Token streaming; partial outputs | Medium |
| Memory | Workspace-key log, bounded ✓ | User/conversation/workflow scopes; retention and deletion; summarisation | Medium |
| Multi-tenancy | One owner per workspace ✓ | Roles, invitations, connection-level permissions | Medium |
| Data governance | Run records keep full inputs/outputs indefinitely | Retention policy, PII handling, export/delete | Medium |
| Deployment | Loopback only, SQLite default, five local processes | TLS, managed Postgres, migrations tooling, health/readiness, load tests, backups tested | Large |

## 4. Updates, in order

**Tier 1 — days each; fixes what the scenarios broke**
1. **Tool errors become observations.** Catch adapter/tool failures inside the agent loop, append `{'tool_error': message}` to the transcript, decrement the budget, let the model continue; fail the run only when the budget is exhausted. (F1)
2. **Tool contracts in the prompt.** Extend `NodeDefinition`/tool configs with a description and an argument JSON schema; include them in `available_targets`; validate model-supplied input before execution. (F2)
3. **Render tool observations.** Retrieve/Query results as numbered passages (reuse `render_evidence`); HTTP results as `{status, content_type, body}`. Collect every retrieved source into a run-wide evidence set. (F3, F8)
4. **Citation validation at the response boundary.** Validate `[S#]` in any Response text against the run-wide evidence set; rewrite unknown labels; report cited sources from the Response node. (F4)
5. **Schema-validated outputs for extraction and classification.** Node config carries a JSON schema; invalid output fails the node with the validation error (one bounded repair attempt is reasonable). (F5, F7)
6. **Provider hygiene.** Retry with backoff on 429/5xx/timeouts; per-call token counts (Ollama `prompt_eval_count`/`eval_count`, OpenAI/Claude `usage`) recorded on the node event; structured JSON logs keyed by run and node ID. (F10)

**Tier 2 — weeks; makes real use cases safe**
6a. Section-aware chunking (heading paths as metadata), a wider candidate set with a cross-encoder reranker, and metadata filters; measured against `evals/real`. (F11)
6b. Claim–evidence entailment check on grounded answers, so a faithful citation of an unsupporting passage is caught. (F12)
7. Native tool calling for OpenAI, Claude and Ollama models that report the `tools` capability, replacing the JSON action protocol where available.
8. Durable per-action approval: run status `waiting_approval`, approval record with a hash of the exact action and arguments, expiry, approver; editing invalidates; resume from approval without holding a worker slot.
9. Action ledger and idempotency keys for Jira/GitHub/Confluence/email; reconciliation view for uncertain outcomes.
10. Workflow versions: immutable published version, editable draft, runs pinned to version + model + KB version.
11. Token streaming over SSE; per-tenant concurrency and daily budget limits.
12. Document-level ACL metadata in knowledge bases, applied before passages reach the model; memory scopes (user, conversation).
13. Evaluation on real documents, run in CI as a regression gate; per-workflow question sets.

**Tier 3 — later; platform breadth**
14. Parallel + join, bounded loops (critic with rubric and iteration cap), reusable subworkflows.
15. Webhook and schedule triggers with signature verification and deduplication.
16. Roles and invitations; connection-level permissions; audit log; retention and PII controls.
17. Deployment hardening: TLS, managed PostgreSQL, migration tooling, OpenTelemetry export, load tests on target hardware.

## 5. Status after the Tier 1 fixes (same day)

Tier 1 items 1–6 were implemented, each with tests, and the campaign was re-run (`evals/results/workflows-latest.json`). Every shape is green, including the two that were red or hollow:

| # | Shape | Before | After |
| --- | --- | --- | --- |
| 4 | Agent + Python tool | run failed on the first tool error | `53.0`; the tool is described to the model, a bad call would come back as a `tool_error` observation |
| 5 | Agent + Retrieve as a tool | described "a JSON data format" | `22 days`; retrieval observations are rendered passages, citations validated |
| 9 | Extraction → JSON | valid by luck | parsed and validated by the runtime; one repair, then the node fails |
| 11 | Retrieve → Summarizer → Critic → Response | `[S4]` leaked unverified | labels are run-unique; Response validates every `[S#]` and reports cited sources |

Findings F1–F5 and F10 are closed: tool errors are observations (F1); targets carry descriptions and input hints and inputs are validated (F2); observations are rendered (F3); evidence is registered run-wide with unique labels and validated at the Response boundary (F4); structured outputs are schema-checked (F5); provider calls retry transient failures with backoff, token usage is on every node event, and the worker writes a JSON journal per run and node (F10). F6 (latency/streaming/parallelism), F7 (substring Condition), F8 (untyped HTTP result) and F9 (memory scope) remain open and are Tier 2.

**Measured on real documents** (`evals/real/`: IRS Publication 463, NIST SP 800-63B-4, 29 CFR 1910.178, 49 CFR Part 395; 38 labelled questions; ~2,900 chunks):

- Retrieval, answer passage in top-4: similarity 0.818, keyword 0.818, hybrid 0.879, **RRF 0.909** (paraphrases 0.722 → 0.889). On real regulatory prose the two signals miss different paragraphs and fusion recovers most of both — the opposite of the synthetic handbook, where pure similarity was best. Top-k 8 does not recover the remaining misses (RRF still 0.909): they are right-document-wrong-paragraph cases deep in the ranking.
- Grounded generation, `llama3.1:latest` over hybrid retrieval: answer accuracy **0.879 — exactly the share of questions whose answer passage was retrieved**; accuracy 1.0 and false abstention 0.0 when the passage was retrieved; 5/5 abstentions on unanswerable questions; citation faithfulness 0.964; zero `[unsupported reference]` rewrites. Generation is retrieval-bound.

Two findings that only real data produced:

- **F11 — Chunking and ranking, not k.** The residual misses (the IRS 60/120-day accountable-plan bullets, the §395.3 11-hour limit) sit in documents with many near-duplicate passages; fixed 800-character windows split lists and lose section context. Production retrieval over regulatory text uses section-aware chunking (heading paths as metadata), a wider candidate set with a cross-encoder reranker, and per-document metadata filters. This is the next retrieval work, ahead of any new backend.
- **F12 — Inference without evidence.** On the one question where retrieval missed the NIST password-hint rule, the model inferred a "yes" from an unrelated passage instead of abstaining. The citation was faithful to a passage that did not support the claim. Citation validation proves provenance, not entailment; an entailment check (LLM-as-judge or NLI) between claim and cited passage is the production-grade safeguard and is Tier 2.
- **F13 — Descriptions prime behaviour.** Showing the Python tool's source in its default description made the model send code (`eval(...)`) instead of data. Tool contracts should describe the interface, never the implementation; the default was changed and the operator description should always state the expected input with an example.

## 6. Reference workflow to build against

Internal knowledge assistant, production shape: ingest with ACL metadata → conversation-scoped memory → hybrid retrieve with ACL filter → grounded answer with schema `{answer, citations[], confidence, abstain}` → citation validation at the response boundary → response; every call logged with tokens and latency; eval set from real documents gating releases. Tiers 1–2 above are exactly what turns today's scenario 2 into that workflow. The support-ticket assistant (classify → retrieve → draft → **approve** → update ticket) needs items 5, 7, 8 and 9 before it can be trusted with a real ticket system.
