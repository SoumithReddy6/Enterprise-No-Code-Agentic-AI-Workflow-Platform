# New Workflow Input Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session. Use subagent-driven-development only if the user explicitly selects delegated execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New workflows assess Agent input before work starts and verify model-derived decision fields before a Condition or downstream action uses them.

**Architecture:** Put assessment in the shared Agent runtime, and use pure deterministic functions for field verification. The compiler and Condition runtime enforce verified-field coverage, while typed control-flow exceptions stop rejection from becoming a recoverable error or a false branch. Normal Agent ports remain unchanged.

**Tech Stack:** Existing Python/Pydantic/LangGraph/jsonschema/exact-JSON backend; React/TypeScript editor; existing pytest, Playwright and production scenario infrastructure. No new judge model or package.

**Spec:** [Agent input validation design](../specs/2026-10-08-agent-input-validation-design.md).

**Scope:** Creating, configuring, executing and resuming new workflows. There is no separate legacy-workflow migration, bulk edit of saved workflows, historical backfill or adoption project in this plan. The earlier decision that input policies are mandatory remains the runtime requirement; this plan does not promise a legacy exemption. Existing test fixtures must be brought into the new contract where required to exercise shared code, rather than disabling checks globally.

**Baseline:** `477e6981bd13a4bf69801d4ff47aa68c4b4248ab`. This is a proposed plan; no product implementation has started.

## Global Constraints

- Every Agent requires an author-configured `input_policy`; no enabled/disabled switch or permissive default.
- Acceptance requirements are 1–4,000 trimmed characters. A policy is at most 32,000 serialized bytes.
- Optional input schema is at most 20,000 serialized bytes; regex/format validation and remote references are refused.
- At most 32 output checks, paths at most 128 characters, and mappings at most 100 entries.
- Assessment statuses are exactly `valid`, `invalid` and `needs_information`; reason is non-empty and at most 1,000 characters; extra keys are rejected.
- Assessment uses the configured provider/model, at most 512 output tokens per call, and one contract repair. It uses existing model authorization and token accounting, not the action budget or tool-step counter.
- Reserved validation metadata is at most 8,000 bytes; no copied prompts, inputs or evidence quotes in it.
- Keep public Agent outputs `text`, `provider`, `sources` and `grounding`. Runtime metadata is not a bindable port.
- Preserve exact-number handling, durable action accounting, approval digests, write replay, lease fencing and the existing safe no-evidence abstention path.
- Existing loop results and events retain their 2,000,000-byte and 32,768-byte ceilings.
- A model assessment is not independent verification, authorization or proof of arbitrary business truth.
- Do not change live services or create test runs in the user's workspace; use isolated test instances and receiver fixtures.

## Review Focus

1. An Agent copies a hallucinated Boolean/amount through another Agent: copying must not turn it into an independently supported fact. Task 4 owns this test.
2. A zero, `false`, or numeric identifier looks like a missing value: preserve type distinctions and exact digits. Task 2 owns these tests.
3. Real requests contain unit price and total, repeat a quoted amount, or mention money while asking an unrelated question: ambiguity must stop literal verification, and an existing literal must not be reported as proof of purchase intent. Tasks 2 and 7 own these tests.
4. A policy schema tries remote retrieval, expensive regex or cyclic references: configuration must refuse it without a network request or an unbounded check. Task 1 owns these tests.
5. Approval or crash resume restores a different input/candidate, or tries to regain spent tokens: reject mismatches and preserve accounting and write identity. Task 5 owns these tests.

## File responsibilities

Paths below are relative to the repository root.

| File | Responsibility |
|---|---|
| New `backend/app/input_policy.py` | Strict policy, assessment and metadata contracts; typed stop and bounded schema validation. |
| New `backend/app/input_checks.py` | Exact input-field equality/mapping and fixed-grammar source-literal verification. No providers or storage. |
| New `backend/app/agent_input_guard.py` | Assessment calls, bounded repair, observation and reusable input decisions. |
| New `backend/app/decision_support.py` | Static binding coverage and runtime verification before a Condition. |
| `registry.py`, `agent_runtime.py`, `execution_policy.py`, `iteration.py` | Shared invocation integration and rejection propagation. |
| `compiler.py`, `platform_graph.py` | Actionable configuration errors, output metadata contracts and decision enforcement. |
| `worker.py`, `main.py`, `storage.py` | Durable rejection, safe resume and bounded persistence using existing JSON/event storage. |
| New `frontend/components/agent-input-policy.tsx` | Typed policy editor with criteria/schema/check controls. |
| `frontend/lib/workflow.ts`, `workflow-editor.tsx` | Client validation, new-run feedback and creation wiring. |

## Task 1: Define strict contracts without changing execution

**Files:** Create `backend/app/input_policy.py`, `backend/tests/test_input_policy.py`.

**Interfaces:** Produce `InputPolicy`, `InputAssessment`, `AgentInputStop`, `validate_assessment(text: str) -> InputAssessment`, and `checked_validation(value: object) -> dict`. Define `INPUT_VALIDATION_KEY = '_input_validation'`. `AgentInputStop` inherits `Exception`, carries status, stage, reason code and a bounded user-facing reason, and is handled explicitly rather than as a generic `ValueError`.

- [ ] Write parameterized contract tests: missing/blank requirements; exact size boundaries; unknown keys/kinds; duplicate paths; malformed assessment/status/reason; malformed hashes, stages and verified-path metadata. Assert `ValueError` or the named typed stop, never an incidental `TypeError`.
- [ ] Add safe-schema tests. Permit structural and numeric keywords, enum/const and local acyclic references. Reject `pattern`, `patternProperties`, `format`, remote refs and reference cycles. A property merely named `pattern` remains legal. Trap network access to prove no retrieval occurs.
- [ ] Run `.venv/bin/python -m pytest backend/tests/test_input_policy.py -q`; record the missing-contract failures before implementation.
- [ ] Implement strict Pydantic models, serialized byte bounds and bounded schema traversal. Limit schema/input container depth to 32 and expanded schema nodes to 2,000; reject rather than rely on recursion failure. Do not change the existing general output-schema validator.
  The new input-schema keyword set is `type`, `properties`, `required`, `additionalProperties`, `items`, `minItems`, `maxItems`, `uniqueItems`, `minLength`, `maxLength`, `enum`, `const`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `multipleOf`, `$defs`, `definitions`, `$ref`, `$schema`, `title`, `description` and `default`. References must be local `#/...` pointers. Only the standard Draft-04/06/07/2019-09/2020-12 dialect declarations are recognized; unknown keywords/dialects are rejected. This is deliberately a subset, not a replacement for arbitrary JSON Schema.
- [ ] Repeat the focused tests, including boundary sizes and hostile nesting. Commit only this inert, tested module and its tests.

## Task 2: Verify facts with data and rules

**Files:** Create `backend/app/input_checks.py`, `backend/tests/test_input_checks.py`.

**Interfaces:** Consume `InputPolicy`; produce `verify_fields(input_text: str, output_text: str, policy: InputPolicy) -> tuple[str, ...]`. Raise `FieldCheckFailure(status, reason_code, field)` for absent, ambiguous or contradictory support. `$` selects the raw whole string; other bounded dotted paths select exact JSON fields.

- [ ] Write failing tests for `input_field/equals` and `input_field/map`: explicit `false`, explicit zero, missing/null values, unknown mappings, Boolean-versus-number mismatch, numeric-string distinctions and `1000.00000000000001`/`9007199254740993` precision.
- [ ] Write source-literal tests: invented zero absent from input; explicit `$0`, `$24`, `$1,350`; verbatim text; fabricated/repeated quotes; unit price plus total; malformed grouping; non-finite/out-of-range numbers. Missing or ambiguous support gives `needs_information`; contradictions give `verification_failed`.
- [ ] Run `.venv/bin/python -m pytest backend/tests/test_input_checks.py -q` and retain the expected failures.
- [ ] Implement exact comparison and fixed, bounded literal scanners. Require one matching quote and one numeric candidate for numeric checks. Use `exact_json`; do not use float conversion, author-supplied regex, arithmetic inference or another LLM.
- [ ] Repeat tests; mutate equality, missing-value and single-candidate checks separately and confirm the intended tests fail. Commit the pure checker and tests.

## Task 3: Enforce assessment and candidate checks in every Agent invocation

**Files:** Modify `backend/app/registry.py`, `agent_runtime.py`, `execution_policy.py`, `iteration.py`, `compiler.py`, `platform_graph.py`; create `agent_input_guard.py`, `backend/tests/test_agent_input_guard.py`. Update affected repository test fixtures with explicit policies and scripted assessment responses.

**Interfaces:** Produce `assess_input(input_text: str, config, ctx, emit, restored: dict | None = None) -> dict` and `verify_candidate(input_text: str, output_text: str, policy: InputPolicy, assessment: dict) -> dict`. The assessment record has input/policy fingerprints; complete output metadata adds the output fingerprint and verified fields. The existing `execute_agent` signature remains unchanged.

- [ ] Write failing tests for a direct Agent, loop Agent and specialist: invalid input makes zero task/tool calls; missing information stops; valid input permits normal generation; forced `valid` plus invented zero still stops after field verification.
- [ ] Test malformed assessment twice, refusal on a missing policy, provider failure, token exhaustion, all `on_error` policies and both loop error policies. A parent Agent must not turn the stop into a tool observation and continue.
- [ ] Run `.venv/bin/python -m pytest backend/tests/test_agent_input_guard.py -q` before integration.
- [ ] Add required `input_policy` to `AgentConfig`; show named policy errors in graph and attached-node validation. At `execute_agent`, validate resolved input before task work, use the configured model with no tools for assessment, and reuse `validate_with_repair` once.
- [ ] Add `AgentInputStop` to the single `CONTROL_FLOW` tuple and replace the outer Agent's manually enumerated control-flow handler with the shared tuple. Callers emit one outcome per invocation; guard decision events have no duplicated `usage` field.
- [ ] Verify the final candidate after existing schema/grounding checks, before returning outputs. Extend the output contract to permit only strictly checked validation metadata on supported nodes, alongside `_truncation`; reject unrelated extra keys.
- [ ] Give assessment calls a separate stable diagnostic call id while attributing spend through the existing Agent execution identity/accounting. Do not shift tool invocation numbers. Test assessment plus repair plus generation token totals, unchanged action counts, frozen identities and free safe abstention at an exhausted token budget.
- [ ] For `provider: demo`, fail with a named unsupported-assessment message rather than manufacture `valid`. Non-Agent demo workflows remain available. Tests use explicit provider responses, not a global guard bypass.
- [ ] Run the new tests and Agent/loop/S07 regression modules; commit the shared runtime with the fixture changes required to keep that commit testable.

## Task 4: Require proof before a Condition chooses a branch

**Files:** Create `backend/app/decision_support.py`, `backend/tests/test_decision_support.py`; modify `compiler.py`, `registry.py` Context/Condition integration and `platform_graph.py` as needed for attachment analysis.

**Interfaces:** Produce `decision_errors(workflow) -> list[str]`, frozen `DecisionSupport(value_sha256: str, field: str, verified: bool, source_kind: str)`, and `require_decision_support(workflow, node, values) -> DecisionSupport`. These helpers inspect graph bindings/state in the compiler layer; the shared execution-policy layer never receives graph state. Add `Context.decision_support` for the resolved proof. Runtime checks use Task 1 metadata and Task 2 verification.

- [ ] Write failing tests for direct checked `amount`/Boolean/category fields, an unchecked sibling, whole-text conditions, a formatted Prompt, raw LLM/query replacement and Agent-to-Agent copying of an unverified source field.
- [ ] Cover an exact `{message}` Prompt pass-through and a checked `input_field` chain. Preserve proof only for those declared relationships; copied or formatted data does not receive a general trusted flag.
- [ ] Test forged/missing metadata, a changed candidate digest and a Condition handler invoked directly without the required runtime support. No true/false branch may execute after refusal.
- [ ] Run `.venv/bin/python -m pytest backend/tests/test_decision_support.py -q` before implementation.
- [ ] Implement transitive binding analysis with cycle/size bounds. Direct checks cover their exact field; mappings trace their source field recursively. Opaque transformations and model-derived fields lacking upstream support are refused. Metadata such as provider identifiers is not mistaken for a checked business fact.
- [ ] Treat model-derived loop results conservatively: do not certify the entire collection because its items succeeded. Require explicit supported fields; unsupported collection-to-condition transformations fail with guidance.
- [ ] In the compiler, provide `DecisionSupport` to the Condition context; the runtime confirms that its hash and field match the actual condition input/config before `evaluate_condition`. A non-model source is recorded explicitly rather than represented by a missing proof. An ordinary numeric/type error remains distinct from missing proof.
- [ ] For Agents supplying these decision fields, reject write-capable tool/specialist attachments during candidate generation. Place consequential writes in explicit downstream nodes after verification. This prevents a write occurring before the final candidate check; other Agent workflows retain their separate write-approval requirements.
- [ ] Run compiler, recovery-binding and condition regressions. Mutate static coverage and runtime enforcement independently and prove both guards matter. Commit the decision boundary.

## Task 5: Persist new-run decisions and resume safely

**Files:** Modify `worker.py`, `main.py`, `storage.py`, `agent_runtime.py`, `iteration.py`; create `backend/tests/test_input_validation_durable.py`.

**Interfaces:** Add bounded `input_validation` to a rejected run's existing JSON document. Agent checkpoints carry complete `_input_validation`; approval frames carry their separately validated input assessment. Add `validate_run_inputs(workflow, message, checkpoints, frames, loop_progress) -> list[str]` for pre-resume validation of runs created with this feature.

- [ ] Write failing worker/API tests: named rejection persists; no response/branch/approval request is created after rejection; token spend is recorded once; resume with changed policy/input/output is refused before a job or status changes.
- [ ] Test a valid Agent pausing for a downstream write approval and resuming: no repeated assessment, unchanged approval digest/invocation, exactly one write. Inject a crash after assessment, after the candidate checkpoint and after write settlement using deterministic barriers. A bare assessment event is not a successful task checkpoint: a crash before a candidate/frame is saved may repeat the assessment, with all reported spend retained. A matching saved approval frame reuses its assessment; a completed candidate checkpoint needs no model call.
- [ ] Test new Agent loop progress replay. Persist a compact per-item validation receipt rather than copying the full 8 KB metadata into every result. Use at most 256 serialized bytes: version, assessment status and hashes of policy, item input and retained value. Derive check coverage from the matching policy and replay checks when a value is retained.
- [ ] Test value/details omission explicitly: a completed item is not re-executed to recover intentionally dropped data; its unavailable value gets no downstream field proof. Include the receipt in compact-entry reserve arithmetic so the 2 MB results ceiling remains true at 1,000 items.
- [ ] Run `.venv/bin/python -m pytest backend/tests/test_input_validation_durable.py -q` and capture failures.
- [ ] Handle `AgentInputStop` explicitly in the worker, retaining `status: failed` with a structured reason/status/stage/node record. Keep guard-system failures separate from invalid user input. Use existing fenced writes and JSON/event rows; add no tables/columns or growing run-level decision history.
- [ ] Validate matching checkpoint/frame fingerprints and recheck retained decision fields before replay. Add run-resume validation before `store.resume_run`; rejected input needs a new run with corrected input.
- [ ] Persist receipts in the existing per-item row transaction. Keep compact-loop hash checks and item rows authoritative; do not add a second full result copy. Preserve loop preview and result limits.
- [ ] Run durable, S07, loop storage/checkpoint/event-bound, token and approval tests. Mutate replay checking, control-flow propagation and receipt validation individually. Commit persistence and API behavior.

## Task 6: Configure guards while creating a new workflow

**Files:** Create `frontend/components/agent-input-policy.tsx`, `frontend/tests/agent-input-policy.test.ts`, `frontend/tests/browser/agent-input-policy.spec.ts`; modify `frontend/lib/workflow.ts`, `components/workflow-editor.tsx`, browser schema fixtures and backend fixture contract tests.

**Interfaces:** Define client `AgentInputPolicy`, `InputValidationResult` and `agentPolicyErrors(workflow, definitions) -> string[]`. A typed editor owns requirements, optional schema and check rows; the API remains authoritative.

- [ ] Write unit tests for blank criteria, incomplete checks, invalid schema/mapping JSON, adding/removing rows and preserving raw invalid edits. Validate/Run must not send a request carrying stale hidden values.
- [ ] Write a browser test that creates a new purchase workflow, configures the amount/evidence check, and inspects the exact exported/validated config. A second creates a Boolean mapping workflow. Test disabled/refused Run with missing criteria or check coverage.
- [ ] Run `npm --prefix frontend test` and the targeted browser spec before implementation; retain failing results.
- [ ] Add a typed policy section to the Agent inspector. Newly placed Agents start visibly unconfigured; do not insert generic criteria that appear to make them safe. Provide examples as author-selectable guidance, not hidden business rules.
- [ ] Show `invalid`, `needs_information`, `verification_failed` and guard-system failures distinctly in the run view, with Agent name/reason and editable input plus Run again. Approval remains a separate panel; rejection is not a completed or incomplete success.
- [ ] Verify the actual browser layout at desktop/tablet widths and update backend-generated fixture contracts. Run unit/typecheck/lint/build/browser checks; commit editor wiring and tests.

## Task 7: Exercise new workflows through real product processes

**Files:** Add guarded example workflows and a small applicability case file under `evals/`; modify `scripts/check_production_scenarios.py`, `scripts/scenarios/lab.py`, `scripts/check_workflows.py`, `scripts/eval_retrieval.py`; add `docs/agent-input-validation.md` and an operations section.

**Interfaces:** New scenario results include assessment first-attempt/after-repair/failed counts, final input decision, verified fields and receiver-call counts. Existing scenario assertions remain; counts are reported from the actual catalogue, not copied from an older report.

- [ ] Define expected outcomes before running models for at least 30 cases across purchase, Boolean/category and read-only summarization tasks. Label them as author-defined expected outcomes, not a human calibration set. Include injection-like input, unrelated questions (including ones containing a real currency literal), missing information and positive controls. Report false acceptance/rejection separately from contract compliance. A literal match alone does not verify intent; use a structured request with deterministic input rules for stronger automatic-approval guarantees.
- [ ] Add deployed scenarios for forced `valid` plus invented `0`/`true`, genuine zero/false, supported category mapping, malformed assessment twice, invalid loop/specialist input, no-proof Condition and approval resume. Assert receiver calls and branch absence through the real API/worker.
- [ ] Add live-model cases using existing local models. Keep unit-price-plus-total requests as expected clarification cases. For safe routing, add an explicit structured `total` record or a single currency literal; do not silently replace ambiguity tests or claim arithmetic inference is supported.
- [ ] Configure explicit policies in repository scenario/evaluation builders. Make guard responses visible in provider fixtures; do not exempt test workflows. The generation harness must call the actual product runtime, never an eval-only guard.
- [ ] Document how a new workflow defines valid/missing/invalid input; independent checks; their limits; assessment cost; rejection UI; read-only decision Agents; internal writes and human approval as separate boundaries. Structured journals use codes/ids only, not model prose or source text.
- [ ] Run the deployed scenarios, then the live suite below. Retain request logs from synthetic fixtures only. Commit examples, scenario assertions and operator/user documentation.

## Task 8: Release verification on the final tree

**Files:** Save fresh artifacts under `evals/results/new-workflow-input-validation-2026-10-08/` and a report under `docs/reviews/`. Use a timestamp suffix for reruns rather than overwriting earlier evidence.

- [ ] Capture HEAD, dirty-tree status, source/document SHA256 values, model digests, start/end times and every command's exit code before claiming readiness. Run source-mutating probes in scratch copies.
- [ ] Run the backend suite serially at hash seeds 0–4, retaining one JUnit/log per seed. Use `.venv/bin/python -m pytest backend/tests -q --junitxml=<artifact-path>`; exit 0 and all tests passing are required. Do not assert a predetermined test count.
- [ ] Run `npm --prefix frontend test`, `run typecheck`, `run lint`, `run build` and `run test:browser`. Run the existing dependency gate with its reviewed exception policy; report accepted residuals honestly.
- [ ] Run `.venv/bin/python -m scripts.check_production_scenarios --all --live-models --out <artifact-path>/production`. Require all actual catalogue stages to pass and no source changes during the run.
- [ ] Run PostgreSQL conformance using `scripts/check_postgres.py` on an isolated schema, including new rejection/checkpoint metadata paths. Existing schema migration/autogenerate checks must remain green because no new DDL is planned.
- [ ] Run fresh generation through the product guard:

  ```bash
  .venv/bin/python -m scripts.eval_retrieval \
    --corpus evals/real --questions evals/real/questions-expanded.json \
    --chunking section --modes hybrid --generate-mode hybrid \
    --reranker local_cross_encoder --generate llama3.1:latest \
    --product-answerability-guard \
    --out evals/results/new-workflow-input-validation-2026-10-08/generation.json
  .venv/bin/python -m scripts.check_grounding_eval \
    evals/results/new-workflow-input-validation-2026-10-08/generation.json \
    --expected-count 53
  ```

- [ ] Keep existing grounding thresholds unchanged: substring >= .81, false abstention <= .09, unanswerable abstention >= .70, citations >= .90, first-attempt grounded-contract compliance >= .90, uncited answerable rows <= 5, and no generation errors. The new assessment measurements are separate from the grounded-answer contract measurements.
- [ ] For the 30 new live assessment cases, require first-attempt contract compliance >= .90 (at least 27/30) before adoption; set this bar before the run and report all semantic mistakes rather than claiming the contract rate proves correctness. Every deterministic bypass/verification scenario must pass.
- [ ] Export the final committed tree into a fresh checkout and repeat the critical tests/browser gates. Any green working-tree result that depends on an omitted file is insufficient.
- [ ] Review source changes and evidence together; fix defects and repeat affected gates without lowering thresholds. After an authorized push, verify all five hosted jobs on that exact SHA. A local green suite does not stand in for hosted CI.

## Completion criteria

The feature is ready only when newly authored Agent workflows have explicit policies;
invalid or unsupported inputs cannot reach their decision branches; numeric, Boolean
and category positive controls still work; nested/recovery/replay paths preserve the
boundary; the UI communicates the outcome accurately; and the recorded release gates
pass on the final source tree. Reports must separate model-assessed suitability from
independently verified fields. This plan contains no claim of universal semantic truth
and no migration project for previously saved workflows.
