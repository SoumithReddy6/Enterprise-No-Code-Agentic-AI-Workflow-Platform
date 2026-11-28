# Relay defects found by the 2026-11-16 hunt

Code under test: source equal to commit `24e659c` (no source file changed since). Method: read
the claim, heartbeat, fence, finish, cancel, approval, resume, loop, Agent and tool paths in full,
trace interleavings by hand, then reproduce each suspect with a test.

- Each defect below has a test under `backend/tests/defects/`, marked
  `@pytest.mark.xfail(strict=True, reason="<id>")`. Each test was first run with `--runxfail` and
  **failed for the stated reason**. With the marker, the directory gives `9 xfailed`.
- When a defect is fixed, its test passes, the strict marker turns that into a failure, and the
  fixer removes the marker in the same commit.
- No source code was changed.

Run them:

```bash
.venv/bin/python -m pytest backend/tests/defects -q -rx
.venv/bin/python -m pytest backend/tests/defects -q --runxfail   # shows each failure
```

## Summary

| Id | Severity | One line | Test |
|---|---|---|---|
| DEF-01 | **Critical** | **Fixed in `f3771b3` (2026-11-27).** An Agent's settled write is sent a second time on resume when the model changes the step order | `backend/tests/test_agent_write_resume.py::test_def01_…` |
| DEF-02 | High | **Fixed in `f3771b3` (2026-11-27).** A stored write result is returned for a different request; the new request is never sent and is reported as success | `backend/tests/test_agent_write_resume.py::test_def02_…` |
| DEF-03 | High | **Fixed (2026-11-27).** Validation takes exponential time; one request stops the API process | `backend/tests/test_validation_cost.py` |
| DEF-04 | High | A run whose worker dies on every attempt is claimed without limit | `test_def04_def05_def07_claim.py::test_def04_…` |
| DEF-05 | Medium | A cancelled run is claimed and started again | `test_def04_def05_def07_claim.py::test_def05_…` |
| DEF-06 | Medium | Agent memory is written with no fence and outside the checkpoint commit; a resume writes it twice | `test_def06_memory_write.py` |
| DEF-09 | Medium | An error in the heartbeat query leaves the graph task running with no supervisor | `test_def09_heartbeat_error_orphans_task.py` |
| DEF-07 | Low | One job row with no run row blocks the whole queue | `test_def04_def05_def07_claim.py::test_def07_…` |
| DEF-08 | Low | A saved workflow can hold a setting far over its declared limit | `test_def08_workflow_save_size.py` |
| DEF-10 | Medium | Found while fixing DEF-03. `static_label` is still exponential when the bindings form a cycle; one request holds a validation thread | `test_def10_static_label_cycle.py` |

Fix order: DEF-01 and DEF-02 together (one root cause), DEF-03, DEF-04 with DEF-05 and DEF-07 (all
in `claim_next`), DEF-09, DEF-06, DEF-08. DEF-10 (found 2026-11-27) needs a decision first.

---

## DEF-01 — A settled Agent write is sent again on resume (Critical)

This breaks the README's first promise ("never send an external write twice", `README.md:3`).

**Root cause.** The identity of a tool call made by an Agent is its step position:
`invocation = f'{identity_base}{identity_join}{step+1}:{target}'` (`backend/app/agent_runtime.py:346`).
A settled write is found again only by that id (`backend/app/worker.py:198-199`,
`backend/app/storage.py:820-824`). An Agent saves its steps only at an approval pause
(`agent_runtime.py:257,408`). After any other interruption it starts again at step 0 and asks the
model again (`agent_runtime.py:306`). The comment at `agent_runtime.py:261-265` says that "a root
agent's Nth step … is the same step on every run". That is true only if the model gives the same
answers again, and nothing makes it do so.

**Trigger (exact sequence).** Workflow: Input → Agent with one attached `tool_http` POST,
`approval: false` (or sign-in off, where approval is off by default, `worker.py:175`,
`main.py:159`).

1. Attempt 1, step 0: the model answers `call post X`. The worker marks the write
   (`worker.py:203`), sends X, and settles it under `agent:1:post` (`worker.py:211`,
   `storage.py:826-846`). The marker is cleared.
2. Attempt 1, step 1: the model call fails (provider error), or the worker is killed. The Agent
   has no checkpoint. The run is `failed`, with no unsettled write, so resume is allowed
   (`storage.py:862-864,873-874`). After a kill, the next claim does the same without the API.
3. Attempt 2, step 0: the model answers differently. In the test it gives an empty input, which
   is a tool error that only advances the step (`agent_runtime.py:339-340`). Any other first
   call has the same effect.
4. Attempt 2, step 1: the model answers `call post X`. The invocation is now `agent:2:post`.
   `completed_action` finds nothing. X is **sent a second time**.

**Evidence.** The test's receiver records `['{"ticket": "X"}', '{"ticket": "X"}']`.

**Also affected (same cause, not tested).** An Agent used as a For each body
(`iteration.py:328-329` gives the owner `loop:3`; the calls are `loop:3:1:post`). With approval
on, the shifted step finds no approval (`worker.py:177`, by invocation), so a second approval is
requested for a request that was already sent; a person who approves it sends it twice.

**Suggested fix.** Save the Agent's position in the **same commit** that settles a write, and
continue from it on resume. The approval pause already does this: it stores a frame for each
Agent on the call path (`agent_runtime.py:408`; `approvals.py:92`), and a resumed Agent replays
the saved step with the saved action and invocation, without asking the model
(`agent_runtime.py:304-306,329,346`). Do the same at `settle_write`: each Agent registers its
frame before it calls a target; the worker passes the open frames to `settle_write`, which writes
them into `agent_frames` beside the result (`storage.py:826-846`). Then a resumed Agent replays
the step of its last settled write, gets the stored result (`worker.py:198-199`), and continues
with the transcript that shows the write as done. The model is never asked again for a step
before a settled write. Design and steps: `docs/tasks/01-DEF-01-02-agent-write-resume.md`.

A model that asks for the same write again **after** it saw the result is a different thing: it
is a new action, and Relay cannot tell it from an intended second write. The test's model
therefore gives its final answer when its transcript shows the write as done.

**Test.** `backend/tests/test_agent_write_resume.py::test_def01_a_settled_agent_write_is_not_sent_again_when_its_step_moves` (moved from `backend/tests/defects/`; no xfail).

**Fix (2026-11-26).** As designed above, with one change of channel: the open frames travel in a
`ContextVar` (`agent_runtime.OPEN_FRAMES`), not as a sixth argument of `ctx.platform('tool', …)`.
The argument broke every `platform` with a fixed signature (test doubles and any outside
resolver). A `ContextVar` also limits the frames to the current call path without a filter: an
asyncio task copies its context, so a parallel branch never sees another branch's frames. The
worker reads them (`worker.py`, tool action) and `settle_write` writes them into `agent_frames`
in its one commit; `approvals.complete` does the same, and `approvals.pause` now merges its
frames with the saved ones instead of replacing them. A `loop_item` event drops the frames of
its item owner (`storage.py`, `worker_event`). Mutants M00–M11 (`evals/results/def01-02-2026-11-27/`):
the control passes, and M01–M06 and M09–M10 are killed. M07 (`complete` saves no frames), M08
(`pause` replaces the saved frames) and M11 (a finished item clears every frame) survive the
whole suite. They change only frames that are not on the current call path, and no reachable
workflow has such a frame: a parallel branch cannot write, and For each runs its items in order.
They are kept as defensive code.

**Gates (2026-11-27).** Backend 2,176 passed and 7 xfailed at PYTHONHASHSEED 0–4 with
PostgreSQL; the deployed gate 35 of 35 three times. The first CI run of `f3771b3` failed `scripts.scenarios.request_enforcement`: its
test-only wrapper `request_review_worker.py` took no keyword arguments, and `settle_write` now
gets `payload_sha256`. The wrapper is fixed in the commit after `f3771b3`. Evidence:
`evals/results/def01-02-2026-11-27/README.md`.

## DEF-02 — A stored write result is returned for a different request (High)

**Root cause.** The same as DEF-01, in the other direction. The write path without approval
returns the stored result when the invocation id matches, and compares nothing
(`worker.py:198-199`). The approval path does compare the rebuilt payload with the reviewed one
(`worker.py:181-183`).

**Trigger.** Steps 1 and 2 of DEF-01. Then on resume the model answers `call post Y` at step 0.
The invocation is `agent:1:post` again. The worker returns the result of X. Y is never sent. The
Agent gets "accepted", the event log shows a tool success, and the run ends `success`.

**Evidence.** Run status `success`, a `post` success event, receiver got only X.

**Suggested fix.** Store the payload digest with the result (`ActionResultRecord`,
`storage.py:99-110`). On a hit with a different digest, raise a typed error that stops the run
("the resumed action differs from the one that was sent"), as the approval path does.

**Test.** `backend/tests/test_agent_write_resume.py::test_def02_a_stored_write_result_is_not_returned_for_a_different_request`.
With part A this test passes for the right reason (the frame replays X; Y is a new step and is
sent). `test_a_different_request_at_the_same_invocation_stops_the_run` forces the state with no
frame (a run settled by an older build) and checks part B.

**Fix (2026-11-26).** Revision `0007_action_result_digest` adds `run_action_results.payload_sha256`.
The worker stores `approvals.digest({payload, connection_id})` of the prepared request with the
result. On a stored-result hit with a non-NULL digest that differs, it raises
`WriteReplayMismatch` (in `CONTROL_FLOW`), so the run fails with "The resumed action differs
from the request that was already sent. Start a new run." A NULL digest (older row) is served
as before.

## DEF-03 — Validation takes exponential time (High)

**Trigger.** A contract-2 workflow with a chain of n nodes where each node binds the previous node
twice, for example `{"message": "p0.text", "x": "p0.text"}`. The extra key is reported as an
error, but the walk runs first.

**Evidence.** `unchecked_model_input` uses a per-path `seen` set and `any(...)` over all inputs
(`backend/app/compiler.py:478-502`). `request_contract_errors` calls it for each node
(`compiler.py:450`). Profile at n = 16: 393,179 calls. Measured wall time: n = 16 0.32 s, n = 18
1.36 s, n = 20 5.63 s (×4 for each two nodes). The limit is 100 nodes
(`backend/app/models.py:43`). `/api/validate` and `/api/runs` call it synchronously inside
`async def` routes (`backend/app/main.py:112-114,156-157`), so one signed-in request blocks the
event loop: every other request, and with the embedded worker (`main.py:58`) every heartbeat.
Contract 1 is not affected (measured: 0.0 s at n = 18).

**Suggested fix.** One visited set for each top-level call, with memoization by
`(ref, stop_at_sources)`; the same in `calls_model` (`compiler.py:497-500`). Review
`provenance.static_label` (`provenance.py:536-544`), which also passes a per-path set. Run
validation with `asyncio.to_thread`. Add a hard node-visit budget that fails closed.

**Test.** Was `backend/tests/defects/test_def03_validation_exponential.py` (a child process with a
10 s limit, so the test cannot hang the suite). Now `backend/tests/test_validation_cost.py`.

**Two more walks (2026-11-27).** Measured before the fix, on the same doubled chain:
`provenance.static_label` and `compiler.checked_dependency` (`compiler.py:514-521`, not in the
card) also make 2**(n+1) − 1 calls (n = 18: 524,287 calls each). A chain from the reader's
checked text into a Switch reaches `checked_dependency`; a chain from the trigger reaches
`static_label`. The user added `checked_dependency` to DEF-03 (2026-11-27).

**Fix (2026-11-27).** Each walk keeps one visited set (or one path) for each top-level call and
remembers only complete answers. The meaning of each walk does not change:

- `unchecked_model_input` is reachability: some walk over inputs (stopping at a verifier's
  `text`, and with `stop_at_sources` at an independent read) or attachments reaches a model. A
  node met again adds nothing, so one visited set is enough. `memo` keeps answers by
  `(state, stop_at_sources)` across the calls of one validation (`request_contract_errors`
  makes one): a true when found, and a false only for the states of a call whose answer is
  false. A state visited in a call that found a model (it can have been on the current path) is
  not remembered. `calls_model` is the `('calls', id)` state of the same walk. Runtime
  (`request_safety.py`) calls the same function, with a fresh memo for each call.
- `checked_dependency`: a node met again on the path is false, and `all` passes that false to
  every node on the cycle, so every answer is complete and is remembered by node.
- `static_label`: a reference met again on the path is guessed, so an answer that met a
  reference higher on the path depends on the path. The memo keeps only the other answers, by
  `(reference, field)`. The depth limit (`len(seen) > len(nodes) * 4`) also depends on the path:
  when the graph has more distinct references than the limit allows, nothing is remembered.
- `/api/validate` and `/api/runs` run `validate_workflow` (and the warnings) with
  `asyncio.to_thread`. `/api/runs/{id}/resume` does not validate the graph (no change).
- No hard node-visit budget (not in the card, user decision).

**Gates (2026-11-27).** Before the fix, `backend/tests/test_validation_cost.py` gave 10 failed and
5 passed on the parent code (the 5 are meaning tests that must pass there). After: n = 100
validates in 1.5–2.3 ms (best of 5); each walk on the doubled chain makes 2n + 2 calls (parent:
2^(n+1) − 1). The same errors and warnings as `88d4d8b` for 69 workflows (60 from
`test_request_contract_validation.py` and `test_switch.py`, 9 from `examples/`), and the same
walk answers on 600 random graphs. Backend 2,191 passed and 6 xfailed at PYTHONHASHSEED 0–4 with
PostgreSQL; deployed gate 40 of 40. Mutants M00–M12: the control passes; M01–M07 and M09–M12 are
killed; M08 (the depth-limit guard always on) survives, no counterexample found. Evidence:
`evals/results/def03-2026-11-27/README.md`.

## DEF-10 — `static_label` is exponential on a binding cycle (Medium)

**Trigger.** The DEF-03 chain into a Switch, where the first Prompt also binds the last one:
`p0.inputs.x = "p39.text"`. Validation reports `invalid binding x = p39.text`, but the Switch
rule runs first.

**Evidence.** `static_label` calls a reference met again on its path guessed. After the DEF-03
fix the memo keeps only answers that met no higher reference on the path; here every answer
meets the root, so none is kept. Measured after the DEF-03 fix
(`evals/results/def03-2026-11-27/timing/cyclic.txt`): n = 14 0.020 s, n = 16 0.081 s,
n = 18 0.331 s (×4 for each two nodes). `checked_dependency` and `unchecked_model_input` take
0.0 s on the same graph. The event loop answers (validation runs in a thread), but one request
holds a thread and a CPU.

**Suggested fix.** Needs a decision: a rule for a cycle (for example, do not run the trust walks
when a binding is invalid, and report the binding), or the hard node-visit budget of DEF-03.

**Test.** `backend/tests/defects/test_def10_static_label_cycle.py` (a child process with a 10 s
limit).

## DEF-04 — A run is claimed without limit (High)

**Trigger.** A run that kills its worker on every attempt (out of memory, a native crash), or
that makes `Worker._execute` raise before its handlers (for example a run document that cannot
be read at `worker.py:69`). The job stays `running`; its lease expires; `claim_next` offers it
again (`backend/app/storage.py:671-676`). `execution_jobs` has no attempt column
(`storage.py:112-119`).

**Evidence.** The test claims the same run 25 times in a row; it is still offered.

**Suggested fix.** `attempts` column, increased in the claim transaction. Over the limit
(default 5), the claim finishes the run as `failed` with the cause `max_attempts` and does not
return it. A run with an unsettled write keeps today's rule. This is W07 (d) of the audit.

**Test.** `…test_def04_def05_def07_claim.py::test_def04_a_run_that_never_finishes_is_not_claimed_without_limit`

## DEF-05 — A cancelled run is claimed and started again (Medium)

**Trigger.** 1. A worker holds the run and dies. 2. The user cancels: `request_cancel` only sets
`cancel_requested` when the job is `running` (`storage.py:779-780`), and the API returns
`running` (`main.py:176-184`). 3. The lease expires. 4. `claim_next` does not read the flag
(`storage.py:671-676`): it sets the run `running`, writes a `running` event and returns it.
5. The new worker starts the graph task (`worker.py:242`) and cancels it only when its first
cancel poll returns (`worker.py:245`).

**Evidence.** The test's claim returns the run. In 5 probe runs with a write node, the first node
emitted `running` and the write was **not** sent: the cancel poll won the race each time. So the
proven effect is "a cancelled run starts again and runs its first step". A send in that window
is possible by timing but was not reproduced.

**Suggested fix.** In the claim transaction: when `cancel_requested` is true, finish the job and
the run as `cancelled` (the same writes as `storage.py:784-788`) and continue with the next
candidate.

**Test.** `…::test_def05_a_cancelled_run_is_not_claimed_and_started_again`

## DEF-06 — Agent memory is written with no fence and outside the checkpoint (Medium)

**Trigger.** An Agent with the `memory` role. `agent_runtime.py:486-487` calls `memory_write`
before the Agent returns. `worker.py:219` passes it to `MemoryService.write`
(`backend/app/agent_memory.py:19-27`): its own transaction, no lease check. The Agent's
checkpoint commits later (`storage.py:703-738`). When that commit fails once, or the worker dies
between the two, the resumed Agent runs again and writes a second entry. A worker that lost its
lease can also still write.

**Evidence.** One run, one resume, 2 memory entries.

**Effect.** Duplicated memory for one run; the second attempt also reads the first attempt's
entry (`agent_runtime.py:291-292`). It is the only worker write path found that does not go
through the fence (design rule 2, `README.md:73`). The rest of the fenced paths were checked:
`storage.py:688-691` is the first statement in each.

**Suggested fix.** Return the memory entry as part of the Agent's outputs and write it inside
`worker_event`, in the checkpoint's transaction; or fence the write and key it by `(run, node)`
so that a repeat is an update.

**Test.** `backend/tests/defects/test_def06_memory_write.py`

## DEF-09 — A heartbeat error leaves the graph task running (Medium)

**Trigger.** `store.cancel_requested` or `store.heartbeat` raises inside the loop at
`worker.py:244-247` (a lost database connection, a lock timeout). The exception is not a
`CancelledError`, so the branch at `worker.py:264-269`, the only one that cancels the graph task,
does not run. The generic branch (`worker.py:270-274`) finishes the run as `failed` and returns.
The task made at `worker.py:242` is never cancelled or awaited.

**Evidence.** After `worker.execute` returned and the run was `failed`, the tool call in flight
ran to completion.

**Effect.** When `finish_run` works, the orphan task stops at its next commit (the fence refuses
it), after it completes the call in flight. When the database is down and then comes back within
the lease, `finish_run` fails too, the job stays `running` with a live lease, and the orphan task
continues through later nodes with no heartbeat; after the lease ends a second worker runs the
same nodes at the same time. Writes stay protected by the marker and the fence. Model calls are
paid twice.

**Suggested fix.** A `finally` after the loop that cancels and awaits the task unless it is done
(`task.cancel(); await asyncio.gather(task, return_exceptions=True)`), for every exit path.

**Test.** `backend/tests/defects/test_def09_heartbeat_error_orphans_task.py`

## DEF-07 — One job with no run blocks the queue (Low)

**Trigger.** A row in `execution_jobs` with no row in `runs`. No foreign key prevents it
(`storage.py:112-119`). `claim_next` updates the job, reads `row = s.get(RunRecord, id)`, gets
`None`, and raises at `storage.py:679`. The session rolls back, so the job is `queued` again. It
is the oldest candidate on every poll (`ORDER BY created`, `storage.py:673`), and the loop stops
at the first exception, so no other run is claimed.

**Evidence.** `AttributeError: 'NoneType' object has no attribute 'data'`; a valid run stays
queued.

**Why Low.** Relay has no code path that deletes a run today. Retention and deletion (audit W18)
create the path, so fix this before W18.

**Suggested fix.** The foreign key of audit W08, and in the claim: fail and skip a candidate
whose run cannot be loaded.

**Test.** `…::test_def07_one_job_without_a_run_does_not_block_the_queue`

## DEF-08 — A saved workflow can hold oversized settings (Low)

**Trigger.** `POST /api/workflows` with a node setting over its limit (a 2 MB `system` text; the
limit is 20,000 characters, `backend/app/registry.py:22`). `safe_draft` compares only key names
(`main.py:81-88`), and the document is stored (`main.py:122`). The API has no request size limit
of its own; the bundled proxy stops at 25 MB (`frontend/app/api/[...path]/route.ts:9`).

**Evidence.** Status 201. A run of the same workflow is refused (422), so the effect is storage
use and slow lists, not execution.

**Suggested fix.** Validate each node's config with its model on save, and add a body size limit
in the API.

**Test.** `backend/tests/defects/test_def08_workflow_save_size.py`

---

## Checked, no defect found

| Area | What was checked | Result |
|---|---|---|
| Two workers, one run | Compare-and-set claim; a new owner id for each claim (`storage.py:676`, `worker.py:292`) | Sound on one host |
| Stale worker, database writes | Every worker write runs `_fence` as its first statement: `worker_event` `:711`, `finish_run` `:753`, `release` `:766`, `reserve_accounting` `:798`, `record_accounting` `:811`, `mark_write` `:816`, `settle_write` `:838`, `record_vector_dependencies` `:742`; `approvals.pause` `:71`, `resolve` `:175`, `begin` `:185`, `complete` `:198`, `consume_review` `:210`, `checked_review` `:237` | Sound. One exception: DEF-06 |
| Stale worker, send in flight | Stall after `mark_write`, during the send, or before `settle_write`: the next claim finds the marker and fails the run (`worker.py:125`) | Sound: one send, run stops as uncertain |
| API writers against a worker | `request_cancel`, `resume_run`, `decide`, `expire_pending` take the job row lock first (`storage.py:774,868`; `approvals.py:121,145`) | Sound |
| Cancel, timeout or shutdown during a send | `CancelledError` is not caught by `except Exception` (`worker.py:192,207`); the marker or the `executing` state stays; resume is refused | Sound |
| Approve against expire, cancel, reject | State checks under the lock (`approvals.py:154-162`); `begin` checks expiry and cancel again (`:190-192`) | Sound |
| Approval digest | Sorted-key canonical JSON (`approvals.py:54-55`); a string that cannot be encoded fails closed at the pause; the request sent is the stored reviewed one (`worker.py:189`), so `1 == True` equality in the comparison at `worker.py:182` cannot change what is sent; a changed connection is refused (`tool_service.py:281-283`) | No bypass found. Note: the worker never compares the encrypted plan with `row.payload`; they come from one insert (`approvals.py:80`) |
| Loop items | A failed item is saved and not run again; an uncertain write never becomes an item row (`iteration.py:411-416`) | Sound |
| Tenant scope, core API | Every route takes the tenant from the session (`main.py:105-195`); run, events, approvals, credentials, connections, models filter on it | No leak found |
| Tenant scope, knowledge services | Segment and base lookups compare the tenant (`kb/search.py:98-104`; `kb/management.py:175`); file paths are hashes of trusted ids (`kb/search.py:109-114`) | No leak found through the API. See the hazard below |
| Retrieval filter | Keys limited to `filename`, `document_id` (`kb/options.py:14-19`) before `getattr` (`kb/search.py:230`) | Sound |
| Egress | Tool HTTP, SMTP and remote vector adapters resolve, require global addresses and pin (`tool_service.py:89-95,215-224,299-303`; `vector_adapters.py:47-54`) | Sound |

## Design hazards (not defects of today's single-user deployment; no test)

- **Knowledge RPC trusts the caller.** One shared key; the tenant and the permitted document list
  come from the caller (`kb/rpc.py:78,86-87`; `kb/search.py:223-229`). Spec:
  `docs/specs/signed-retrieval-resolution.md`.
- **Lease time is each host's wall clock** (`storage.py:670,690`). Spec:
  `docs/specs/concurrency.md`.
- **No idempotency on `POST /api/runs`** (`main.py:155-161`). A client retry makes a second run
  and a second set of writes.
- **The first account takes the workspace and operator access** (`auth.py:196-216,144-153`). Safe
  on loopback; unsafe on a public address before the first sign-up.
- **An account can be locked by anyone who knows its email**: 10 wrong passwords block it for
  15 minutes (`auth_security.py:83-90`).

## Not examined

Repeat item commits line by line (only the identity and the row commit were read,
`repeat.py:369-370,431-434`); parallel-branch interleavings on a real PostgreSQL; the request
reader and router; the editor.
