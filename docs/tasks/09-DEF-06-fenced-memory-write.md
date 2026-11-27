# DEF-06 — Fenced memory write

| | |
|---|---|
| Order | 9 of 29 (Phase 1: defect fix, Medium) |
| Closes | DEF-06 |
| Depends on | nothing |
| Effort | S–M |
| Spec | `docs/defects.md` DEF-06. |

## Goal

An Agent's memory entry is written once for each run, under the fence, in the commit of the Agent's checkpoint.

## Files to touch

- `backend/app/agent_runtime.py:486-489` (return the entry; do not call `memory_write`)
- `backend/app/compiler.py:769-812` (carry the entry from the Agent's outputs to the success event under a reserved key, then drop it from the outputs)
- `backend/app/storage.py:703-738` (`worker_event`: when the event has the key, write `agent_memories` in the same session)
- `backend/app/agent_memory.py` (a `write_in(session, …)` that takes the caller's session)
- `backend/app/worker.py:219` (remove the `memory_write` action)

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_def06_one_run_adds_one_memory_entry_when_the_checkpoint_commit_fails_once` | Exists; remove `xfail` |
| `test_memory_entry_commits_with_the_agent_checkpoint` | One commit holds the event, the checkpoint and the memory row |
| `test_a_worker_without_the_lease_writes_no_memory` | Expire the lease before the event: no memory row |
| `test_a_specialist_or_loop_body_memory_agent` | Validation already refuses memory Agents in branches and Repeats (`compiler.py:282-283,360-361`); a memory Agent as a For each body writes one entry for each item, in the item's commit |
| `test_memory_stays_bounded_and_workspace_scoped` | The existing test in `test_platform_integration.py:65-70` passes |

Mutants: memory written in its own commit; written before the fence.

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

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

The risk is I4: the memory row joins the atomic commit; nothing leaves it.

## Done when

- The defect test passes with no `xfail`; `grep -n "memory_write" backend/app` returns nothing.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item DEF-06 (fenced memory write).
Read docs/tasks/09-DEF-06-fenced-memory-write.md first, then every file it links.
Keep the global lock of agent_memory.py out of the fenced transaction: the row is keyed by tenant and key, and the fence already orders writers of one run.

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
