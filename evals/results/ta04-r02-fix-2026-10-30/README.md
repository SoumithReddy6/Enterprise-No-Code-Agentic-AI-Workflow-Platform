# TA04 correction R02: approved work on resume counts as work (2026-10-30)

Finding: R02 (P2) in `docs/reviews/ta04-fix-review-2026-10-30.md`, reviewed commit
`3deb964`. Application code baseline: `3deb964`. The original TA04 evidence in
`evals/results/ta04-fix-2026-10-30/` and the auditor's files in
`evals/results/ta04-fix-review-3deb964/` are not changed.

## Cause

The policy decided "did this token-refused attempt do work?" from a new action debit or a
new model call. An approved write runs on resume against the reservation made before its
pause, so it makes neither. The attempt was reported as `did not run`, and in a loop the
item that sent the write got no entry (`processed: 0`).

## Change

- `backend/app/worker.py`: one per-attempt platform work counter. It is incremented when a
  platform operation sends a request now (approved send, write, read, Jira read, knowledge
  retrieval), or returns the stored result of a write this run already sent (a completed
  approval, a completed write marker). It never charges anything.
- `backend/app/compiler.py`: passes the counter into the run state (`platform_work`).
- `backend/app/execution_policy.py`: `_work_done(run)` = model calls + platform work. A token
  refusal counts its attempt as executed if an action was charged or `_work_done` grew.
- `docs/operations.md`: one sentence.

## The rule for a stored-result replay (for the auditor)

The audit asks to keep a cache-only replay distinct from a real execution. This fix keeps it
distinct in effect: a stored result is never sent again and never charged again. But it
**counts as work** when it replays a write this run already sent. Reason: an agent frame
stays in the database until its node is checkpointed, and a recovered node is not
checkpointed. So after a crash, the resume replays the frame and gets the stored write
result. If only a fresh send counted, the resumed run would say `did not run` while the
fresh run says `stopped after 1 attempt(s)`, and the loop would again drop the item whose
write happened. Mutant M19 (count only fresh sends) is killed by both resume tests.

What does not count: an attempt refused before any request (T1, T5; mutant M6 is killed by
T1). In the code, the counter is incremented only after the request checks pass, so a write
refused before it is sent does not count; no separate test covers that case. Nothing is charged twice: the action counters
are 1 (graph) and 2 (loop: Jira read + write) in fresh and resumed runs.

## Tests (in `backend/tests/test_token_budget_recovery.py`)

| Test | On `3deb964` | Asserts |
|---|---|---|
| `test_r02_an_agent_that_sent_an_approved_write_did_run` | fails | `agent stopped after 1 attempt(s)`, actions 1, cause `('agent','budget')`, 1 model call, 1 POST |
| `test_r02_a_loop_records_the_item_whose_approved_write_was_sent` | fails | processed 1, failed 1, `budget_halt: tokens`, actions 2, no `not_run` event |
| `test_r02_a_loop_resumed_after_the_send_records_the_same_item` | fails | crash after the write's success event; resumed loop equals the fresh one; 1 POST |
| `test_r02_resume_that_replays_the_stored_write_reaches_the_same_verdict` | passes (both runs said `did not run`) | crash after the recovered event; resumed verdict equals the fresh one. Guards against counting only fresh sends (M19) |

## Results

| Check | Result |
|---|---|
| New tests on `3deb964` (`baseline-3deb964-app-code.txt`) | 3 R02 tests fail; the other 44 pass |
| New test file on the fix | 47 of 47 pass |
| Auditor's red regressions (`test_replayed_attempt.py`, run from a scratch copy) | 2 of 2 pass; `test_real_source_boundary.py` (R01) still fails, as expected |
| Mutants (`mutants.py`, `mutants.json`) | 19 of 19 killed (M1–M16 rerun on the new code, M17–M19 new), each by all of its planned tests |
| Backend `PYTHONHASHSEED` 0–4 | 1,898 passed each (1,894 before + 4 new) |
| Isolation (`isolation.txt`) | 1,898 passed |
| Deployed scenario gate (`production/report.json`) | 24 of 24 rows passed; source unchanged during the run (`head` `3deb964` + working tree) |

## Not in this change

R01 (P1, existing since `51ba164`): the final Response rebuilds its sources from the shared
run evidence and can publish a source that a later check rejected. It needs its own
correction at the Response boundary, with T14 extended to the final Response.

`production/report.json` SHA-256: `c1f00fc6bd277d21ea8e510093cad77a1fd499f7a97f88564152722e9110752b`.

Logs (`*.log`), `runs.json`, `provider-requests.json` and `production/switch/` stay local.
