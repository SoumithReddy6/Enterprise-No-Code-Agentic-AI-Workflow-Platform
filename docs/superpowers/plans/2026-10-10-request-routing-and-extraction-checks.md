# Request Routing and Extraction Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution; use superpowers:subagent-driven-development only if the user chooses delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Tasks 1–4 are committed; all five hosted CI jobs passed for the router commit `724ece6` (run `38159805438`). Task 5 implements the pure deterministic checking and receipt layer on 2026-10-11; final local verification and user commit/push precede hosted CI. Tasks 6–8 remain pending. No verifier handler, runtime review/continuation gate or complete admission starter is enabled yet. Router live accuracy remains uncertified (Step 4 smoke: 4/7); this layer invokes no judge model.

**Goal:** Handle prepared greetings without an LLM, route other inputs by workflow purpose, and prevent unsupported extractions from authorising consequential actions.

**Architecture:** Add greeting-cache and LLM request-router nodes at entry, followed by a deterministic extraction-check node. Repair provenance first and enforce value-bound verification in the shared execution path, using existing checkpoint/approval machinery for uncertainty.

**Tech Stack:** Existing FastAPI, Pydantic, LangGraph, exact JSON/Decimal, SQLAlchemy/Alembic, React and Playwright; async redis-py and an isolated Redis service for common replies.

**Spec:** [Draft design](../specs/2026-10-10-request-routing-and-extraction-checks-design.md). Both documents need review before execution.

## Global Constraints

- Audit baseline: `dad766e`; R01–R12 were reproduced and then repaired in Task 1 (`79216b8`). Its tests and hosted CI are recorded separately from the original 1,195-test baseline.
- New workflows default to contract 2; no separate old-workflow conversion project.
- Original messages remain unchanged. Router replies/examples/cache text never become extraction evidence.
- Cache exact whole-message entries only; no cached acceptance, approval or write result.
- Cache limits: 100 entries; message 256 characters; reply 2,000 characters; value 4,096 bytes; TTL 3,600 seconds; total operation budget 200 ms.
- Router: three typed outcomes, initial call plus at most one repair, existing model authorisation and token accounting.
- No guessed-value automatic bypass in the new contract. Unverified meaning goes to clarification/review.
- Verification receipt: version 1, at most 64 field results and 8,192 bytes, exact input/value/policy/run scope.
- Existing event/item/value ceilings, fencing, expiry, rate limits and write reconciliation remain enforced.
- Any schema change uses Alembic. Cache data is disposable; database records remain authoritative.
- Build tests that fail on the defect first; keep earlier audit evidence untouched; test with isolated processes/data.

## Review Focus

1. A greeting prefix followed by a real request must not lose that request (Task 3).
2. Router acceptance of a fabricated/incomplete request must not authorise a write (Tasks 5–6).
3. A value changed after verification, or a review from another run, must not remain eligible (Tasks 5–6).
4. Redis outage, malformed cache data and cross-tenant keys must not change admission rules (Task 3).
5. Attached Agents, loop replay and crash-after-approval must not bypass checking or repeat a send (Tasks 1, 6, 8).

## File structure and responsibilities

| Area | Files | Responsibility |
|---|---|---|
| Provenance repair | `backend/app/provenance.py`, `calculate.py`, `compiler.py`, `execution_policy.py`, `iteration.py` | Close R01–R12 before enforcement |
| Contracts | `backend/app/request_contracts.py` (new), `models.py`, `registry.py` | Strict node configs, workflow contract, declared control handles/optional input |
| Prepared replies | `backend/app/common_replies.py` (new), `.env.example`, `compose.yaml`, `backend/requirements.txt` | Exact lookup, Redis adapter, local fallback, dependency lifecycle |
| Request router | `backend/app/request_router.py` (new), `registry.py`, `storage.py` | Restricted model call, validated route, usage/auth integration |
| Verification | `backend/app/extraction_checks.py`, `verification_receipts.py` (new) | Deterministic field checks and receipt eligibility |
| Runtime/review | `compiler.py`, `execution_policy.py`, `worker.py`, `approvals.py`, `storage.py`, `main.py`, `platform_graph.py` | One execution boundary, durable typed review, no bypass |
| Migration | New revision after `0003_loop_storage` | Approval kind/backfill with tested downgrade |
| Editor | `frontend/lib/workflow.ts`, `components/workflow-editor.tsx`, `components/platform-settings.tsx` | Setup, handles, checks and review UX |
| Operations/evidence | `readiness.py`, `operator_metrics.py`, `scripts/check_production_scenarios.py`, new evaluation script/cases, docs/CI | Service health, content-free metrics, end-to-end release evidence |

Do not split graph semantics, retry policy or model accounting into competing implementations.

---

### Task 1: Repair the provenance foundation

**Files:** Modify the existing provenance/Calculate/compiler/shared-execution modules and existing/new focused tests. Read the independent audit and its rerunnable probes first.

**Interfaces:** Preserve public node ports. Produce bounded, value-specific provenance consistently for graph and attached invocations; cached successes retain their original decision receipt. Add `EvidenceContext(eligible_texts: tuple[str, ...], structured_sources: tuple[dict, ...], complete: bool)` in `provenance.py`; each structured source records its originating node/port and digest. It contains only evidence eligible from this invocation's actual resolved input, not all run evidence.

- [x] Freeze the labelled measurement suite and release targets from the spec before product changes. Record a baseline against the original example and pin model/policy/input hashes; preserve both safe and unsafe results. The suite and thresholds are not revised after the new live run.
- [x] Add failing permanent regressions for R07 (metadata overflow) and R01/R02/R03/R04/R10/R12 (incorrect trust). Assert a valid 64-field answer still completes; unsupported values remain guessed and would require review.
- [x] Run those tests against the reviewed baseline and retain failures in a new evidence directory.
- [x] Repair those boundaries: origin-aware eligible evidence, collision-free field identity, whole-token/exact numeric parsing, conservative overflow and model-port labels. Tool results of unknown independence remain unverified; echoes/transforms retain dependency origin.
- [x] Add failing R05/R06 arithmetic tests, R08 coalesce/boolean short-circuit tests, R09 cached inspector receipt test, and R11 per-item/attached labels and resume tests.
- [x] Fix selected-operator evaluation, exact sign handling, static certainty wording, replay receipts and shared labelling without changing the retry/lease boundary.
- [x] Run focused tests and all backend/frontend tests affected. The old defect-probe script should now stop reproducing defects; its exit code alone is not the new acceptance test. Do not overwrite its original JSON artifact.
- [x] Commit the independently reviewed repair (`79216b8`). Implementation ruling: supply one atomic commit for the tested shared v2 record/context contract, rather than intermediate combinations of graph and attached producers. A clean export of HEAD plus the exact file list is verified before handing the command to the user.

### Task 2: Declare contracts and control-flow interfaces

**Files:** Create `request_contracts.py`; modify `models.py`, `registry.py`, `compiler.py`, `platform_graph.py`, frontend workflow types; add `test_request_contracts.py` and compiler tests.

**Interfaces:**
- `Workflow.contract: Literal[1, 2] = 1`.
- Strict configs `GreetingCacheConfig`, `RequestRouterConfig`, `ExtractionCheckConfig`; types `RouterDecision`, `FieldRule`, `VerificationReport`.
- Add `NodeDefinition.branch_routes: tuple[str, ...] = ()` and `optional_inputs: dict[str, str]` with an empty default. Publish both additively. Existing Condition declares true/false; new entry/check nodes declare continue/reply.
- `request_contracts.control_routes(definition) -> tuple[str, ...]` is used by compiler and registry-backed editor, rather than another hand-maintained node-type list.

- [x] Write tests for round trips, old documents omitting contract, unknown fields, exact limits, route-key consistency and reserved optional-port names.
- [x] Run them before adding the contracts; confirm the intended failures.
- [x] Implement strict schemas and two-handle controls. Optional inputs are optional bindings, not implicit source lookups; validate every provided reference with normal availability guarantees.
- [x] Test control nodes reject recovery without a branch, extra/missing edges and body/tool attachments. Re-run S08-style binding tests on both routes.
- [x] Add contract-2 validation tests: entry chain required; a router accepted path cannot directly reach a write; required verification cannot be bypassed by an error edge or attachment. Invalid drafts remain saveable, invalid submissions create no run.
- [x] Regenerate registry-backed browser fixtures for the additive fields and run compiler/workflow integrity/registry/type, fixture parity and browser tests. Independently review the seam; model calls inside attached loop bodies are included in decision dependency checks.
- [x] Commit the seam without new node handlers registered until their owning task implements them. The editor's contract-2 starter activates in Task 7 when the full path exists; Step 2 preserves contract versions but keeps today's starter runnable.

### Task 3: Implement exact prepared-reply caching

**Files:** Create `common_replies.py`, `test_common_replies.py`; modify registry, Context/service wiring in main/worker, readiness, env/Compose/requirements and CI Redis test service.

**Interfaces:**
- `normalise_common_message(message: str) -> str` applies the spec's lookup-only normalisation.
- `ReplyScope(tenant_id: str, workflow_digest: str, reply_set_digest: str)`.
- `async lookup_common_reply(message: str, config: GreetingCacheConfig, scope: ReplyScope, cache: CommonReplyCache | None) -> CommonReplyResult`.
- `CommonReplyResult(branch: Literal['continue','reply'], reply: str, outcome: Literal['hit','filled','local_fallback','not_common'])`.
- `CommonReplyCache.get(key: str) -> Awaitable[bytes | None]`, `set(key: str, value: bytes, ttl: int) -> Awaitable[None]`, `aclose() -> Awaitable[None]`.

- [x] Write tests for Hello/HELLO/hello!, whitespace, explicitly configured multilingual aliases, unknown messages, normalisation collisions and `Hello, buy 3 laptops` staying intact.
- [x] Test cache outage/timeout/expiry/wrong payload, an encoded reply exceeding the 4,096-byte cache limit, and cross-tenant/policy scope. Assert the exact configured greeting still replies with zero model calls, while non-greetings continue unchanged.
- [x] Run failing tests, then implement the exact configured-message map, cache adapter and local fallback. Verify cached text equals the current authoritative configured reply; cache data never determines permission to proceed.
- [x] Select compatible pinned Redis client/server releases, record the versions and audit them before adding them. Use async client calls and explicit shutdown; no dependency installation during plan drafting.
- [x] Add an isolated real-Redis test of TTL and tenant/version keys, using a controlled clock only for unit tests. Keep secrets/raw messages out of journal and readiness.
- [x] User committed/pushed Task 3 as `953d4d2`; all five hosted jobs passed in run `38155405292` before Task 4.

### Task 4: Implement the LLM request router

**Files:** Create `request_router.py`, `test_request_router.py`; modify model authorisation/type lists, registry, shared execution/service wiring and router checkpoint tests.

**Interfaces:**
- `build_router_messages(message: str, config: RequestRouterConfig) -> tuple[str, str]` separates saved rules from original input.
- `validate_router_decision(text: str) -> RouterDecision`.
- `async request_router_node(inputs: dict, config: RequestRouterConfig, ctx: Context) -> dict[str, str]` returns branch/reply/decision.
- Initial/repair calls use existing authorised provider and spend persistence; no new retry/accounting loop.

- [x] Write tests for proceed/clarify/out_of_scope, missing purpose, malformed/extra fields, contradictory proceed payloads, invalid output twice, and no tools/delegation.
- [x] Assert router failure and token exhaustion cause no extraction or external call. Router acceptance remains model-derived, not source/verified metadata.
- [x] Run failing tests, then implement the prompt, one bounded repair and branch mapping. Keep the original extraction input bound to the trigger, not a router paraphrase.
- [x] Verify usage for both calls, model allowlist/credential enforcement, lease-loss/cancellation propagation, and saved-policy/input digests on replay with zero additional model calls.
- [x] Add deployed receiver tests: an out-of-scope request sends nothing; a deliberately wrong proceed still cannot bypass the later check. The router commit is kept independent.

- [x] User committed/pushed router `724ece6`; all five hosted jobs passed in `38159805438` before Task 5.

### Task 5: Implement deterministic extraction verification

**Files:** Create `extraction_checks.py`, `verification_receipts.py`, `test_extraction_checks.py`, `test_verification_receipts.py`; modify shared provenance/context contracts.

**Interfaces:**
- `verify_extraction(original: str, value: str, config: ExtractionCheckConfig, origins: EvidenceContext, reference: str | None) -> VerificationReport`.
- `VerificationReport.verdict: Literal['passed','clarify','needs_review']`, with bounded field results and reason codes.
- `make_receipt(run_id: str, node_id: str, original: str, value: str, policy_digest: str, report: VerificationReport, review_id: str | None = None) -> dict`.
- `checked_receipt(record: object) -> dict` and `receipt_matches(record: dict, *, run_id: str, original: str, value: str, policy_digest: str) -> bool`.
- Reserved output key `_verification`; explicitly validate allowed metadata keys rather than accepting arbitrary underscore keys.

- [x] Write tests for invented zero, explicit zero, null/missing facts, booleans, negatives forbidden by schema, exact exponents, path collisions, malformed types and receipt overflow.
- [x] Test `item: "order"` with a real catalogue fixture is not an automatic pass, and a quotation-only rule leaves suitability unknown. Test yes/no from prose remains unverified; structured source equality/authoritative records can establish it.
- [x] Test independent references, wrong reference bindings, echo/model reference refusal, inconsistent total versus quantity × price, and missing required rules. No new judge model is called.
- [x] Run failures, then implement exact schema/Pointer rules and Calculate cross-checks using repaired existing helpers. Include complete rule/pointer/input/value digests; omitted coverage never passes.
- [x] Test changed values, policy edits, another run's receipt, another tenant's reference, and human approval of only the displayed value cannot authorise a different value.
- [x] Run focused verification/provenance/Calculate tests; keep the pure checking and receipt layer separate from continuation/review.

- [x] User committed/pushed this layer as 46e65e7; hosted CI run 38273719543 passed all five jobs before Task 6.

### Task 6: Enforce checks and add durable review

**Files:** Modify compiler/shared execution/worker/approvals/storage/main/platform_graph; create the Alembic revision and `test_request_review.py`, `test_request_safety_boundary.py`; extend migration/PostgreSQL tests.

**Interfaces:**
- One `check_execution_eligibility(identity, resolved_inputs, receipts, workflow_contract)` boundary covers graph and attached execution.
- `ReviewPause` carries kind (`decision` or `verification`), exact payload/digest, execution identity and checkpoint frame. Existing write ApprovalPause remains compatible.
- Approval records gain kind; existing rows backfill to write. Typed decision reviews take true/false; verification reviews accept/reject the displayed interpretation.
- Verification output only returns a continue branch after pass or a matching recorded human confirmation. Origin labels are not rewritten by approval.

- [x] Write tests for absent checks, router-proceed bypass, guessed business values, failed cross-checks, error routing and direct writes in contract 2. Assert zero receiver requests before eligibility or human confirmation. A handler/model/cache must not be able to mint its own verification receipt.
- [x] Add failing tests for pause before branch/write, released lease, mismatched digest, expiry, rejection, cancelled run, stale owner and second approve idempotence.
- [x] Implement the approval-kind migration with downgrade/reupgrade, adoption from the current head and empty autogenerate diff. Keep data-migration and schema authorities separate.
- [x] Implement checkpoint/pause/requeue using existing transactions and fencing. Add ReviewPause to the shared CONTROL_FLOW tuple and every caller's pause handling; it must never be converted into an item failure, retried or recovered. A reviewed interpretation's receipt is specific to run/value/policy; downstream derived eligibility uses only actual checked dependencies, not every expression field.
- [x] Verify cached resume restores review receipts/reasons, specialist and loop execution cannot escape the boundary, and changed outputs invalidate a prior receipt. Malformed restore fails before any downstream call.
- [x] Use subprocess SIGKILL at pause/confirmation/settlement windows against SQLite and isolated PostgreSQL. Preserve uncertain-write refusal and durable result replay; no duplicate sends.
- [x] Run focused worker/approval/migration suites and deployed boundary scenarios; commit independently of the editor.

Implementation verified on 2026-10-12. Hosted CI for Step 6 remains pending the user commit/push; check all five jobs before starting Task 7. Evidence: `evals/results/request-enforcement-step6-2026-10-12/report.json`.

### Task 7: Build the editor and user explanations

**Files:** Modify frontend workflow/editor/settings files; add `tests/request-routing.test.ts`, `tests/browser/request-routing.spec.ts`; update prepared examples.

**Interfaces:** Registry-backed continue/reply handles, strict JSON config editors, contract-2 new workflow seed, typed review panels consuming stored receipts/approval kind.

- [ ] Write unit/browser tests before wiring: new flow contains cache/router/check; purpose and rules are visible; invalid JSON blocks Validate/Run while remaining saveable.
- [ ] Implement purpose/accepted examples/exclusions and extraction field-rule settings. For a generic Q&A seed, configure a broad Q&A brief; require extraction checks where model-derived business facts are used, not on every narrative response.
- [ ] Show prepared cached replies versus model routing, missing fields, actual per-field evidence/check outcomes and manual review. Never label router proceed as approval.
- [ ] Build purchase, support/ticket, refund and yes/no examples using generic nodes. Examples needing authoritative records use explicit source fixtures; unavailable evidence causes review, not invented defaults.
- [ ] Test review digest/expiry feedback, cached replay explanations, all result branches, and long reasons/answers at 1280 and 768 px. Assert response area remains scrollable and nonzero.
- [ ] Run frontend units, typecheck, lint, build and Playwright; verify registry fixture parity; commit the UI/examples.

### Task 8: Measure the full path and document release evidence

**Files:** Create `evals/request-admission/questions.json`, `scripts/eval_request_admission.py`, `scripts/check_request_admission_eval.py`; extend production scenarios, operator metrics and operations docs/CI.

**Interfaces:** Saved case expectations are immutable during a run; summary separately reports cache handling, routing errors, verification errors, manual decisions, unsafe automatic actions, latency and tokens. Gate fails on unsafe automatic action, missing required result/provenance, or valid-request proceed rate below the predeclared 0.90, without changing existing grounding thresholds.

- [ ] Load the suite frozen in Task 1: at least 20 proceed, 20 clarify, 10 out_of_scope and 10 prepared-reply cases, spanning purchases, support, refunds and yes/no. Preserve at least 20 held-out cases excluded from router prompt examples and tuning. Verify labels for routing, available facts and safe action independently; add new regressions separately rather than relabelling the fixed suite.
- [ ] Add deterministic wrong-router/wrong-extraction faults and repeat each approval/crash boundary with a real receiver. Exclude manually approved actions from the automatic-safety numerator and report them explicitly.
- [ ] Run fresh live LLM cases and retain all outcomes, including unnecessary clarification/review and accepted-but-unverified requests. Zero unsafe automatic actions on the fixed suite is required; disclose finite sample size and model versions.
- [ ] Run backend at five hash seeds with JUnit, frontend/unit/typecheck/lint/build/browser, unchanged deployed and live production campaigns, knowledge checks, schema/PostgreSQL conformance and unchanged 53-question grounding gate. Preserve genuine failures; do not relax gates to fit results.
- [ ] Record commit, source/case/policy hashes, model digest, environment, timestamps, exit codes and receiver counts. Verify clean-checkout reproducibility. Use isolated databases/accounts/Redis; leave real workspace history and earlier audit artifacts untouched.
- [ ] Document cache fallback, router limits, review/expiry/reconciliation, privacy-safe metrics and acceptance results. Add CI Redis integration tests and check all hosted jobs on the exact pushed revision.
- [ ] Commit evidence and release recommendation only after reviewing the results. Activate contract-2 enforcement for the new templates only when the boundary and measurement gates pass.

## Draft self-review

- The entry design uses two visible nodes without requiring A5.2 multi-way business routing; clarify and out_of_scope share a terminal reply branch.
- Optional reference bindings still pass normal availability validation; they do not reintroduce S08 through hidden config references.
- Router decisions remain model-derived; downstream eligibility is checked separately.
- Prepared reply fallback preserves the greeting rule during Redis outage rather than widening acceptance.
- Per-value receipts and human scope retain origin; no approval becomes source evidence for another run.
- R01–R12 are prerequisites, not treated as resolved by adding a router.
- Static presence/quotation is not presented as proof of natural-language field meaning. Ambiguous roles require review and are measured.
- This draft adds no semantic cache, replacement model, new calibration threshold, automatic expert override or saved-workflow conversion campaign.

## Review and execution

Review the linked design and this task plan before implementation. The proposed phases are
independent review/commit gates, not a request to change everything at once. Execution
method is not selected by this drafting request; native execution or explicitly authorised
delegation can be chosen after review.
