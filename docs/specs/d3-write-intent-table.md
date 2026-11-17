# Spec: write-intent table (audit W24, gap D3 markers)

Status: ready to implement. Written 2026-11-16 against source `24e659c`.

**Rule: this work is never combined with any other change.** Each commit below touches only the
marker storage. No refactor, no formatting, no other schema change, no defect fix (DEF-01 and
DEF-02 are separate cards) goes in the same pull request.

## 1. Current behavior

An external write is protected by a marker that is committed before the send.

| Step | Code | What it does |
|---|---|---|
| Direct write, marker id | `backend/app/worker.py:196` | `marker = invocation` for a write |
| Already sent? | `worker.py:198-199`; `backend/app/storage.py:820-824` | `completed_action` reads `run_action_results`; a hit returns the stored result |
| Check, then mark | `worker.py:202-203`; `storage.py:814-818` | `prepare_write()` (sends nothing), then `mark_write`: fence, add the id to `runs.data['write_nodes']`, commit |
| Send | `worker.py:205-210` | Any exception becomes `UncertainWriteError`; the marker stays |
| Settle | `worker.py:211`; `storage.py:826-846` | Fence; insert `run_action_results`; remove the id from `write_nodes`; **one commit** |
| Approved write, marker | `backend/app/approvals.py:182-194` | `begin`: fence; add `row.checkpoint_owner` to `write_nodes`; `owns_marker = 1` when it was not there; status `executing`; one commit |
| Approved write, settle | `approvals.py:196-203` | `complete`: fence; status `completed` + result; remove the marker when `owns_marker`; event; one commit |
| Is a marker settled? | `storage.py:848-859` | `_write_settled(data, id)`: true when `id` is a checkpointed node, or `id` is `<loop>:<index>` and that item has a saved result |
| Refuse resume | `storage.py:861-864`, called at `worker.py:125` (each claim) and `storage.py:874` (resume API) | Any marker that is not settled raises |

Problems: the markers live in the run's JSON document, which is rewritten whole
(`storage.py:818,844`); they have no constraint; they cannot be listed across runs, so a
reconcile screen (audit W17) has nothing to read.

## 2. Target design

### 2.1 Table

Alembic revision `0009a_write_intents` (use the next free number if `0009` is not yet merged; the
name must contain `write_intents`).

```
run_write_intents
  run_id      String(64)   NOT NULL
  invocation  String(512)  NOT NULL     -- the same id that is put in write_nodes today
  tenant_id   String(64)   NOT NULL
  kind        String(16)   NOT NULL  CHECK (kind IN ('direct','approval'))
  state       String(16)   NOT NULL  CHECK (state IN ('marked','settled'))
  marked_at   String(64)   NOT NULL     -- storage.now()
  settled_at  String(64)   NULL
  PRIMARY KEY (run_id, invocation)
  INDEX ix_run_write_intents_state (state)
  CHECK ((state = 'settled') = (settled_at IS NOT NULL))
```

No foreign key in this revision (audit W08 adds the composite key for all child tables).

The migration also **copies** existing markers: for each run whose `data['write_nodes']` is not
empty, insert one row for each id with `state = 'marked'`, `kind = 'direct'`,
`marked_at = runs.created_at`. This is an exact copy, not a guess: an id in `write_nodes` is a
marked intent by definition. `_write_settled` (see 2.3) still decides whether it blocks resume.

### 2.2 Mode setting

One setting, read once at `Store.__init__`: `RELAY_WRITE_INTENT_MODE`.

| Mode | Writes | Reads for "unsettled?" | Release |
|---|---|---|---|
| `dual` (default in release 1) | JSON and table | JSON **or** table (either says unsettled → refuse) | 1 |
| `table` (default in release 2) | JSON and table | table only | 2 |
| `table_only` (default in release 3) | table only | table only | 3 |

Any other value: `Store.__init__` raises `ValueError('RELAY_WRITE_INTENT_MODE must be dual, table or table_only')`.

### 2.3 Functions (all in `backend/app/storage.py` unless stated)

- `_intent_mark(s, run, invocation, kind)`: private; called inside the caller's fenced session.
  If no row: insert `marked`. If a row is `marked`: no change. If a row is `settled`: raise
  `ValueError('This write already settled.')` (unreachable today, because `completed_action` is
  checked first; it must fail closed if it is reached).
- `_intent_settle(s, run_id, invocation)`: set `state='settled'`, `settled_at=now()` where the
  row is `marked`. No row: no error (a run that started before release 1 and was not copied).
- `mark_write`: after the fence, call `_intent_mark(..., 'direct')`; in modes `dual` and `table`
  also update `write_nodes` as today; **one commit**.
- `settle_write`: after the fence, insert the result row as today, call `_intent_settle`, and in
  modes `dual` and `table` remove the id from `write_nodes` as today; **one commit**.
- `approvals.begin` (`approvals.py:193-194`): compute `owns_marker` from the **table** in modes
  `table` and `table_only` (no `marked` row for `checkpoint_owner`), from JSON in mode `dual`;
  call `_intent_mark(..., 'approval')`; same single commit as today.
- `approvals.complete` (`approvals.py:202`): when `owns_marker`, call `_intent_settle`; same
  single commit as today.
- `unsettled_writes(s, row, document)`: returns the sorted list of invocation ids that block
  resume.
  - from JSON: `[id for id in document.get('write_nodes', []) if not _write_settled(document, id)]`
  - from the table: every row with `state='marked'` whose id is not `_write_settled(document, id)`
  - mode `dual`: the union; mode `table` and `table_only`: the table list.
- `check_resume_writes` keeps its name and message. It becomes an instance method that takes the
  run document and reads the table in a session. Its two callers (`worker.py:125`,
  `storage.py:874`) pass what they pass today. `claim_next` (`storage.py:685`) adds
  `'unsettled_writes': [...]` to the document it returns, computed in the claim's session, and
  `worker.py:125` raises when that list is not empty (so the worker makes no extra query).

`_write_settled` does not change. It stays the only rule for "an approval-path marker with a
checkpoint is settled".

### 2.4 Release 3 guard

In mode `table_only`, `Store.__init__` runs one query after the schema step: any run with a
non-empty `data['write_nodes']` that has an id with no table row. If one exists, raise
`RuntimeError('Write-intent markers exist only in the run document for run <id>. Start once in mode "table" (it copies them) before "table_only".')`.

## 3. Rejected alternatives

| Alternative | Why not |
|---|---|
| One-step cutover (table only, at once) | A rolling restart runs old and new code together: the old code reads JSON, the new code writes the table, and a marker is invisible to one of them |
| Store the settled state only in the table and drop `_write_settled` | It changes when an approval-path marker stops blocking resume. Behavior must be identical |
| Add the payload digest column now | That is the DEF-02 fix. Never combined |
| A foreign key to `runs` now | W08 owns the composite tenant key for all child tables |
| A database trigger that mirrors JSON to the table | Hides the write path from the code that must hold I2 and I3 |

## 4. Invariants that must not break

| Id | Invariant | How this spec keeps it |
|---|---|---|
| I1 | Each worker write runs the fence first in its transaction | `_intent_mark` and `_intent_settle` take the caller's session; they never open one |
| I2 | The marker is committed before the send; the request is checked before the marker | The row insert is in `mark_write`'s commit, which stays before `tools.execute` (`worker.py:203-206`); `prepare_write()` stays before it |
| I3 | The result and the marker removal are one commit | Result insert, row update and JSON removal share `settle_write`'s single `commit()` |
| I7 | An unsettled write blocks resume | Mode `dual` refuses when **either** store says unsettled; a disagreement never picks one |
| I6, I8 | Approval digest; pause releases the lease | Not touched. `approvals.pause`, `decide`, `resolve` do not change |

## 5. Change list, in commit order

Each commit must pass the gate in §7 before the next starts.

| # | Commit | Files | Content |
|---|---|---|---|
| 1 | Tests first (red) | `backend/tests/test_write_intents.py` (new) | Tests T01–T12 of §6. Show that they fail on the parent commit (T01–T10 by a missing table or attribute; record the output) |
| 2 | Table and copy | `backend/migrations/versions/0009a_write_intents.py`, `backend/app/storage.py` (model class `WriteIntentRecord` only), `backend/migrations/baseline_schema.py` is **not** changed | Revision with upgrade (create, copy) and downgrade (drop; see §9). Extend `populated-downgrade` in `scripts/check_milestone_storage.py` with one run that has a marker |
| 3 | Mode setting and helpers | `backend/app/storage.py` | `RELAY_WRITE_INTENT_MODE` parsing, `_intent_mark`, `_intent_settle`, `unsettled_writes`. Not yet called |
| 4 | Dual write, direct path | `backend/app/storage.py` | `mark_write`, `settle_write` call the helpers |
| 5 | Dual write, approval path | `backend/app/approvals.py` | `begin`, `complete` call the helpers |
| 6 | Dual read | `backend/app/storage.py`, `backend/app/worker.py:125` | `check_resume_writes` and the claim use `unsettled_writes` in mode `dual` |
| 7 | **Release 1.** Mutants, evidence, docs | `evals/results/w24-release1-<date>/`, `docs/operations.md` | Mutants M00–M06 of §6.3 |
| 8 | **Release 2.** Default mode `table` | `backend/app/storage.py` (one line), tests | T11 changes its expected default. Gate again |
| 9 | **Release 3.** Default mode `table_only`, guard | `backend/app/storage.py`, tests | §2.4 guard; T12. Gate again. `README.md` §2.3 names the table |

Do not start commit 8 until release 1 ran in the deployed gate and the PostgreSQL checks with no
failure. Do not start commit 9 in the same pull request as commit 8.

## 6. Acceptance tests

### 6.1 `backend/tests/test_write_intents.py`

Use the `client` fixture pattern of `backend/tests/test_platform_integration.py:10-13` and the
`write_flow` helper at `:42-46`.

| Id | Test name | Asserts |
|---|---|---|
| T01 | `test_mark_write_inserts_a_marked_row_in_the_marker_commit` | After `store.mark_write(run, owner, 'tool')`: one row `(run, 'tool', 'direct', 'marked')`, and `write_nodes == ['tool']`. A spy on `Session.commit` shows exactly one commit in the call |
| T02 | `test_settle_write_sets_settled_and_stores_the_result_in_one_commit` | After `settle_write`: row `settled` with `settled_at`, result row present, `write_nodes == []`; exactly one commit |
| T03 | `test_a_failure_between_result_and_row_update_leaves_neither` | Patch `_intent_settle` to raise; `settle_write` raises; no result row, row still `marked`, marker still in `write_nodes` |
| T04 | `test_resume_is_refused_when_only_the_row_says_unsettled` | Mark; delete the id from `write_nodes` by SQL; `resume_run` raises the existing message (mode `dual`) |
| T05 | `test_resume_is_refused_when_only_json_says_unsettled` | Mark; delete the row by SQL; `resume_run` raises (mode `dual`) |
| T06 | `test_a_claim_refuses_a_run_with_an_unsettled_row` | Mark; expire the lease; claim and `worker.execute`; run `failed` with "external write" in the error; the patched send was not called |
| T07 | `test_approval_begin_marks_and_complete_settles_with_the_approval_commit` | `begin`: row `(owner, 'approval', 'marked')`, approval `executing`, one commit. `complete`: row `settled`, approval `completed`, one commit |
| T08 | `test_an_executing_approval_blocks_resume_from_the_row` | After `begin` only: remove the JSON marker by SQL; `resume_run` still raises |
| T09 | `test_a_checkpointed_owner_marker_does_not_block_resume` | Row `marked` for id `n`, and `n` in `checkpoints`: `unsettled_writes` is empty (the `_write_settled` rule) |
| T10 | `test_migration_copies_existing_markers_as_marked` | Database at the parent revision with a run whose `write_nodes == ['a:1:t']`; upgrade; one `marked` row; downgrade; table gone and `write_nodes` unchanged |
| T11 | `test_mode_setting_values` | Unset → the release default; `dual`, `table`, `table_only` accepted; `x` raises `ValueError` |
| T12 | `test_table_only_refuses_a_json_only_marker_at_startup` | A run with a JSON marker and no row; `Store(...)` in mode `table_only` raises `RuntimeError` naming the run |

Parametrize T01–T09 over SQLite and, when `RELAY_TEST_POSTGRES_URL` is set, PostgreSQL (the
pattern of `backend/tests/test_parallel_accounting.py`).

### 6.2 Existing tests that must pass unchanged

`backend/tests/test_platform_integration.py` (all), `test_execution_safety.py`,
`test_approvals.py`, `test_durable.py`, `test_loop_storage.py`, `test_request_review.py`.
`backend/tests/defects/` must still report 9 xfailed (this work fixes none of them).

### 6.3 Mutants (scratch copies only; M00 first)

| Id | Mutant | Must be killed by |
|---|---|---|
| M00 | No change | all pass (control); the runner fails on "no tests ran" |
| M01 | `mark_write` inserts the row after a second commit | T01 |
| M02 | `settle_write` commits the result before the row update | T02, T03 |
| M03 | `unsettled_writes` reads JSON only in mode `dual` | T04 |
| M04 | `unsettled_writes` reads the table only in mode `dual` | T05 |
| M05 | `approvals.begin` skips `_intent_mark` | T07, T08 |
| M06 | `unsettled_writes` ignores `_write_settled` for rows | T09 |

## 7. Gate (at release 1, again at 2, again at 3)

All must pass. Record the outputs in `evals/results/w24-release<N>-<date>/`.

```bash
for s in 0 1 2 3 4; do PYTHONHASHSEED=$s .venv/bin/python -m pytest backend/tests -q || break; done
.venv/bin/python -u -m scripts.check_production_scenarios --all --out <scratch>     # S03, S04, S16, S24 are the named gate rows
.venv/bin/python -u -m scripts.check_milestone_storage --out <scratch> --checks \
  postgres-migrations postgres-four-workers postgres-stale-lease postgres-sigkill-loop \
  postgres-accounting-before-commit postgres-accounting-after-commit postgres-parallel-sigkill \
  postgres-repeat-sigkill populated-downgrade \
  sqlite-parallel-sigkill sqlite-repeat-sigkill sqlite-accounting-before-commit sqlite-accounting-after-commit
```

The PostgreSQL checks need a throwaway PostgreSQL container (`compose.yaml`). They take more than
5 minutes together: ask the user before the run. If the user's Relay is running, do not touch its
`.data/` directory.

## 8. Failure-mode analysis

| Failure | Mode `dual` result | Why it is safe |
|---|---|---|
| Kill after the marker commit, before the send | Row `marked`, JSON marker present; next claim refuses | Same as today |
| Kill after the send, before `settle_write` | Same | Same as today (S16) |
| Kill inside `settle_write` before commit | Nothing of the settle is saved; refused | One commit (T03) |
| Old code (before commit 4) and new code run together during a restart | Old code writes JSON only; new code reads JSON or table | The union read covers markers written by old code |
| New code marks, old code settles (removes JSON only) | Row stays `marked`; `completed_action` has the result; new code refuses resume | Fails closed. The operator sees a refused resume for a write that did settle. Recovery: finish the restart; this window exists only while both versions run. Document it in `docs/operations.md` |
| A row exists and JSON does not (or the reverse) by damage | Refused | Either store blocks |
| The table is missing (migration not run) | Startup already refuses a schema that is not at head (`backend/app/auth.py:175-177`) | No silent path |
| Mode `table_only` with JSON-only markers | Startup refuses (§2.4) | T12 |

## 9. Rollback

| From | How | Loss |
|---|---|---|
| Release 1 (`dual`) | Deploy the previous build; `alembic downgrade` drops the table | None: JSON is complete |
| Release 2 (`table`) | Set `RELAY_WRITE_INTENT_MODE=dual`; or as above | None: JSON is still written |
| Release 3 (`table_only`) | Set mode `table` first. Its startup step writes every `marked` row that is not `_write_settled` back into `write_nodes` (add this step in commit 9, test T12b `test_mode_table_restores_json_markers_from_rows`). Then the earlier rollbacks apply | None, when done in this order. A direct downgrade from `table_only` is refused by the revision's `downgrade()` when a `marked` row has no JSON marker |

## 10. Done when

- T01–T12 pass on SQLite and PostgreSQL; mutants M01–M06 are killed and M00 passes.
- The gate of §7 passed at each of the three releases, with evidence saved.
- `grep -n "write_nodes" backend/app` after release 3 shows reads only in the mode `dual`/`table`
  branches and the rollback step.
- `docs/gap-audit.md` §11 marks W24 done; `docs/resume-evidence.md` gets no new claim (this item
  has no number).
