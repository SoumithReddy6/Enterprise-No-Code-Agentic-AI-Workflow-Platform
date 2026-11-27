# DEF-01-02 — Agent write resume

| | |
|---|---|
| Order | 1 of 29 (before Phase 0: defect fix, Critical) |
| Closes | DEF-01, DEF-02 |
| Depends on | nothing |
| Effort | M (2–3 days) |
| Spec | This card (design below). Evidence: `docs/defects.md` DEF-01, DEF-02. |

## Goal

An Agent with a write tool must never send a settled write again on resume (DEF-01), and must never return a stored result for a different request (DEF-02). Today both happen: see `docs/defects.md`.

## Design (this card is its own spec)

**Root cause.** `agent_runtime.py:346` builds a tool call's invocation from the step position.
`worker.py:198-199` finds a settled write only by that invocation and compares nothing. An Agent
saves its steps only at an approval pause (`agent_runtime.py:257,408`). After any other
interruption it starts at step 0 and asks the model again. See `docs/defects.md` DEF-01, DEF-02.

**Part A — save the Agent's position in the settle commit (fixes DEF-01).**

1. In `_execute_agent`, before the `invoke` at `agent_runtime.py:374`, register this Agent's frame:
   `ctx.run.setdefault('open_frames', {})[frame_key] = {<the same dict that line 408 builds>}`.
   Remove the key in a `finally` after the outcome is handled (success, failure, or exception).
   A specialist is an Agent too, so each level of the call path registers its own frame; the dict
   then holds one frame for each Agent between the flow node and the tool.
2. *(Done differently on 2026-11-26: the frames travel in a `ContextVar`, `agent_runtime.OPEN_FRAMES`, because a sixth argument broke every `platform` with a fixed signature. See `docs/defects.md` DEF-01, Fix.)* `ctx.platform('tool', …)` gets the frames: add a sixth argument at the two call sites
   (`agent_runtime.py:36` passes `None`; `execution_policy.py:321` passes
   `dict(ctx.run.get('open_frames') or {})` when `ctx.run` exists, else `None`).
3. `worker.py:155` unpacks it (`node_type, settings, input_text, identity, *rest`; `frames = rest[0] if rest else None`).
   `worker.py:211` becomes `self.store.settle_write(id, owner, marker, result, frames=frames)`.
4. `Store.settle_write(…, frames=None)` (`storage.py:826-846`): when `frames` is a non-empty dict,
   set `row.data = {**row.data, 'agent_frames': {**row.data.get('agent_frames', {}), **frames}}`
   **in the same session, before the single `commit()`**. Nothing else changes in that method.
5. Resume needs no new code: `compile_workflow` receives `run['agent_frames']`
   (`worker.py:229`), the Agent pops its frame (`agent_runtime.py:257`), replays the saved step
   with the saved action and invocation and no model call (`:304-306,329,346`), and the worker
   returns the stored result (`worker.py:198-199`). `charge_first_attempt=not replay`
   (`agent_runtime.py:378`) already keeps the replay free.
6. Clean-up. A node checkpoint already drops the frames of its owner (`storage.py:736`). A loop
   item's owner is `<loop>:<index>` (`iteration.py:49-51`), which that filter never matches. In
   `worker_event`, when the event is a `loop_item` (`storage.py:730-734`), also drop frames whose
   `checkpoint_owner == f"{event['node_id']}:{event['item_index']}"`, in the same commit.
7. The approval path already saves frames at the pause. After an approved write completes, the
   frame was consumed. Apply the same rule there: `approvals.complete` (`approvals.py:196-203`)
   takes `frames` and writes them in its single commit; `worker.py:190` passes them.

**Part B — compare the request on a stored-result hit (fixes DEF-02).**

1. New nullable column `run_action_results.payload_sha256 String(64)` (its own Alembic revision,
   with downgrade). Old rows read `NULL`.
2. `settle_write(…, payload_sha256=None)` stores it. The worker computes it as
   `approvals.digest(prepared['payload'])` from the `prepare_write()` result it already has at
   `worker.py:202` (keep the value in a local).
3. `completed_action` returns `(result, payload_sha256)`; update its two callers
   (`worker.py:102,198`).
4. On a hit at `worker.py:198-199`: call `prepare_write()` for the current request; when the
   stored digest is not `NULL` and differs, raise a new exception
   `WriteReplayMismatch(ValueError)` (define it in `tool_service.py` next to `UncertainWriteError`)
   with the message `'The resumed action differs from the request that was already sent. Start a new run.'`.
   Add it to `CONTROL_FLOW` (`execution_policy.py:23`) so that an Agent cannot treat it as a tool
   error and continue. `tool_python` has no prepared payload: skip the comparison for it.

**Rejected.** (1) Key the result by a payload digest only: an Agent that intends two equal writes
could send only one. (2) Refuse any write whose payload equals an earlier settled one: the same
problem. (3) Make the model deterministic: not in Relay's control. (4) Save the frame in a second
transaction after the settle: a kill between the two reopens DEF-01.

**Failure modes.** A kill before the settle commit: nothing saved, the marker blocks resume (I7),
as today. A kill after it: the frame and the result are both there; the resume replays. A frame
from an older build has no new key: no change of shape, so none is needed. A run settled by the
old build has no frame: it behaves as today (DEF-01 remains for that run only); state it in
`docs/operations.md`.

**Rollback.** Code only for part A (frames written by the new build are ignored by the old one
except that it would replay them, which is the approval-path behavior it already has). Part B:
downgrade drops the column.

## Files to touch

- `backend/app/agent_runtime.py` (register and remove open frames around `:349-447`)
- `backend/app/execution_policy.py:23,321` (control-flow tuple; pass frames)
- `backend/app/worker.py:102,155,190,198-211`
- `backend/app/storage.py:99-110,730-736,820-846` (`ActionResultRecord`, `worker_event` clean-up, `completed_action`, `settle_write`)
- `backend/app/approvals.py:196-203`
- `backend/app/tool_service.py` (new exception)
- `backend/migrations/versions/<next>_action_result_digest.py`
- `backend/tests/defects/test_def01_def02_agent_write_resume.py` (remove the two `xfail` markers; move the file to `backend/tests/test_agent_write_resume.py`)

## Tests to write first

The two defect tests already exist and fail. Add, in `backend/tests/test_agent_write_resume.py`:

| Test name | Asserts |
|---|---|
| `test_settle_write_saves_the_open_frames_in_its_commit` | After the first attempt of the DEF-01 setup: `run['agent_frames']` has the key `agent:0:agent` with `step == 0` and the saved action; a spy shows one commit in `settle_write` |
| `test_a_kill_between_send_and_settle_saves_no_frame_and_blocks_resume` | Patch `settle_write` to raise: no frame, marker present, resume returns 409 |
| `test_resume_does_not_ask_the_model_for_the_settled_step` | On resume the model's first prompt contains `"tool_result": "accepted"`; the receiver has one request |
| `test_a_specialist_write_saves_a_frame_for_each_agent_on_the_path` | Outer Agent → specialist → write: two frames; resume replays both; one request |
| `test_a_loop_item_agent_write_resumes_inside_the_item` | For each body Agent with a write, failure after the settle: resume sends nothing again; after the item's row commits, its frames are gone |
| `test_a_different_request_at_the_same_invocation_stops_the_run` | Force the DEF-02 state without frames (delete `agent_frames` by SQL): the run fails with the mismatch message; the Agent does not continue |
| `test_an_old_result_row_without_a_digest_is_served_as_before` | `payload_sha256 IS NULL`: the stored result is returned |
| `test_an_approved_write_saves_frames_at_complete` | Approval path: after `complete`, a failure before the Agent checkpoint, resume: no second approval is requested and no second request is sent |

Mutants (M00 first): frames written in a second commit; `open_frames` not removed after the call; digest comparison skipped; `WriteReplayMismatch` left out of `CONTROL_FLOW`; loop-item frame clean-up removed.

## Commands to verify

```bash
# 1. New tests fail on the parent commit (show the output), then pass.
# 2. Full backend suite under five hash seeds:
for s in 0 1 2 3 4; do PYTHONHASHSEED=$s .venv/bin/python -m pytest backend/tests -q || break; done
# 3. Deployed scenario gate (about 5 minutes, local only):
.venv/bin/python -u -m scripts.check_production_scenarios --all --out <scratch>
# 4. Frontend, when a frontend or API contract file changed:
(cd frontend && npm test && npm run typecheck && npm run lint && npm run build)
```
```bash
# PostgreSQL checks. Use a throwaway container, never the user's database. Ask first: more than 5 minutes.
.venv/bin/python -u -m scripts.check_milestone_storage --out <scratch> --checks \
  postgres-migrations postgres-four-workers postgres-stale-lease postgres-sigkill-loop \
  postgres-accounting-before-commit postgres-accounting-after-commit postgres-parallel-sigkill \
  postgres-repeat-sigkill populated-downgrade
```

Named gate rows: S03, S04, S05, S16, S24, M01 (mixed approval modes inside one loop Agent).

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

The risk is I3: `settle_write` must keep exactly one `commit()`, now with the frames in it.

## Done when

- Both defect tests pass with no `xfail`; the new tests pass on SQLite and PostgreSQL; mutants are killed.
- `backend/tests/defects/` reports 7 xfailed.
- `README.md` §2.3 says how an Agent continues after a settled write; `docs/defects.md` marks DEF-01 and DEF-02 fixed with the commit hash.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item DEF-01 and DEF-02 (a settled Agent write is sent again on resume).
Read docs/tasks/01-DEF-01-02-agent-write-resume.md first, then every file it links.
This is a source fix with a design in the card. Do part A, pass the gate, then part B. Keep settle_write to one commit.

Rules for this session:
- Read the card, the linked spec and the cited code first. Do not make design decisions that the spec already made. If the spec and the code disagree, stop and ask.
- Write the tests first. Show that each fails on the parent commit for the reason the card states.
- Never weaken an existing invariant (I1-I8 in docs/gap-audit.md section 6) or an existing test to make a new test pass. If they conflict, stop and ask.
- Run mutants only in scratch copies, with an M00 no-change control and a guard that fails when no test ran.
- Ask before any run longer than 5 minutes, any network access, or any API key use. Use a throwaway PostgreSQL container, never the user's database or .data directory. Never use git stash.
- Give exact git add and git commit commands; no Co-Authored-By line; the user commits.
- After the gate passes: update the status table in docs/gap-audit.md section 11, add only verified numbers to docs/resume-evidence.md, and remove the xfail marker of a defect test only in the commit that fixes it.
- Write replies in about 80% Simplified Technical English.
```
