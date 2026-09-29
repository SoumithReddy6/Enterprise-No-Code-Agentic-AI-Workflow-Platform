# Batch 1 audit and production scenario campaign — September 26, 2026

## Decision

The direct F01/F02/F12 safety scenarios now pass, including actual worker crashes. Batch 1 still has two integration gaps: loop-agent usage disappears from durable events, and a valid mixed-approval agent cannot resume. The broader A3/A4 campaign reproduces three additional defects. Keep the master gate red until these are fixed; the passing unit suite does not supersede these failures.

Audited HEAD: `add26492669e7e8ee690f884cac4b704c5480f0f`, with the existing working-tree changes recorded in the artifact. This is a working-tree audit, not certification of HEAD alone. Backend and harness source hashes were unchanged during the final campaign. No product fixes were applied as part of this audit. No stash, reset, or branch switch was used.

Evidence: [final report](../../evals/results/batch1-production-final/report.json), [persisted run histories](../../evals/results/batch1-production-final/runs.json), [receiver observations](../../evals/results/batch1-production-final/provider-requests.json), [live campaign](../../evals/results/batch1-production-final/live-models.json). The report contains 18 new scenario results plus six repository/integration stages.

## Remaining findings

### B1-01 — P2: loop agents lose durable model usage

**Reproduction L01:** authenticated API submission → Jira fixture with two issues → for_each → real `llama3.1:latest` agent → response. Both items complete successfully. The worker journal records actual `model.usage`, but the persisted run has no event containing `usage`. Run `2772bc3b2e8546938b1c458434b14c3d` demonstrates this with a real provider, not a mocked usage counter.

**Cause:** `registry.py:97` accumulates usage under `checkpoint_owner` (`each:0`, `each:1`). `compiler.py:232` looks under the enclosing node ID (`each`). The item success event in `iteration.py:92` does not carry usage either. Moving write ownership to the item exposed an accounting dependency on the old owner key.

**Impact:** SQL metrics based on run events undercount token spend. Restoring budget counters from those events cannot recover missing calls. This is missing durable accounting; the test does not claim that the provider call itself lacked a runtime token check.

**Repair target:** persist each item's usage exactly once, including pause/failure paths, with an explicit aggregation rule. Prove no double-counting after resume and that provider/model totals equal observed calls. Do not simply copy cumulative usage into every event.

### B1-02 — P2: mixed approval modes cannot resume within an item

**Reproduction M01:** an agent inside a loop completes a write with approval disabled, then requests approval for a second write. Before approval: `awaiting_approval`, `write_nodes=['each:0']`, no completed item. After approval: run fails with the external-write reconciliation message; only the first action was sent. The approval itself is recorded as approved.

**Cause:** `_write_settled` (`storage.py:478–488`) accepts an item only after its whole result is persisted. `check_resume_writes` rejects the incomplete owner before the saved agent frame can continue. An item owner is finer than a loop owner, but still coarser than an individual agent action.

**Impact:** a valid workflow cannot complete. This is a safe refusal, not an approval bypass or duplicate send. Do not remove the uncertainty check to fix it.

**Repair target:** durable action-level settlement that distinguishes a completed first write from an ambiguous in-flight write. Preserve the latter's hard stop. M01 is explicitly a deterministic real Worker/Store probe with scripted model/transport fixtures; it is not claimed as a deployed external-network reproduction. Its [log](../../evals/results/batch1-production-final/mixed-approval.log) records both states and observed calls.

### B1-03 — P2: the new F12 tests depend on hash ordering

`test_execution_safety.py:151` and `:175` compare `write_nodes` to an ordered list. `storage.py:475` constructs that list from a set, whose iteration order varies across processes.

Reproduce:

```bash
PYTHONHASHSEED=1 .venv/bin/python -m pytest backend/tests/test_execution_safety.py -q
```

Result: **2 failed, 3 passed**. The first full campaign also hit both failures; the final default-seed run passed all 546 tests. Compare sets if ordering is not part of the contract. These assertions can make CI fail despite correct settlement behavior.

Also, `test_batch_resumes_once_every_write_item_has_settled` completes a batch and checks settlement; it does not crash and resume it. S15 now supplies that missing process-level coverage. [Pinned-seed evidence](../../evals/results/batch1-production-final/hash-seed-1.log).

### A3/A4 defects still reproduced outside Batch 1

| Scenario | Observed behavior | Production consequence |
| --- | --- | --- |
| S07: retry a transient provider read inside a loop | One HTTP request; item fails on 503 despite body retries | A3 retry policy is bypassed by A4 direct body execution (`iteration.py:45–61`) |
| S08: fallback reads failed node's successful output | `/api/validate` returns `valid: true` for `fallback.text = work.text` | Invalid recovery binding is accepted before execution; no success value is guaranteed on that branch |
| S09: cap a larger batch with max_items | Run exposes `truncated: false` | Operator cannot rely on the run-level incomplete flag; loop_summary carries truncation but it is not propagated |

These are existing cross-phase problems, not asserted to be introduced by the four Batch 1 edits. The campaign keeps each failure visible rather than treating known defects as expected passes.

## What Batch 1 demonstrably fixed

- **F01:** three items create three distinct approvals, release their leases while paused, and send only the reviewed payload after approval. Wrong digests are rejected; repeat approval does not resend. Rejection produces zero sends.
- **F02:** a receiver accepts the write then drops the response. Both `continue` and `route` stop, do not execute downstream, and refuse unsafe resume.
- **F12:** after an abrupt exit immediately following the real item SQL commit, lease expiry and a replacement worker finish remaining items without repeating the settled write. If killed after receiver receipt but before a response, the write remains uncertain and is not replayed.
- **Checkout reproducibility:** `port_types.py` is now committed. Exporting HEAD with `git archive` into a temporary directory and importing the compiler succeeds. This is an import/packaging check, not a full clean-environment suite. [Evidence](../../evals/results/batch1-clean-checkout.json).
- **Original F06 invocation collision:** the item-owner change already separates the rechecked invocation IDs (`each:0:1:calc` and `each:2:1:calc`). The old per-item action-budget reset still reproduces; do not conflate that remaining F05 issue with the now-distinct IDs. [Probe artifact](../../evals/results/batch1-runtime-recheck.json).

Precision: the old swallowed ApprovalPause lost the pause/approval workflow; that alone is not evidence of an unauthorized external send. The current tests verify actual receiver observations.

## Verification results

| Check | Result |
| --- | --- |
| New production/contract scenarios | **13/18 passed**, S07/S08/S09/L01/M01 failed |
| Direct Batch 1 safety and crash cases | S01–S05, S15, S16 passed |
| Backend suite, final default-seed run | 546 passed |
| F12 tests with pinned hash seed | 2 failed, 3 passed |
| Frontend tests | 26 passed |
| Typecheck / lint / production build | Passed |
| Existing live Ollama/embedding/Docker/knowledge workflows | 15/15 passed |
| Master command | Exit 1, correctly rejecting failures |

Run the same campaign:

```bash
.venv/bin/python -u -m scripts.check_production_scenarios --all --live-models \
  --out evals/results/production-next
```

The [scenario guide](../production-scenario-testing.md) documents a lighter command without model dependencies and the catalogue for future phases. A manual GitHub Actions workflow has been added; it has not been executed remotely. It intentionally fails on the known defects rather than weakening assertions.

## Limits and next order

Seventeen new scenarios use real isolated API/worker processes, HTTP, auth and persistence; M01 is a separate Worker/Store contract probe. Jira/delivery are local receiver fixtures, not real external accounts. SQLite is tested, not PostgreSQL concurrency. The campaign does not certify browser behavior, all security properties, or the 53-question grounding quality gate.

Fix durable usage and mixed-approval settlement before declaring Batch 1 closed. Correct the order-sensitive test assertions. Then continue Batch 2 with run-wide action/token budgets and resume accounting, retaining all these regressions. Add PostgreSQL/two-worker, concurrent approval, expiry, knowledge-service failure/cleanup and migration profiles before claiming production readiness. No failing threshold was relaxed during this audit.
