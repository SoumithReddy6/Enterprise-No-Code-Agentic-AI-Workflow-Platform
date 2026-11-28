# DEF-03 evidence (2026-11-27)

Card: `docs/tasks/02-DEF-03-validation-exponential-time.md`. Parent commit: `88d4d8b`. Spec:
`docs/defects.md` DEF-03. Scope change (user, 2026-11-27): `compiler.checked_dependency` has the
same per-path pattern and is fixed here too. Residual found and recorded as DEF-10 (user
decision): `static_label` on a binding cycle.

## Isolation (`isolation.txt`)

`git archive 88d4d8b` plus the changed files of this commit, committed in a throwaway
repository (`fd7f31f`). Every changed backend and test file has the same SHA-256 prefix in the
working tree and in the copy; the old defect test is absent from both. The suite and the
mutants ran on this copy. `test_def10_static_label_cycle.py` and the docs were added after the
copy; see "Gates".

## Before the fix (`baseline/`)

- `red.txt`: `backend/tests/test_validation_cost.py` on the parent code (tests written first):
  10 failed, 5 passed.
  - The three 10 s child-process tests time out (`did not finish in 10 s`).
  - The three call-count tests stop at their bound: `more than 3402 calls of
    unchecked_model_input/calls_model` (nodes × inputs, 42 × 81), `more than 4176 calls of
    checked_dependency` (48 × 87), `more than 16704 calls of static_label` (48 × 87 × 4).
  - `test_validate_route_does_not_block_the_event_loop`: `/api/health` answered 1.01 s after the
    slow validation started, after it ended.
  - The two memo-path tests and the shared-memo graph test fail with `TypeError: unexpected
    keyword argument 'memo'`: the parent has no memo. They are meaning tests for the new memo,
    not reproductions.
  - Pass on the parent, as they must: the diamond test, the record-read test, both fixture
    replays and the fresh-memo graph test (the fixture was made there).
- `walk_calls.py`, `walk-calls-88d4d8b.txt`, `walk-calls-fix.txt`: each walk, one call from the
  end of the doubled chain. Parent: 2^(n+1) − 1 calls for all three (n = 18: 524,287). Fix:
  2n + 2 (n = 18: 38; n = 80: 162). The answers are the same.

## Fixture (`fixture/`)

`backend/tests/fixtures/validation_answers_88d4d8b.json`, made on `88d4d8b` by
`make_fixture.py` (it refuses to run on another commit or with a changed `backend/app`):

- 60 workflows recorded by `record_plugin.py` while `test_request_contract_validation.py` and
  `test_switch.py` ran (87 calls, 60 unique; 24 under the `future_nodes` declarations), replayed
  in their environment: 0 dropped. Calls made while a test had replaced
  `request_contract_errors` are not recorded.
- 9 workflows from `examples/` (`reference-fixtures.json` is not a workflow).
- For each workflow: `validate_workflow` errors and `decision_warnings`. 53 have errors, 5 a
  Switch refusal, 3 warnings.
- 600 random graphs (seed 3, 1–7 nodes; 534 have a cycle): for each reference, the answers of
  `unchecked_model_input` (with and without `stop_at_sources`, with and without deps),
  `checked_dependency`, `static_label` for three fields, and `switch_input_allowed`. The test
  compares them with a fresh memo for each call and with one memo shared by all calls on a graph.

## Timing (`timing/`)

- `results.json`: 100-node workflows, `validate_workflow` + `type_warnings` + `decision_warnings`,
  best of 5: 1.5 ms (plain chain), 2.3 ms (chain from the trigger into a Switch), 1.9 ms (chain
  from the reader into a Switch). Worst of 5: 26 ms. Card target: under 1 s.
- `cyclic.txt`: DEF-10. The Switch chain with `p0.x = p{n-1}.text`: `static_label` n = 14
  0.020 s, n = 16 0.081 s, n = 18 0.331 s; the other two walks 0.0 s.

## Mutants (`mutants/`)

`mutants.py` applies one exact replacement to a fresh `git archive` of the isolation commit (the
mutation point must occur once) and commits the copy in a throwaway repository. Oracle `walk`:
`test_validation_cost.py` and 7 related files (274 tests). Oracle `full`: the whole backend
suite (with PostgreSQL on 55433), for M00 and for each mutant that `walk` does not kill. An
oracle that selects no test, or a planned test that did not run, stops the run. `ran` counts the
test ids the result regex reads (parametrized ids with spaces are read short), not the tests.
The oracle logs are cut to `*-summary.txt` (failed tests and the last line).

`run1/` is on `c9465f0`. There M06 was killed only by the DEF-10 test and M07 survived: the
planned test asked `static_label` for `(y.text, '')`, but the walk from the Calculate asks for
`(y.text, 'a')` (its operand path), so a wrong memo entry was never read. The test was fixed
(third copy commit `20110d4`) and `run2/` ran M00, M06, M07 and M08 again.

| Id | Mutant | Result |
|---|---|---|
| M00 | No change | Control passes `walk` and `full` (run2: 2,191 passed, 7 xfailed) |
| M01 | Memo key without `stop_at_sources` (card) | Killed: `test_a_record_read_after_a_model_query_…` (planned), visit count |
| M02 | One visited set shared by all calls (card) | Killed: `test_a_diamond_…` (planned), ring test, shared-memo graphs |
| M03 | False cached on every return | Killed: `test_a_node_on_the_walk_path_is_not_remembered_as_safe` (planned) |
| M04 | All visited states marked false after any call | Killed: the same (planned), shared-memo graphs, guard-declaration replay |
| M05 | No memo lookup in `unchecked_model_input` | Killed: `test_unchecked_model_input_visits_each_node_once` (planned) |
| M06 | `static_label` remembers every answer | run2: killed by `test_a_label_found_on_the_walk_path_is_not_remembered` (planned) |
| M07 | `static_label` cut test off by one | run2: killed by the same (planned) |
| M08 | Depth-limit guard always on | **Survives** `walk` and `full` |
| M09 | No `static_label` memo lookup | Killed: `test_static_label_cost_is_linear` (planned), Switch timing test |
| M10 | A `checked_dependency` cycle counts as checked | Killed: both graph fixture tests (planned) |
| M11 | No `checked_dependency` memo lookup | Killed: `test_checked_dependency_cost_is_linear` (planned), Switch timing test |
| M12 | `submission_errors` validates on the event loop | Killed: `test_validate_route_does_not_block_the_event_loop` (planned) |

M08 can change an answer only when the graph has more distinct references than 4 × nodes, so
that the depth limit can apply and a remembered answer skips it. `m08_search.py` compared the
real `static_label` with the always-remember walk on 7,749 random one-node graphs with 6–8
distinct references: no difference. It is recorded as not killed, with no counterexample found;
it is not shown to be equivalent.

## Gates

| Gate | Result |
|---|---|
| Backend suite (`suite/seed-*.txt`) | 2,191 passed, 6 xfailed at PYTHONHASHSEED 0–4, none skipped: PostgreSQL cases on a throwaway container on 55433. Isolation commit `fd7f31f` (before the DEF-10 test and the label-test fix) |
| Changed tests (`suite/changed-tests-20110d4.txt`) | `test_validation_cost.py` + `backend/tests/defects`: 15 passed, 7 xfailed at seeds 0–4 on `20110d4` |
| Full suite on `20110d4` | 2,191 passed, 7 xfailed (M00 `full` oracle of `mutants/run2`, default hash seed) |
| Deployed scenario gate (`production-1/`) | 40 of 40 on a clean clone of `20110d4` (frontend tests, typecheck, lint and build included), `working_tree: ''`, `source_files_changed: []`, 276 s |
| Frontend | No frontend file changed; the gate's frontend rows passed |

`backend/tests/defects/`: DEF-03's test left (now `test_validation_cost.py`), DEF-10's was added
(user decision). The directory still has 5 test files and 7 strict xfails (DEF-04 to DEF-10);
the card's "count goes down by one" holds for DEF-03 only.

## Not claimed

- DEF-10: a binding cycle still makes `static_label` exponential. The event loop answers; one
  request holds a thread and a CPU.
- No hard node-visit budget (not in the card).
- The meaning check covers the recorded and example workflows and 600 random graphs of up to 7
  nodes. It is not a proof; the argument for each walk is in the docstrings and in
  `docs/defects.md` DEF-03.
