# DEF-01 and DEF-02: gates for the fix `f3771b3` (2026-11-27)

Defects: `docs/defects.md` (DEF-01 Critical, DEF-02 High). Card:
`docs/tasks/01-DEF-01-02-agent-write-resume.md`. The fix was written on 2026-11-26 in another
session and committed as `f3771b3` before these gates ran. Baseline for the red run: `27e1e19`.

## Change under test (`f3771b3`)

| File | Change |
|---|---|
| `backend/app/agent_runtime.py` | `OPEN_FRAMES` (a `ContextVar`): the frame of each Agent on the call path while its tool call runs |
| `backend/app/worker.py` | Reads the open frames and passes them to `settle_write` and `approvals.complete`. Stores the request digest with the result; a stored result with a different digest raises `WriteReplayMismatch` |
| `backend/app/storage.py` | `settle_write` writes the frames and the digest in its one commit; `completed_write`; a `loop_item` event drops the frames of its item owner |
| `backend/app/approvals.py` | `complete` saves the frames; `pause` merges its frames with the saved ones |
| `backend/app/execution_policy.py`, `tool_service.py` | `WriteReplayMismatch`, in `CONTROL_FLOW` |
| `backend/migrations/versions/0007_action_result_digest.py` | `run_action_results.payload_sha256` (nullable) |

## Follow-up in the next commit

| File | Change |
|---|---|
| `scripts/scenarios/request_review_worker.py` | The test-only `settle` wrapper takes `**kwargs`. Hosted CI on `f3771b3` (run 46021334967) failed `postgres-conformance`: `request_enforcement` pause, confirmation and settlement failed on SQLite and PostgreSQL, because `settle_write` now gets `payload_sha256` and the wrapper took only positional arguments. Reproduced locally (`request-enforcement/f3771b3/`) and passes after the fix (`request-enforcement/wrapper-fix/`) |
| `docs/defects.md`, `docs/gap-audit.md`, `README.md` | The commit hash, these gate results and the test count (2,176) |

## Red on the baseline (`baseline/red.txt`)

`git archive 27e1e19` + `test_agent_write_resume.py`, `test_execution_safety.py` and
`test_s07_boundaries.py` from `f3771b3`: 8 failed, 19 passed.

| Test | Baseline failure |
|---|---|
| `test_def01_…` | The receiver got the same write 2 times |
| `test_settle_write_saves_the_open_frames_in_its_commit` | No frame saved |
| `test_resume_does_not_ask_the_model_for_the_settled_step` | The model is asked again |
| `test_a_specialist_write_saves_a_frame_for_each_agent_on_the_path` | No frames saved |
| `test_a_loop_item_agent_write_resumes_inside_the_item` | No frame for `each:1:0:agent` |
| `test_def02_…` | Run success, but the receiver got only X |
| `test_a_different_request_at_the_same_invocation_stops_the_run` | The run ends `success` |
| `test_an_old_result_row_without_a_digest_is_served_as_before` | No column `payload_sha256` (a schema failure, not a behavior failure) |

Pass on the baseline: `test_a_kill_between_send_and_settle_saves_no_frame_and_blocks_resume`
(the control) and `test_an_approved_write_saves_frames_at_complete`. Its docstring says that it
is a guard for the approval path, because the pause already saved the frame; it is not red
evidence. The changed tests in `test_execution_safety.py` and `test_s07_boundaries.py` pass on
the baseline too.

## Mutants (`mutants/`)

`mutants.py` applies one exact replacement to a fresh `git archive` of `f3771b3` (the mutation
point must occur once) and commits the copy in a throwaway repository. Oracle `rw`: the four
write and approval test files (48 tests). Oracle `full`: the whole backend suite on SQLite, for
M00 and for each mutant that `rw` does not kill. An oracle that selects no test, or a planned
test that did not run, stops the run.

| Id | Mutant | Killed by |
|---|---|---|
| M00 | No change | Control: 48 of 48 `rw`, 2,173 passed + 3 PostgreSQL skips + 7 xfailed `full` |
| M01 | Frames written in a second commit | `test_settle_write_saves_the_open_frames_in_its_commit` (planned) |
| M02 | `OPEN_FRAMES` not reset after the call | `test_a_loop_item_agent_write_resumes_inside_the_item` (the planned specialist test passes) |
| M03 | The worker passes no frames | 5 tests, both planned |
| M04 | Loop-item frame clean-up removed | `test_a_loop_item_…` (planned) |
| M05 | Digest not compared | `test_a_different_request_at_the_same_invocation_stops_the_run` (the planned `test_def02_…` passes: with the frames of Part A, the resume replays the X step from its frame, and the model's Y comes at the next step as a new write, so the digest check is not reached. `test_a_different_request_…` removes the frames first, the state an older build leaves) |
| M06 | `WriteReplayMismatch` not in `CONTROL_FLOW` | `test_a_different_request_…` (planned) |
| M07 | `approvals.complete` saves no frames | **Survives** `rw` and `full` |
| M08 | `approvals.pause` replaces the saved frames | **Survives** `rw` and `full` |
| M09 | The digest is not stored | `test_a_different_request_…` (the planned `test_def02_…` passes, for the reason given at M05) |
| M10 | A NULL digest counts as a mismatch | `test_an_old_result_row_…` (planned) |
| M11 | A finished item clears every frame | **Survives** `rw` and `full` |

M07, M08 and M11 change only frames that are not on the current call path. As far as found, no
reachable workflow has one: writes in a parallel branch are refused (`compiler.py`, "external
writes belong after the Join"), For each runs its items in order (`iteration.py`), and a node's
checkpoint clears its frames. The approval pause already saves the frame that `complete` saves.
They are recorded as probably equivalent, not as killed.

A first mutant pass (not kept) was invalid: its full-suite oracle failed 52 tests in every
copy, because the copy had no git repository (`git rev-parse HEAD`) and `-p no:logging` removed
`caplog`. The harness now commits each copy and keeps logging, and M00 runs the full oracle as
its control.

## Gates

| Gate | Result |
|---|---|
| Backend suite (`suite/seed-*.txt`) | 2,176 passed, 7 xfailed at PYTHONHASHSEED 0–4, none skipped: the PostgreSQL cases ran on a throwaway container on 55433, in the isolation copy (`f3771b3`) |
| `request_enforcement` (`request-enforcement/`) | `f3771b3`: 3 of 9 (pause, confirmation, settlement fail on both databases). With the wrapper fix: 9 of 9, `source_files_changed: []`. PostgreSQL: the throwaway on 55433 (`MILESTONE_POSTGRES_URL`) |
| Deployed scenario gate (`production-1..3/`) | 35 of 35 each, on `f3771b3` + the wrapper fix (copy commit `a3e33e5`), `source_files_changed: []`, about 135 s per run |
| Hosted CI on `f3771b3` | 4 of 5: `postgres-conformance` failed (see above) |
| Isolation (`isolation.txt`) | Every changed backend, migration and test file has the working tree's hash in both copies |

## Not run

- Frontend checks: the fix has no frontend change; hosted CI `frontend` and `browser-layout`
  passed on `f3771b3`.
- Hosted CI on the follow-up commit: after the push.

## Not claimed

- A run settled by a build before revision 0007 has no saved frame and no digest. Its resume can
  still send a moved write again; `docs/operations.md` says to start a new run instead.
- A model that asks for the same write again after it saw the result asks for a new write.
  Relay cannot tell it from an intended second write.
