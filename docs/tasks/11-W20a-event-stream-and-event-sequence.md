# W20a — Event stream and event sequence

| | |
|---|---|
| Order | 11 of 29 (Phase 1) |
| Closes | P2, part of P3 |
| Depends on | W05 |
| Effort | S–M |
| Spec | `docs/gap-audit.md` W20 parts (a) and (c); gaps P2, P3. |

## Goal

The event stream costs one query for each tick and checks the session every 5 s; the event sequence number comes from a counter on the job row.

## Files to touch

- `backend/app/main.py:194-217`
- `backend/app/storage.py:481-504,531-548` (`_events`, `_event`)
- Migration `<next>_event_counter` (`execution_jobs.next_seq Integer NOT NULL DEFAULT 0`, backfilled with `max(seq)+1`)

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_stream_tick_uses_one_query` | A statement counter around one tick with no new event: 1 statement (today: 5) |
| `test_a_revoked_session_ends_the_stream_within_six_seconds` | Existing behavior (`main.py:205-208`) with the new interval |
| `test_events_keep_gapless_order_under_parallel_branches` | A fork with 8 branches: `seq` is 0…n with no gap or repeat (SQLite and PostgreSQL) |
| `test_seq_counter_is_backfilled_for_existing_runs` | Migration on a populated database; the next event gets `max+1` |
| `test_api_writers_use_the_counter_under_the_job_lock` | `request_cancel`, `resume_run`, `approvals.decide` each add one event with the next number |

Mutants: counter read without the job-row lock; session check removed from the stream.

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

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

The counter update must be in the fenced transaction (I1, I4); every `_event` caller already holds the job-row lock (`storage.py:536-537`).

## Done when

- Stream queries/s for each client and statements for each event, before and after (load harness phase B cell or a statement counter in the test), are recorded.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W20 parts a and c (event stream and event sequence).
Read docs/tasks/11-W20a-event-stream-and-event-sequence.md first, then every file it links.
Do not use LISTEN/NOTIFY. Do not move checkpoints in this card (that is card W20b).

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
