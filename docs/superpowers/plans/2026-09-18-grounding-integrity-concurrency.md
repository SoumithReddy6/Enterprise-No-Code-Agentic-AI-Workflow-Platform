# Grounding, Data Integrity, and Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Replace phrase-based grounding with one validated answer contract, close citation and empty-evidence bypasses, preserve legacy workflow imports, enforce unique KB names, and reject stale workflow saves.

**Architecture:** `agent_runtime.py` owns one grounded-answer contract used by Agent and Query. The frontend citation domain is widened to match server labels above 100, and the Response boundary always validates citation syntax. Database-enforced normalized KB names and compare-and-swap workflow updates protect shared state.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, SQLAlchemy, LangGraph, React, TypeScript, pytest, Node test runner.

**Spec:** User-supplied requirements dated 2026-09-18 in this task.

## Global Constraints

- Use one bounded repair after an invalid grounded contract, then fail.
- Do not adopt the contract if real-document first-attempt compliance is below 0.90.
- Widen the frontend citation domain (the user-approved alternative) and test 120 passages without silently dropping sources.
- Preserve the supplied baseline `evals/results/real-generation-recheck-2026-09-18.json`.
- Stale workflow saves and normalized KB name conflicts return HTTP 409.

---

### Task 1: Grounded answer contract and citation grammar

**Files:**
- Modify: `backend/app/agent_runtime.py`
- Modify: `backend/app/platform_nodes.py`
- Modify: `backend/app/registry.py`
- Test: `backend/tests/test_agent_grounding.py`
- Test: `backend/tests/test_platform_integration.py`

**Interfaces:**
- Produces `finalize_grounded_answer(text, passages, repair, output_schema=None) -> (text, sources, grounding_json)`.
- Grounded model output is `{answer, citations, abstain, reason}`.

- [x] Add failing tests for hedged answers, clean abstention, trailing guesses, missing citations, malformed JSON, retrieval tools with zero passages, grouped citations, empty-registry Response validation, and the widened citation domain with 120 sources.
- [x] Run focused tests and verify failures match the reported defects.
- [x] Implement contract parsing, one repair, canonical citation rendering, empty-evidence enforcement, and shared Query integration.
- [x] Remove phrase-sniffing and make Response validation unconditional.
- [x] Run focused and full grounding tests.

### Task 2: Evaluation compliance gate and README terminology

**Files:**
- Modify: `scripts/eval_retrieval.py`
- Modify: `backend/tests/test_eval_metrics.py`
- Modify: `README.md`
- Create: `evals/results/real-generation-grounded-contract-2026-09-18.json`

**Interfaces:**
- Generation rows expose `contract_compliance` as `first_attempt`, `after_repair`, or `failed`.
- Summary exposes counts and rates for all three states plus uncited answerable rows.

- [x] Add failing metric tests for compliance aggregation.
- [x] Implement compliance data collection without changing retrieval metrics.
- [x] Run the real-document generation evaluation with the baseline settings.
- [x] Stop adoption if first-attempt compliance is below 0.90; otherwise compare substring match, false abstention, citation rate, and uncited rows.
- [x] Replace README overclaims with the harness terms and inline limitations.

### Task 3: Legacy VectorDB workflow compatibility

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/platform_graph.py`
- Modify: `frontend/lib/workflow.ts`
- Test: `backend/tests/test_compiler.py`
- Test: `backend/tests/fixtures/pre-vectordb-removal-workflow.json`
- Test: `frontend/tests/workflow.test.ts`

**Interfaces:**
- Workflow parsing accepts legacy `store` edge kinds.
- Validation/import returns an actionable retired-VectorDB message and never a raw Pydantic trace.

- [x] Add a representative pre-removal fixture and failing backend/frontend tests.
- [x] Accept and isolate retired store edges in graph splitting.
- [x] Emit the actionable retired-VectorDB error for unsupported legacy nodes.
- [x] Run compiler, API, and frontend import tests.

### Task 4: Database-enforced KB name uniqueness

**Files:**
- Modify: `backend/app/kb/management_models.py`
- Modify: `backend/app/kb/management.py`
- Modify: `backend/app/kb/rpc.py`
- Modify: `backend/app/kb_gateway.py`
- Test: `backend/tests/test_kb_management.py`
- Test: `backend/tests/test_kb_services_integration.py`

**Interfaces:**
- `name_key` is whitespace-collapsed and case-folded.
- Unique constraint covers `(tenant_id, name_key)`.
- A domain `ConflictError` maps through RPC and gateway to HTTP 409.

- [x] Add failing create, rename, cross-tenant, concurrent, and HTTP conflict tests.
- [x] Add the schema column, additive migration/backfill, normalized writes, and database constraint handling.
- [x] Preserve the submitted conflicting name in the 409 response.
- [x] Run KB domain and integration suites.

### Task 5: Optimistic workflow concurrency

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/storage.py`
- Modify: `frontend/lib/workflow.ts`
- Modify: `frontend/components/workflow-editor.tsx`
- Test: `backend/tests/test_api.py`
- Test: `frontend/tests/workflow.test.ts`

**Interfaces:**
- PUT body is `{...workflowFields, updated_at}`.
- Store compare-and-swap raises `WorkflowConflict(current)` on mismatch.
- HTTP 409 returns the current server workflow and timestamp.

- [x] Add failing fresh, stale, concurrent, and frontend state tests.
- [x] Implement atomic timestamp comparison and conflict response.
- [x] Track the loaded/saved timestamp in the editor and show the conflict notice without clearing edits.
- [x] Run API and frontend tests.

### Task 6: Full verification

- [x] Run `git diff --check`.
- [x] Run `.venv/bin/python -m pytest backend/tests -q`.
- [x] Run `npm test`, `npm run typecheck`, `npm run lint`, and `npm run build` in `frontend/`.
- [x] Review the complete diff against every achieved criterion and report any unmet metric gate explicitly.

## Final implementation status

Implementation and regression verification steps above are complete. **Batch A acceptance is blocked by the model-quality gate**, not by unit tests. Final real-document substring match is 0.818 against the required 0.879, although first-attempt compliance is 0.947 and citation rate is 1.0. See [the verification report](../../grounding-integrity-verification-2026-09-18.md) for both runs and the unresolved factual errors. No push or commit was performed. Live PostgreSQL and hosted CI remain unverified.
