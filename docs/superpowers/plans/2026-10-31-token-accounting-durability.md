# TA02 Token Accounting Durability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** reusable work and its known accounting commit together; resume cannot replenish spent allowance.

**Architecture:** keep the existing cumulative snapshot authoritative. Capture it before
emission and commit it with the event/checkpoint/item or approval frame under the existing
job fence. Block further model calls while known spend is not durably acknowledged; guard
all accounting writers against decreasing totals.

**Tech Stack:** Python, asyncio, SQLAlchemy 2.0.52, SQLite/PostgreSQL 17, LangGraph 1.2.11,
pytest and the existing scenario/process harnesses. No dependency or schema migration.

**Spec:** `docs/superpowers/specs/2026-10-31-token-accounting-durability-design.md`, revision 2.
**Application baseline:** `90d9839` has the same application as `f3e0923`.

## Global Constraints

- No call ledger, Redis, broker, cost estimator, metrics reconstruction or full event sourcing.
- Reuse `execution_policy.accounting_snapshot`; do not add a second token calculator.
- Keep cumulative totals separate from per-attempt event usage; replay sends no new request.
- `tokens_spent` is an exact nonnegative integer; booleans are invalid.
- No accounting writer may lower a stored total. Compare under the job fence; equal is allowed.
- New runs get `run.data['accounting_contract']=1` and a zero snapshot in one creation transaction.
- Existing/legacy runs are never silently promoted. New model requests require trusted accounting.
- Unverified legacy runs with unstarted approved writes fail at claim before sending them.
- Preserve free/cached completion, existing authorization, uncertainty/reconciliation and TA04 semantics.
- Snapshot bookkeeping is not added to public event payloads. No change to output ports or receipts.
- At most **24 new collected pytest cases**, including parametrization, plus **4 reused harness rows**.
- No separately shipped partial implementation: enable the new run contract only when all paths land together.

## Review Focus

- Approval frames must commit the supplied in-memory snapshot, not the stale DB copy: T03/T04.
- A cancelled thread can still reach storage later; no writer or acknowledgment can regress: T05/T06.
- Cached events must preserve the previous baseline; partial loop replay must not add it again: T07/T10.
- A legacy approval record becomes queued/running after approval; claim must inspect the record: T10.
- Rejected/cancelled responses and repair calls cross different emission paths: T06/T08/T12 and existing TA01 controls.

## Test inventory and budget

Create `backend/tests/test_accounting_durability.py`. Import only existing public modules
at collection time; new helpers may be imported inside their tests. Do not count an import
or unrelated fixture failure as a reproduction. The original T01 must fail on the actual
saved-checkpoint/accounting behavior of the baseline. D denotes changed behavior; C denotes
a compatibility assertion. Keep provider responses synthetic, Worker/Store real.

| ID / test name suffix | Collected cases | Exact assertions |
|---|---:|---|
| T01 `outage_checkpoint_resume` (D) | 1 | Limit 15, response 10+5. Disable fast saves; interrupt just after m0 commit. `m0` and cumulative 15 survive together; resume reuses m0, refuses m1, provider calls stay 1. Also inspect creation's marker/zero snapshot without hiding the original failure behind that new assertion. |
| T02 `outcome_transaction` (D/C), before/after commit | 2 | With fast saves disabled, pre-commit failure leaves checkpoint/event and accounting at their old state; post-commit interruption leaves both new checkpoint and 15 tokens. Never claim a lost commit acknowledgment proves rollback. |
| T03 `pause_snapshot`, valid/missing (D) | 2 | Stored 0, frozen in-memory 15 with a reserved action. Valid: failed pause commit rolls back approval/frames/deferred/accounting, then successful retry commits 15 plus the deferred key once. Missing: `AccountingUnavailable`, no new pause state. No prerequisite successful fast save. |
| T04 `monotonic_writer` (D), event/fast/reserve/pause | 4 | Save 30, attempt 10 through each writer. Typed refusal, saved 30 unchanged and no associated new checkpoint/frame. Equal-total updates still work. |
| T05 `cancelled_thread_cannot_regress` (D) | 1 | Block old 10-token thread before its fence; cancel its await; commit a 30-token outcome; release/join the old thread. Saved total remains 30. Controlled storage race, not a claim that parallel graphs are supported. |
| T06 `cancelled_known_usage_snapshot` (D/C) | 1 | First nested/provider call reports and saves 15. Second reports 15, then raises cancellation before `record_spend`, so memory is 30 but the sink/DB are still 15. Cancel emission must persist 30. Do not infer it from TA03 metrics. Also assert acknowledging captured generation 1 after generation 2 exists leaves generation 2 pending. |
| T07 `loop_progress_replay` (D/C) | 1 | Query/model body reports 15; stop after item 0 commit. Progress and 15 survive; resume never repeats item 0 or adds its tokens again; verify final action counter as in the uninterrupted control. |
| T08 `pending_before_repair` (D), failed/recovered write | 2 | Fast save fails after the first paid response. A strict flush returning false prevents call 2; a confirmed flush permits exactly the intended repair. Calls 1/2 respectively, with no action debit by the flush. |
| T09 `durability_not_recoverable` (D), grounded-continue/structured-route | 2 | Use real agent repair paths. Accounting refusal must remain typed through `finalize_grounded_answer` and the structured-only wrapper, fail by name, and cause no fallback/new provider request. Default fail is already exercised by T08. |
| T10 `legacy_trust` (D/C), model/approved-write/cached | 3 | Plausible old snapshot with no contract: new model request refused; unstarted approved write refused at claim with receiver calls 0; fully cached completion succeeds with model calls 0 and marker absent. Do not restart external actions. |
| T11 `invalid_trusted_snapshot` (D), missing/boolean/negative/text | 4 | Contract marker 1 with missing snapshot or totals `True`, `-1`, `"15"`: named refusal, no provider call, no inferred zero baseline. |
| T12 `fast_save_lease_loss` (D/C) | 1 | Real llm_node/provider returns known usage, persistence raises `LeaseLost`; the exact exception propagates, no retry or repair. Keep the pre-existing paid-answer-on-ordinary-I/O-outage control. |
| **Total** | **24** | Reuse existing TA01/TA04/S07/approval/R01 compatibility tests. |

## Task 1: transaction and accounting interfaces

**Files:** create `backend/app/accounting_state.py`, `backend/tests/test_accounting_durability.py`;
modify `backend/app/storage.py`, `backend/app/approvals.py`, `backend/app/execution_policy.py`.
Apply pytest wrapper/direct-call compatibility edits from the map below in this task,
before later runtime checks. Update scenario entrypoints in Task 3 before executing them.

**Interfaces produced:**

- `AccountingUnavailable(ValueError)`; add it to the existing shared `CONTROL_FLOW` tuple.
- `zero_snapshot() -> dict`: `tokens_spent=0`, empty `action_budget`, `usage`, `usage_by_invocation`.
- `checked_snapshot(value: object) -> dict`: detached copy; required four fields above,
  exact nonnegative integer total and dictionary containers, otherwise `AccountingUnavailable`.
- `accounting_trusted(run: dict) -> bool`: exact integer contract marker 1 plus a checked
  snapshot means true; legacy marker absent means false; marker 1 with invalid state raises.
- `AccountingDurability(trusted: bool)` with `generation`, `acknowledged`, `pending`,
  `changed()`, `capture(snapshot) -> (generation, detached_dict)` and `acknowledge(generation)`.
  The counters are in-memory only; `acknowledge` never clears a newer pending generation.
- `Store._set_accounting(row, snapshot) -> None`: **no commit**. Validate, compare with the
  latest persisted total under the caller's fence, reject a smaller total, assign a detached
  snapshot. Do not clamp totals or merge unrelated snapshots.
- `Store.worker_event(id, owner, event, *, accounting=None) -> bool`;
  `record_accounting(...) -> bool`; `reserve_accounting(...) -> bool`: true only after
  acknowledged commit (including an already-equal saved value). `record_accounting` no
  longer silently succeeds on a lost fence. Preserve the existing false event result for
  a rejected fence; never acknowledge that result. For marked runs, a reusable/usage-bearing
  event cannot omit accounting; `None` only preserves legacy/nonpublishing state.
- `approvals.pause(store, run_id, owner, exc, *, accounting=None) -> bool`: validate frozen
  snapshot, add the write's deferred key idempotently, call the common assignment guard,
  commit snapshot/frames/approval/lease release once. Missing snapshot raises the typed
  error. Do not use the database snapshot as the base or require an earlier fast-save ack.

- [ ] Write T02–T05 first. On the baseline, record intended failures; do not treat missing new interfaces as proof of the original TA02 defect.
- [ ] Update pytest wrappers to accept/forward `**kwargs` without weakening assertions. Direct storage/pause fixtures must provide explicit canonical snapshots when needed; update `test_loop_storage.py::test_an_approval_pause_defers_its_reservation_in_the_column` while retaining its column/legacy and exact-payload assertions.
- [ ] Implement the pure helpers and the common fenced assignment. Route **all four writers**, including `reserve_accounting` and pause, through it.
- [ ] Make event accounting share its existing transaction with `_event`, token rows, loop item merge and checkpoint compaction. A failure rolls back that transaction; no nested Session/commit.
- [ ] Make pause use the supplied frozen snapshot in its own transaction. On missing/invalid/regressing snapshot, roll back. On unacknowledged commit, do not claim rollback or enqueue another write; retain existing durable-status/idempotent recovery semantics.
- [ ] Run: `.venv/bin/python -m pytest backend/tests/test_accounting_durability.py -q -k 'outcome_transaction or pause_snapshot or monotonic_writer or cancelled_thread'`.
  Expected: **9 passed** (2+2+4+1). Do not enable the creation marker or ship this task alone.

## Task 2: runtime durability, restore trust and provider barrier

**Files:** modify `backend/app/worker.py`, `backend/app/compiler.py`, `backend/app/registry.py`,
`backend/app/iteration.py`, `backend/app/storage.py`, `backend/app/approvals.py`,
`backend/app/agent_runtime.py`; extend the
new helper/test modules. Runtime guards and creation of the contract marker are one task,
so intermediate tests do not classify every fresh run as legacy.

**Consumes:** Task 1 interfaces. **Produces:**

- `compile_workflow(..., durability=None)` carries the shared `AccountingDurability`
  object in run state. `None` preserves compiler-only/in-memory executions; the production
  Worker always supplies a managed instance.
- `ensure_accounting_ready(run: dict | None) -> None` (async, in the accounting helper):
  untrusted managed state raises; pending known usage calls the existing strict
  `persist_accounting(snapshot)` callback, requires a true ack and acknowledges the
  captured generation. False/missing ack or ordinary I/O failure becomes the typed error;
  lease loss and cancellation propagate unchanged. No new action charge.
- Worker `persist_spend` and `persist_accounting` return the storage acknowledgment and
  acknowledge **their captured version**, never whatever generation exists after await.

- [ ] Write T01/T06–T12. T01 must show the baseline's durable answer plus absent accounting and extra call before the fix.
- [ ] Increment the local usage generation in `account_usage` whenever reported usage is accumulated, including zero-token reported calls and rejected responses. Missing provider usage remains TA06; do not invent a count.
- [ ] Wrap the compiler's root emitter: refresh the existing sink with `accounting_snapshot` before every forward, including nested explicit emit and `Context.emit`. Avoid recursion/shadowing of the original emitter. Do not add accounting to event payloads or derive it from `event['usage']`.
- [ ] In `Worker.emit`, freeze snapshot+generation before the background thread; call `worker_event(..., accounting=frozen)`; acknowledge only a true commit. Keep shielding and the current persisted-node-cancellation behavior. Delete the separate post-event accounting write.
- [ ] Keep immediate `record_spend` best-effort for ordinary I/O failure, leaving pending state. Re-raise control flow (especially `LeaseLost`); do not silently acknowledge it. A completed answer can still be saved by the atomic outcome transaction.
- [ ] At shared `call_model`, before a new provider request, call `ensure_accounting_ready`. Cover structured/grounded repair, planning and query generation through that existing path. Demo/abstention/cached paths do not need a new provider request. Provider-internal retries and unknown usage remain TA05/TA06 scope.
- [ ] In `agent_runtime.finalize_grounded_answer` and `execute_agent`'s structured-only repair wrapper, re-raise `CONTROL_FLOW` before generic `ValueError` handling. Keep the special `TokenBudgetExhausted` behavior; `AccountingUnavailable` is never a budget abstention. Router/reader repair awaits already sit outside their output-validation catches; verify them without adding unrelated parsing changes.
- [ ] In Worker's approval catch, freeze the current sink and generation, pass `accounting=frozen` to `approvals.pause`, acknowledge that version only after its commit. Preserve clear `AccountingUnavailable` messages; do not let the generic pause-error message hide the cause.
- [ ] Preserve cumulative restore baseline separately from attempt reporting. Cached emission cannot add a saved total again. Check loop-item emission and approval frame publication explicitly, not only ordinary success.
### New-run contract and legacy handling in the same runtime task

**Interface:** `approvals.has_unstarted_approved_write(store, run_id, tenant_id) -> bool`,
reading `ApprovalRecord` with `kind='write'`, `status='approved'` for that tenant/run.
Executing/uncertain writes continue through the existing reconciliation guard; they are
never described as unsent. Completed actions retain stored-result replay behavior.

- [ ] Keep T10's cached legacy case as a positive control, not only refusal tests.
- [ ] At `create_run`, set contract marker and checked zero snapshot in the creation transaction. This is the only automatic introduction of trusted zero.
- [ ] For marker 1, `Store._accounting`/`_document` must expose a missing or invalid column as invalid, not mask it with a stale legacy `data['accounting']` copy. Retain the historical fallback only for legacy reads.
- [ ] Initialize the managed durability object from `accounting_trusted`. Invalid marked state fails before spending. Legacy state remains untrusted across capture, replay and persistence; never add the marker during resume.
- [ ] At worker claim/preflight, after existing uncertainty checks and before any node executes, refuse an untrusted run with an unstarted approved write. Query the approval record, not the run's already-updated status. Keep approval rejection/expiry/cancellation/reconciliation endpoints usable; no automatic restart.
- [ ] Legacy runs without such a pending write can complete through cached/free paths. At the shared provider boundary, untrusted state still refuses new model calls. Do not rewrite legacy missing accounting to a trusted zero or sum operator metrics.
- [ ] Run all **24** new cases and the focused compatibility group. Exact user-visible copy is centralized in the helper:
  - legacy model: `Past token usage cannot be verified for this older run. No further model request was started. Saved results were kept.`
  - legacy approved write: `Past token usage cannot be verified for this older run. The unstarted approved action was not sent by this attempt. Saved results were kept.`
  - pending I/O: `Run accounting could not be saved; no further model request was started. Saved results were kept. Resume after storage recovers.`
  - invalid marked state: `Saved token accounting is missing or malformed. No further model request was started. Saved results were kept.`
  - stale write: `The accounting write was refused because it would lower tokens_spent.`
  - pause: `Current accounting is unavailable; the approval pause could not be confirmed. Saved results were kept.`
- [ ] Run existing `test_s07_boundaries.py`, `test_token_budget.py`, `test_token_budget_recovery.py`, `test_operations.py`, `test_approvals.py` and `test_response_source_boundary.py`.
  Expected: paid answer retained on fast-save outage; T01 provider calls 1; T08 calls 1/2;
  T12 exact lease exception; existing controls pass. Do not ship Tasks 1–2 separately.

## Task 3: reuse process harnesses, mutation checks and release evidence

**Files:** modify `scripts/check_milestone_storage.py`, `scripts/scenarios/milestone_worker.py`,
`scripts/scenarios/lab.py`, `scripts/scenarios/request_review_worker.py`,
`scripts/scenarios/request_routing.py`, `.github/workflows/ci.yml`,
`docs/operations.md`. Add evidence under `evals/results/ta02-fix-<date>/` during implementation.

**Harness interfaces:** extend the existing milestone runner with named checks
`sqlite-accounting-before-commit`, `sqlite-accounting-after-commit`,
`postgres-accounting-before-commit`, `postgres-accounting-after-commit`. Reuse its
`database(kind)`, process lifecycle, fixture receiver and worker-entrypoint marker. Add
only test-mode accounting fault/barrier switches to existing entrypoints; application
modules do not import the harness. No second server/worker framework.

Reuse `request_routing.ModelFixture`; add optional `prompt_tokens=7, completion_tokens=3`
constructor arguments preserving its current defaults, and use `10, 5` for these rows.
The fixture server lives in the parent, outside the killed worker. In the existing
milestone worker entrypoint only, require its lab marker and add test-only flags for
fast-save failure and `before_commit`/`after_commit` barriers. The before-commit barrier
must be inside the transaction (flush then block before commit), not just before calling
`worker_event`; the after-commit barrier fires after the real method returns true.
Scope these hooks to the selected LLM outcome so run creation is not blocked.

- [ ] Update scenario wrappers of `worker_event` to accept/forward `**kwargs`; preserve `args[-1]` as the event by passing accounting as a keyword. Update `approvals.pause` in `request_review_worker.py` similarly. Recheck Task 1's pytest/direct-call edits; never disable the marker to hide a required-snapshot failure.
- [ ] Implement the four rows using fixture LLM usage 10+5 and limit 15. Before-commit kill, with fast saves disabled: neither new reusable state nor its accounting update is committed. After-commit kill: both survive, resume sends no duplicate saved request and no second over-budget call. Receiver stays outside the killed worker; use existing lease recovery facilities and isolate all databases/schemas.
- [ ] Append the two PostgreSQL named checks to the current `postgres-conformance` command; run SQLite checks locally. Preserve S15/S16 and existing migration/ownership/uncertainty checks. Do not create another CI job or widen its timeout without measured need.
- [ ] Run 12 selected mutants in disposable copies, each with its named oracle: M1 omit outcome accounting (T01/T02/T07); M2 miss cancellation capture (T06); M3 pause uses DB copy (T03); M4 remove monotonic guard (T04/T05); M5 skip pending barrier (T08); M6 promote legacy/invalid state (T10/T11); M7 omit legacy approved-write preflight (T10); M8 remove the durability type from `CONTROL_FLOW` (T09); M9 wrap the grounded repair refusal (T09 grounded-continue); M10 wrap the structured repair refusal (T09 structured-route); M11 accept false persistence ack (T08); M12 acknowledge latest rather than frozen generation (T06). T07/existing S07 retain replay-double-count assertions; T12 has its baseline red/green proof for fast-save lease propagation. Reject unrelated fixture/import failures as kills.
- [ ] Collect count: `.venv/bin/python -m pytest backend/tests/test_accounting_durability.py --collect-only -q` must report **24**. Replace redundant cases before increasing this budget; explain a genuinely new requirement if expansion is necessary.
- [ ] Complete full backend seeds 0–4 with retained JUnit, the clean-export isolation proof and deployed scenario campaign including S15/S16. The existing auditor TA02 probe asserts the old defect; retain its baseline JSON and use permanent T01 as the passing fix oracle instead of rewriting historical evidence.
- [ ] Document atomic result and pause boundaries, pending-save refusal, monotonic totals, legacy approved-write preflight and free completion. Keep soft-limit and pre-durability response-window wording explicit. Do not claim full TA03/TA05/TA08 closure.
- [ ] Confirm no tracked app/test changes occurred during the campaign; record source hashes, commands and outcomes, not large raw event histories. `git diff --check` clean.
- [ ] Commit the coordinated implementation only after all paths satisfy the new marker; then check the five hosted jobs on the exact pushed SHA. No partial-contract commit is deployed.

### Wrapper compatibility map

The current wrappers are in `test_switch.py`, `test_loop_completeness.py`,
`test_request_review.py`, `test_response_source_boundary.py`, `test_agent_provenance.py`,
`test_provenance_foundation.py`, `test_operations.py`, `test_execution_policy.py`,
`test_budget_recovery.py`, `test_loop_event_bounds.py`, `test_token_budget_recovery.py`,
`test_s07_boundaries.py`, `test_tool_truncation.py`, `test_loop_checkpoint.py`,
`test_common_replies.py` under `backend/tests`, plus `scripts/scenarios/lab.py`.
Use `rg -n 'worker_event|approvals.pause|reserve_accounting|record_accounting' backend/tests scripts` before the signature edit
to enumerate direct call sites too. These are mechanical compatibility changes; retain
their original behavioral assertions and record any non-mechanical change separately.

## Self-review and stop rule

- Every design invariant maps to a task and an existing or new test above; pause and
  monotonic-write requirements are included in every writer, not just the original repro.
- New interfaces and error copy are defined once; bookkeeping remains outside event payloads.
- Test budget is 24 collected cases (1+2+2+4+1+1+1+2+2+3+4+1) and four harness rows.
- No mutable call ledger, provider reservation model, metrics rewrite or new infrastructure.
- The remaining provider-response/no-durable-save gap is documented, not hidden by a replay guarantee.
- Close TA02 after the atomic/pause/monotonic/legacy/pending barriers and normal gates pass.
  New unrelated findings are recorded for their owning steps rather than extending this plan.

**Next action:** review this implementation plan. Execution is not started by writing it.
