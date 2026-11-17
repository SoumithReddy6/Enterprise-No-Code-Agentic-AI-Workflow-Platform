# Relay defects found by the 2026-11-16 hunt

Commit under test: `167d53e` (source equal to `24e659c`). No source code was changed.
**This hunt is not complete.** The session stopped at its usage limit. One defect is confirmed
with a test. The others are reasoned from the code and have **no test yet**: treat them as
"suspected" until a test fails for the stated reason.

## Confirmed

### DEF-03 — Validation takes exponential time (High)

- **Trigger.** A contract-2 workflow with a chain of n nodes where each node binds the previous
  node twice (for example `{"message": "p0.text", "x": "p0.text"}`). The extra key is reported as
  an error, but the walk runs first.
- **Evidence.** `unchecked_model_input` uses a per-path `seen` set and `any(...)` over all inputs
  (`backend/app/compiler.py:478-502`). `request_contract_errors` calls it for each node
  (`compiler.py:450`). Measured: n=16 0.32 s, n=18 1.36 s, n=20 5.63 s (×4 for each two nodes).
  The limit is 100 nodes (`backend/app/models.py:43`). `/api/validate` and `/api/runs` call it
  synchronously on the event loop (`backend/app/main.py:112-114,156-157`), so one signed-in
  request stops the API process and, with the embedded worker, the heartbeats.
- **Test.** `backend/tests/defects/test_def03_validation_exponential.py` (strict xfail; it failed
  by the 10 s timeout, the stated reason).
- **Suggested fix.** Memoize the walk by `(ref, stop_at_sources)` with one visited set for each
  top-level call; do the same in `calls_model` (`compiler.py:497-500`). Run validation in a
  thread. Check `provenance.static_label` (`provenance.py:536-544`) for the same pattern.

## Suspected (reasoned from the code; test not written)

| Id | Sev | Trigger | Evidence | Suggested fix |
|---|---|---|---|---|
| DEF-01 | High | An Agent with a write tool and `approval: false` (or sign-in off). Attempt 1: step 1 sends write X; it settles under invocation `agent:1:T`. The run fails or the worker dies before the Agent checkpoint. On resume the Agent starts at step 0 again. If the model gives a different first step (another tool, or an empty input, which only advances the step), the same write X runs at step 2 with invocation `agent:2:T`, finds no stored result, and is **sent again** | Invocation is built from the step position: `agent_runtime.py:346`. No frame is saved except at an approval pause: `agent_runtime.py:257,408`. Lookup by invocation only: `worker.py:198-199` | Key the stored result by a digest of the prepared payload as well as the position; or save the Agent transcript after each settled write, in the settle commit |
| DEF-02 | High | Same setup. On resume the model calls the same tool at the same step with a **different** input Y. The worker returns the stored result of X. Y is never sent, and the Agent and the event log show success | `worker.py:198-199` returns `done` with no payload comparison. The approval path does compare (`worker.py:181-183`) | Store the payload digest with the result; refuse when it differs |
| DEF-04 | High | A run that kills its worker at each attempt is claimed again after each lease expiry, without end | No attempt column (`storage.py:112-119`); `claim_next` (`storage.py:669-686`) | W07 (d) in the audit |
| DEF-05 | Medium | `claim_next` hands out a job whose `cancel_requested` is true (cancel sent while the worker was dead). The new worker starts the graph and stops it only when its first cancel poll returns; a node can start in that window | `storage.py:671-676` has no cancel test; `worker.py:242-246` | In the claim, finish such a job as `cancelled` and do not return it |
| DEF-06 | Medium | Agent memory is written with no fence and before the Agent checkpoint. A crash after the write repeats it on resume; a worker that lost its lease can still write | `worker.py:219`; `agent_memory.py:19-27`; `agent_runtime.py:486-487` | Fence the write and commit it with the checkpoint |
| DEF-07 | Low | A job row with no run row (or a run whose document cannot be loaded) makes `claim_next` raise on every poll, and it is always the first candidate, so no later run is claimed | `storage.py:673-685` (`row` is `None` at `:679`) | Skip and fail such a job inside the claim |
| DEF-08 | Low | `POST /api/workflows` checks only the key names of a node's config, not the values, so very large values are stored | `main.py:81-88,122` | Validate with the node's config model on save |

## Checked, no defect found

- **Approval digest.** Canonical JSON with sorted keys; a non-encodable string fails closed at the
  pause; the request that is sent is the stored reviewed one, not the rebuilt one
  (`approvals.py:54-55,148-149`; `worker.py:181-189`). Defense-in-depth note: the worker never
  compares the encrypted plan with `row.payload`.
- **Cancel, timeout or shutdown during a send.** The marker stays, so resume is refused
  (`worker.py:203-212,264-268`; `storage.py:862-864`).
- **Fence order.** Each worker write runs the fence first in its transaction; API writers lock the
  job row first (`storage.py:688-691,774,868`; `approvals.py:121,145`).
- **Retrieval filter.** Filter keys are limited to `filename` and `document_id`
  (`kb/options.py:14-19`).

## Not examined (time ran out)

Repeat item commits in detail, the knowledge management dispatch, parallel-branch interleavings on
PostgreSQL, the event stream under load.
