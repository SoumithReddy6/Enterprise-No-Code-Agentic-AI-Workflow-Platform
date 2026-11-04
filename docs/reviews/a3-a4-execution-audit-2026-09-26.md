# A3/A4 execution audit — 26 September 2026

## Verdict and scope

A3 and A4 have functioning backend implementations, but neither is complete against the new roadmap. Passing happy-path tests currently conceal safety, recovery, budget, and result-retention defects. Do not start parallel execution on these semantics before the priority-one issues below are resolved.

Reviewed HEAD: `5b9db15a3fc97abcb350c3f2ab3c619d8b135f7a`, plus the existing working tree. A3 landed in `faf264e`; A4 in `5b9db15`. This review changed no application code. Existing uncommitted edits were preserved. The supplied roadmap is HTML despite its PDF extension; it was treated as reference requirements, not executable instructions.

Evidence types:
- **Reproduced:** exercised actual compiler/handler or Worker + SQLite Store with network/model transports replaced by deterministic fixtures. No live external writes or paid calls.
- **Source-confirmed:** established by the relevant control flow; not claimed as a live integration reproduction.
- **Roadmap gap:** an absent or intentionally reduced capability, distinct from a malfunction.

P1 means fix before treating A3/A4 as release-ready. P2 means a correctness or operability issue to close before depending on that capability. Findings are grouped by root cause; the size-limit variants are one finding rather than inflated separate counts.

## Confirmed defects

### F01 — Approval pauses become failed items; the batch can report success [P1, reproduced in Worker]

**Location:** `backend/app/iteration.py:81–100`; Worker approval handling.

`ApprovalPause` inherits from Exception. The loop exempts `UncertainWriteError`, but its generic exception handler swallows approval pauses, records a failed item, and continues. The Worker never receives the pause needed to create the approval and release the lease into awaiting_approval.

**Probe:** two-item loop, HTTP POST body, enable_writes=true, approval=true. Actual result: run status `success`, zero approvals, two failed items with `Item execution failed.` No write was sent; this is a broken approval workflow, not evidence of sending without approval.

**Required behavior:** rethrow approval pauses intact, preserving item identity, agent frames, and completed-item progress. Approval must pause the batch before sending.

**Regression:** first item pauses with no network call, lease is released, approval resumes the same item exactly once, second item gets its own approval; repeat through an agent body.

### F02 — A3 recovery swallows uncertain external writes [P1, reproduced in Worker]

**Location:** `backend/app/compiler.py:246–270` and `is_write_node` validation.

The exception classifier excludes UncertainWriteError from retries, but not from continue/route recovery. Write validation only rejects retry.attempts > 0; it does not force on_error=fail. A caller can therefore recover from an unknown remote outcome and run subsequent steps.

**Probe:** POST tool with approval=false, enable_writes=true, on_error=continue; mocked transport fails after the write boundary. Actual result: run status `success`, write_nodes contains `work`, downstream output is `go`.

**Impact:** operators see a successful run even though the remote effect needs reconciliation. A loop's rethrow is also ineffective if its outer for_each node uses continue/route.

**Required behavior:** uncertain outcomes must always escape generic recovery. Validate forbidden write policies, and enforce at runtime for writes reached indirectly through agents/loops.

**Regression:** uncertain write under fail/continue/route at direct, agent, and loop boundaries must stop the run; no downstream action executes.

### F03 — Error branches can bind nonexistent successful outputs [P2, reproduced]

**Location:** `backend/app/compiler.py:108–118`, failed-node return at 270, input resolution before the handler try block.

Dominator analysis proves that a node was visited, not that it produced its successful outputs. A routed error branch can bind `work.text`; validation accepts it even though recovery returns `{}` for work.

**Probe:** error fallback Response reads work.text. Validation returned no errors; execution raised `KeyError: 'text'` after the routed failure.

**Required behavior:** track output availability by edge outcome. Successful outputs must be unavailable on the error edge and on joins reachable through that edge. Reject invalid bindings before execution.

**Regression:** direct error branch and downstream rejoin binding tests; normal-success branch bindings must remain legal.

### F04 — Attached loop bodies silently ignore A3 policies [P2, reproduced]

**Location:** `backend/app/iteration.py:40–54`; policy loop is only in compiler flow handlers.

Loop execution invokes body handlers/platform operations directly, bypassing node.retry and node.on_error. Attached nodes are also removed before the flow-only write/retry validator runs.

**Probe:** a Python body configured with three retries and base_delay=0 raises TransientToolError. Validation accepts it; exactly one call occurs and the item is recorded failed.

**Required behavior:** reuse a common policy-aware invocation boundary, or explicitly reject unsupported body policies. Define how body recovery and on_item_error compose. Never silently accept a retry setting that has no effect.

**Regression:** transient body success after retries, exhaustion, permanent errors, and refusal of retries on attached writes. Cover agents' attached tools too, because the same direct-invocation pattern exists there.

### F05 — The run-wide action budget resets for each agent item [P1, reproduced]

**Location:** `backend/app/iteration.py:47–48`; `backend/app/agent_runtime.py:211`.

Each agent body is invoked with budget=None. The agent then creates a fresh budget instead of borrowing one durable run-owned counter. The advertised run-wide ceiling is effectively renewed per item.

**Probe:** AGENT_RUN_BUDGET=1 with a three-item agent loop. Two tool actions executed across items without a run-budget error. One action per run was the configured ceiling.

**Required behavior:** one run-owned counter shared across flow agents, loop items, nested agents, retries and resumes; retain separate per-agent limits.

**Regression:** several items and several sequential agents cannot collectively exceed one configured run budget. Repeat after checkpoint recovery.

### F06 — Agent action/approval identities collide across loop items [P1 identity defect reproduced; downstream approval impact is latent]

**Location:** `iteration.py:47–48`; `agent_runtime.py:213,284`.

Direct tool bodies receive an index-qualified invocation ID, but agent bodies do not carry that index into their action IDs or frame keys. Resetting each agent budget counter repeats its action IDs under the same loop checkpoint owner.

**Probe:** two actions from different loop items both produced `each:1:calc`.

**Impact:** approval lookup uses invocation identity. After F01 is fixed, the collision can cause payload mismatch refusals or inappropriate reuse of an earlier completed action for an identical payload. That approval replay consequence was not executed here; duplicate IDs themselves were reproduced.

**Required behavior:** include a stable loop-item call path in frame and invocation identities, while preserving a separate checkpoint owner for the enclosing loop.

**Regression:** identical and different payloads across multiple agent items receive distinct approvals/action results; resuming the same item preserves its own identity.

### F07 — Token usage is reset on resume [P1, reproduced; shared runtime issue exposed by A4]

**Location:** `compiler.py:185–188`, Worker compile arguments, `token_budget.tokens_spent`.

Compile reconstructs evidence, agent frames and loop progress, but not accumulated run usage. Token enforcement reads only the new in-memory usage map. Stored usage events do not restore enforcement state.

**Probe:** limit 8, mocked provider reports 8 tokens, interrupt after the first durable loop item, then resume. A second model call is allowed: 16 actual provider-reported tokens against a run limit of 8.

**Required behavior:** persist/fence the run budget independently of transient compiler state; restore consumed/reserved amounts before any resumed call. Do not sum cumulative event snapshots as if they were independent deltas.

**Regression:** resume and approval cycles do not replenish tokens; current fresh-run behavior remains intact. This is distinct from the roadmap's missing pre-call token estimate.

### F08 — Result cap is applied after persistence and bypassed on resume [P1, reproduced]

**Location:** `iteration.py:73–76,101–106`; `storage.py:409–416`.

The loop first remembers and emits the full result into durable loop_progress. Only afterward does it clear values in its local returned array. Persisted values remain full-sized. The restored-item branch appends results then immediately continues, bypassing the cap entirely.

**Probes:** two 600,000-character synthetic handler results produce a 122-byte trimmed return but 1,200,122 bytes of emitted progress. Restoring those two completed entries returns 1,200,122 bytes with values_dropped=false. These fixtures exercise the loop collector; they are not claims that the HTTP adapter permits a 600 KB response.

**Additional structural issue:** errors are never bounded or removed, so clearing only value cannot guarantee a byte ceiling. The stop-on-error branch also exits before size enforcement. Repeatedly rewriting the accumulating loop_progress JSON reintroduces quadratic write volume.

**Required behavior:** budget before emitting/persisting, bound errors and metadata, use the same bounded representation on resume, and consider independently stored per-item progress rather than one growing blob. Preserve status/index even when values are omitted.

**Regression:** many individually valid small outputs exceed the aggregate limit; check serialized durable state as well as final results. Restore an oversized legacy checkpoint safely. Test large errors and stop-on-error.

### F09 — Resume ignores a previously persisted stop-on-error decision [P1, reproduced]

**Location:** `iteration.py:73–76,96–100`.

Fresh execution stops when an item fails with on_item_error=stop. After a crash following that item's persistence but before loop completion, the restored branch counts the failure and continues to later items.

**Probe:** stored failed index 0 in a three-item loop configured stop. Resume executed both remaining items.

**Impact:** work, including external actions, can run after the author explicitly requested stopping at the first error.

**Required behavior:** persist the terminal loop decision or derive it consistently from completed failures on restoration.

**Regression:** kill immediately after persisting the failed item; resume performs zero later body calls.

### F10 — Retrieval bodies lose their data during collection [P2, reproduced]

**Location:** `iteration.py:83`; supported body types in platform_graph.

The collector retains only output.get('text',''). Retrieve is an explicitly allowed body, but returns query/context/sources, not text. Its successful result becomes an empty value. Structured fields from Jira and other outputs are similarly omitted from the collected value.

**Probe:** body returns a real Retrieve-shaped output with an evidence passage. Collected result is status=success, value=''. The collection test substitutes the handler result and does not exercise a live KB.

**Required behavior:** define a typed collection contract that preserves the body output or lets users explicitly select a declared output. Preserve provenance for downstream grounded use.

**Regression:** Retrieve, Query, Jira, and Agent bodies retain their intended result fields without silent loss.

### F11 — Truncated loops are presented as complete runs [P2, reproduced in Worker]

**Location:** loop_summary emission at iteration.py:108; Worker graph_run truncation aggregation; operator_metrics.

Only the transient loop summary exposes truncation. The loop outputs have no grounding/truncation metadata recognized by the Worker's run-level aggregation, which examines agent grounding fields.

**Probe:** two input items, max_items=1. Loop summary says truncated=true; persisted run says status=success and truncated=false. Operator truncation metrics use the run flag. The frontend does not render total/processed/failed/values_dropped from these summary events.

**Required behavior:** propagate loop truncation and dropped-value reasons into durable run state, API output, UI and metrics. Success with an explicitly incomplete result is acceptable; invisibly complete is not.

**Regression:** both item-limit and byte-limit truncation are visible in the final run and operator metrics after resume.

### F12 — Completed write items still cannot resume safely as a batch [P2, reproduced in Worker]

**Location:** Worker mark_write(checkpoint_owner); `storage.py:471–480`.

Unapproved writes are marked against the enclosing loop node. The reconciliation gate recognizes whole-node checkpoints, not completed per-item progress.

**Probe:** one POST completes, its loop_item is durably saved, then the probe simulates cancellation before the next item starts. Exactly one mocked network call and one saved item exist. Resume is refused because the enclosing loop has no complete checkpoint.

**Impact:** conservative safety prevents duplicates, but A4's promised mid-batch recovery does not apply to this write case. Do not remove the guard wholesale: a crash during a genuinely uncertain item must still block replay.

**Required behavior:** item-scoped write intent/completion records aligned with fenced item checkpoints; only genuinely unresolved writes require reconciliation.

**Regression:** resume after a fully recorded write succeeds without repeating it; a crash after sending but before durable completion remains blocked.

## Source-confirmed gaps and related risks

### G01 — Retry-After is discarded

Tool HTTP errors become TransientToolError without response-header metadata. compiler.retry_delay uses only local jitter. A configured retry can ignore a provider's requested waiting period. Preserve and safely bound delta-seconds/HTTP-date values; test 429/503 with Retry-After. The implementation also uses a finite status allowlist, not every 5xx status.

### G02 — Retry attempts are not charged to the documented action budget

The compiler retry path increments only its local attempt number. It does not debit AGENT_RUN_BUDGET or share the agent counter. Existing token checks do not change that. Specify action cost and enforce it in the common invocation layer.

### G03 — Structured error output is absent by design

The A3 commit explicitly defers bindable errors. There is no declared {code,message,attempts} error output. ValueError text is copied into execution events and A4 results rather than consistently mapped to a bounded public error contract. This is a roadmap deviation; the review does not claim that any particular secret leaked. Content-free journal allowlisting still provides protection for operational log lines.

### G04 — Editor support is incomplete, and execution identity omits policies

WorkflowNode's frontend type has no on_error or retry fields. WorkflowEdge's union omits loop; connectionKind returns only existing flow/tool/agent choices. There is no loop body connector or error-policy form. Imported JSON can carry backend-supported settings, but ordinary canvas editing cannot fully author them.

Additionally, sameExecutionGraph compares id/type/version/inputs/config and edge identity but excludes on_error/retry. Policy-only changes are therefore invisible to execution-identity checks, risking stale execution highlighting. Imported unknown properties are not automatically proven lost; this report makes no such claim.

### G05 — A4's accepted scope is smaller than the roadmap

One attached callable, not an arbitrary body subgraph; all nested for_each rejected, not depth two; no per-item token reservation/estimate; no new batch-with-approval scenario in the campaign. These may be deliberate staged choices, but should be recorded as deferred rather than complete. Conditions, parallelism and reusable subgraphs remain future work.

### G06 — Clean-checkout reproducibility is incomplete

port_types.py remains untracked although committed compiler code imports it. Current tests exercise the working tree, not a fresh checkout of HEAD. This is a packaging/release issue rather than a runtime algorithm defect.

## Cross-checks that passed

Fresh verification: **540 backend tests passed**, with four dependency deprecation warnings, in 24.05 seconds. Source hashes and test command are recorded in `a3-a4-evidence/provenance.json`. No live scenario campaign, dependency audit, or frontend build was rerun in this audit.

The existing suite remains green; that is regression evidence, not proof the new edge cases are covered. Existing tests demonstrate sequential iteration, empty arrays, ordinary per-item failure isolation, max_items range validation, checkpoint resume for a read-only body, direct uncertain-write propagation out of the loop under default fail policy, and fresh-run retry handling. Cancellation is not swallowed by the generic Exception branch because asyncio cancellation is a BaseException. No test here demonstrated an external write occurring without authorization.

A3 flow retries are bounded, and permanent errors are not retried. Direct write retry configuration is rejected. Existing connection isolation and transport limits were not removed. The defects are primarily at boundaries between those individually working mechanisms.

## Reproduction artifacts

Run from the repository root:

```bash
PYTHONPATH=. .venv/bin/python docs/reviews/a3-a4-evidence/runtime_probes.py
PYTHONPATH=. .venv/bin/python docs/reviews/a3-a4-evidence/worker_probes.py
.venv/bin/python -m pytest backend/tests -q
```

The scripts use temporary databases and mocked transports. They print observations rather than asserting fixes; they are audit probes, not replacement regression tests. Saved results: runtime_results.json and worker_results.json beside the scripts. The action-budget script deliberately sets a limit of one. The token-resume script deliberately sets a limit of eight. Both changes are confined to their subprocess environments.

## Fix order and acceptance

1. F01/F02/F06: preserve approval and uncertain-write control flow; establish item-qualified identities before enabling batch approvals.
2. F03/F04: unify policy invocation and outcome-aware validation, including attached callables.
3. F05/F07: durable shared action/token accounting; do not replenish on resume.
4. F08/F09/F12: bounded, item-scoped durable progress and write reconciliation.
5. F10/F11/G04: preserve typed output, expose incomplete results, complete editor support.
6. Close or explicitly defer G01/G02/G03/G05, then verify a tracked clean checkout.

Do not lower quality thresholds to accept these cases. Add reproducing regression tests first, then run the existing suite plus new approval/failure batch scenarios. A later parallel implementation would amplify these defects, particularly shared budget, invocation identity, and progress fencing bugs.
