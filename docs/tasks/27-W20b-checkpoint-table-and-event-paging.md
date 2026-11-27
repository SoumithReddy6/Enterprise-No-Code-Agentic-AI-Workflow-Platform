# W20b — Checkpoint table and event paging

| | |
|---|---|
| Order | 27 of 29 (Phase 3) |
| Closes | rest of P3, D3 (checkpoints) |
| Depends on | W24 (release 3), W05 |
| Effort | M–L |
| Spec | `docs/gap-audit.md` W20 parts (b) and (d). Write a spec first, on the model of `docs/specs/d3-write-intent-table.md` (table, mode setting `dual` → `table` → `table_only`, exact copy, three releases). |

## Goal

Checkpoints move from the run JSON to `run_checkpoints`, written in the commit of their event; `GET /api/runs/{id}` pages events.

## Files to touch

- `docs/specs/checkpoint-table.md` (new, first deliverable)
- `backend/app/storage.py:550-595,640-667,703-738` (`_compact_loop`, `_hydrate_loop`, `_document`, `worker_event`)
- `backend/app/storage.py:487-493`, `backend/app/main.py:162-163` (paging)
- One migration with an exact copy

## Tests to write first

The spec lists them. They must include:

| Test name | Asserts |
|---|---|
| `test_checkpoint_row_event_usage_and_accounting_are_one_commit` | I4 |
| `test_resume_across_the_upgrade_restores_from_copied_rows` | A run checkpointed before the migration resumes after it |
| `test_dual_mode_refuses_when_the_two_stores_disagree` | |
| `test_bytes_written_for_a_twenty_node_run_grow_linearly` | The probe method of `evals/results/loop-checkpoint-2026-09-30/storage_probe.py` |
| `test_run_api_pages_events` | |

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

Named rows: every `*-accounting-*-commit`, `*-sigkill-loop`, `*-parallel-sigkill`, `*-repeat-sigkill`.

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

I4 is the subject of this card. Like W24: alone, never combined.

## Done when

- SQL bytes written for the `w20` workflow before and after are measured and recorded.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W20 parts b and d (checkpoint table and event paging).
Read docs/tasks/27-W20b-checkpoint-table-and-event-paging.md first, then every file it links.
First session: write the spec only and stop for review. Second session: implement release 1.

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
