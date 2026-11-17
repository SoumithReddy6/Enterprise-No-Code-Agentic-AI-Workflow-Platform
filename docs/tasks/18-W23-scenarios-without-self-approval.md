# W23 — Scenarios without self-approval

| | |
|---|---|
| Order | 18 of 29 (Phase 2) |
| Closes | S2 (in the deployed gate) |
| Depends on | W12 |
| Effort | S–M |
| Spec | `docs/gap-audit.md` W23; `docs/specs/multi-tenancy-core.md` §2.6 and §5 PR 7. |

## Goal

`self_approval = true` is temporary. S01, S02, S05, S14 and S29 run with separate initiator and approver accounts, and no scenario sets the flag.

## Files to touch

- `scripts/check_production_scenarios.py:99-123,137,163-205,318-330` (`Lab.boot` creates an Operator account and an Approver account through an invite; `Lab.decide` uses the approver's client)
- `scripts/scenarios/repeat_loops.py` (S29), `scripts/scenarios/mixed_approval.py` (M01)
- `backend/tests/test_separation_of_duties.py`, `backend/tests/test_scenario_gate.py`

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_new_org_self_approval_default_false` | A new organization has `self_approval = false`, and its initiator's approve returns 403 |
| `test_scenarios_do_not_set_self_approval` | No file under `scripts/` contains `self_approval` |
| S01, S02, S05, S14, S29 | Every assert they have today, plus: the initiator's own decision returns 403 and the receiver gets nothing |

Mutant: remove the participant check in `decide()` → all five scenarios fail on the new assert. M00 control first.

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

The scenarios must keep every existing assert (digest mismatch 409, duplicate approval no-op, changed destination blocked, restart repeats nothing, cached Repeat).

## Done when

- The five scenarios pass with two accounts; the grep test passes; the migration default for upgraded organizations stays `true` and is documented as a setting the owner should turn off.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W23 (scenarios without self-approval).
Read docs/tasks/18-W23-scenarios-without-self-approval.md first, then every file it links.
Change scenario code and tests only. No source change.

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
