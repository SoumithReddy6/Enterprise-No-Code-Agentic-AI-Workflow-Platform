# W17 — Reconcile path and idempotency key

| | |
|---|---|
| Order | 24 of 29 (Phase 3) |
| Closes | O1 |
| Depends on | W24 (release 2), W10, W13 |
| Effort | M |
| Spec | `docs/gap-audit.md` W17. |

## Goal

An operator can resolve an uncertain write with an audited decision, and HTTP writes carry a stable Idempotency-Key.

## Files to touch

- `backend/app/tool_service.py:54-62,215-234,236-279` (`ToolConfig.idempotency_key: bool = False`; header `Idempotency-Key: sha256(run_id + ":" + invocation)`; the key is part of the prepared plan, not of the reviewed payload digest — add it to the payload preview as a separate field so the approver sees it)
- `backend/app/main.py`: `GET /api/reconcile`, `POST /api/runs/{id}/writes/{invocation}/reconcile` (`runs:reconcile`)
- `backend/app/storage.py`: `reconcile_write(run_id, principal, invocation, outcome, evidence)` — locks the job row; `delivered`: insert the result row with the evidence text and settle the intent; `not_delivered`: delete the intent; both add a `reconcile` participant and an audit row, in one transaction
- Frontend: one list screen
- Scenarios S30, S31, S32

## Tests to write first

| Test name | Asserts |
|---|---|
| `test_reconcile_delivered_settles_and_resume_never_sends` | |
| `test_reconcile_not_delivered_clears_and_resume_sends_once` | |
| `test_reconcile_needs_the_permission_and_writes_an_audit_row` | |
| `test_reconcile_of_a_settled_or_unknown_intent_is_refused` | 409 / 404 |
| `test_reconcile_of_an_executing_approval_completes_or_reopens_it` | `delivered` → approval `completed`; `not_delivered` → approval `approved` again, same digest |
| `test_idempotency_key_is_stable_for_one_action_and_differs_between_actions` | |
| `test_relay_never_retries_a_write_because_a_key_exists` | Validation still refuses a retry policy on a write (`compiler.py:174-175`) |
| S30, S31, S32 | Receiver request counts 1, 1, and the same key on both attempts |

Mutants: reconcile without settle; key derived from the attempt; reconcile in two transactions.

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

Reconcile is the only new way to settle an intent (I3, I7), and it is a recorded human decision. The key never makes Relay retry.

## Done when

- S30–S32 pass; the README's tagline can then state the reconcile path.

## Ready-to-paste prompt

```text
You are working in the Relay repository. Implement work item W17 (reconcile path and idempotency key).
Read docs/tasks/24-W17-reconcile-path-and-idempotency-key.md first, then every file it links.
Read the write-intent table spec first; this card reads that table.

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
