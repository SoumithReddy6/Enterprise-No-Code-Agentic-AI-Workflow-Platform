# W24 — Write-intent table

| | |
|---|---|
| Order | 13 of 29 (Phase 2 (alone, by rule)) |
| Closes | D3 (markers) |
| Depends on | W08 |
| Effort | M |
| Spec | `docs/specs/d3-write-intent-table.md`. |

## Goal

Write-intent markers move from the run JSON to the table `run_write_intents`, in three releases, with identical behavior.

## Files to touch

Spec §5 (nine commits). Only marker storage: `backend/app/storage.py:814-864`, `backend/app/approvals.py:182-203`, `backend/app/worker.py:125`, one migration, `backend/tests/test_write_intents.py`.

## Tests to write first

T01–T12 of the spec §6.1; mutants M00–M06 of §6.3.

## Commands to verify

Spec §7 (the gate, at each of the three releases): S03, S04, S16, S24 and the PostgreSQL and SQLite SIGKILL and accounting checks.

## Invariants

I1 fence first in each worker transaction (`storage.py:688-691`); I2 write marker committed before the send (`worker.py:194-206`); I3 result and marker removal in one commit (`storage.py:826-846`); I4 event, checkpoint, usage and accounting in one commit (`storage.py:703-738`); I5 saved token spend never goes down (`storage.py:625-638`); I6 approval bound to a digest and rebuilt before the send (`approvals.py:148-149`, `worker.py:181-183`); I7 an unsettled write blocks resume (`storage.py:862-864`); I8 a pause releases the lease in the commit of its accounting (`approvals.py:59-96`). Full list: `docs/gap-audit.md` §6.

I2, I3 and I7 are the subject of this card. A disagreement between the two stores refuses resume.

## Done when

- Spec §10. **Never combine this card with any other change, refactor or format commit.**

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W24 (write-intent table).
Read docs/tasks/13-W24-write-intent-table.md first, then every file it links.
Do release 1 only in this session (commits 1 to 7 of the spec). Stop after the gate passes. Releases 2 and 3 are separate sessions and separate pull requests.

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
