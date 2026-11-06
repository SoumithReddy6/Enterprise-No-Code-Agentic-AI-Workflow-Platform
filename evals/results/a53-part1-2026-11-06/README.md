# A5.3 Part 1 evidence: parallel branches with an explicit Join, and the TA08 accounting lock

Design (revision 3, approved for tests-first Part 1):
`docs/superpowers/specs/2026-10-24-parallel-branches-design.md`. Design review:
`docs/reviews/a53-design-r3-review-2026-11-06.md`, with its evidence in
`evals/results/a53-design-r3-review-89129ca/` (not changed). Decisions: policy A (the
admission limit, one call per running branch), Part 1 only, the run-scoped lock (TA08).
Baseline: `89129ca` (the rev-3 design commit; application identical to `main`).

## What changed

| Area | Change |
|---|---|
| `registry.py` | A `join` node: category Control, no ports, no settings, a handler that returns `{}`. Not in the editor palette (`paletteCategory` is an explicit map). |
| `compiler.py` validation | A non-control node with 2–8 next nodes is a fork; more than 8 is refused ("A fork has 2–8 branches."). The old "connect exactly one next node; parallel branches are not supported yet." is gone; a node with no next node now reads "connect one next node, or 2–8 parallel branches that end at one Join.". `branch_errors` checks every fork region: closed chains, one Join per fork, a Join fed by one fork with 2–8 edges and no recovery or retry, and the branch content rules over each node's attachment closure (writes, memory-role Agents, Response, control nodes, Calculate, error routes, nested forks). |
| `compiler.py` binding | `binding_guarantees` takes the union of the arrivals at a Join, the intersection elsewhere. `bindable_outputs` uses the same function. |
| `compiler.py` graph | A fork adds one edge per branch; each Join is entered by one list edge from its branches' last nodes (`add_edge([...], join)`). |
| `accounting_state.py` | `AccountingPublications`: the newest snapshot of one run attempt as `(publication_sequence, spend_generation, deepcopy(snapshot))`, published synchronously through `update()` (it is the compiler's accounting sink), and the `asyncio.Lock` that orders commits. |
| `worker.py` | All four accounting writers commit under that lock: `persist_accounting` (reserve), `persist_spend` (record), `emit` (event with accounting), and the approval pause. Each commits the newest publication, not its caller's snapshot, and acknowledges only that publication's spend generation. The commit task is shielded and owns the lock until its thread finishes; a cancelled caller waits for it, then re-raises. Events, `usage_for` and `PersistedNodeCancellation` receipts are unchanged. |
| `frontend/lib/workflow.ts` | `bindableOutputs` takes the union at a Join, as the backend does. |
| Fixtures | `bindable-outputs.json` gains the `fork_join` case; `request-nodes.json` gains the `join` definition. Both regenerated, not hand-edited. |
| Storage script | `sqlite-parallel-sigkill` and `postgres-parallel-sigkill` (D01) in `scripts/check_milestone_storage.py`; `--barrier-node` in `scripts/scenarios/milestone_worker.py`. |
| CI | `postgres-conformance` runs `postgres-parallel-sigkill` and the parallel accounting pytest cases with `RELAY_TEST_POSTGRES_URL`. |
| Docs | `docs/operations.md`: the Overshoot bound (one call per running branch, at most 8 per fork) and a **Parallel branches** section. `docs/platform-reference.md`: the `join` node and the roadmap. `README.md`: TA08 closed, A5.3 Part 1 done, counts. |

## Files

| File | Content |
|---|---|
| `baseline-89129ca.txt` | The two new test files on a clean archive of `89129ca`, PostgreSQL cases on a throwaway container: 25 failed, 1 passed (L01, the control). |
| `baseline-89129ca-d01.json` | The two D01 rows on a clean archive of `89129ca` with the new storage script: both fail at submission (the fork is refused). |
| `isolate.py`, `isolation.txt` | Clean-export proof: git archive `89129ca` + the 16 changed files; hashes match the working tree; no other tracked file differs. |
| `mutants.py`, `mutants.txt` | M1–M17 on fresh copies of the isolation base; all 17 planned kills. |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4 in the isolation copy, PostgreSQL cases included. |
| `process-rows.json` | The storage-script rows: D01 on SQLite and PostgreSQL, plus the full CI postgres-conformance list, on a throwaway PostgreSQL 17 container (127.0.0.1:55433). |
| `frontend.txt` | Frontend unit, typecheck, lint, build and browser tests. |
| `production/report.json` | Deployed scenario gate. |

## Gate results

| Gate | Result |
|---|---|
| New tests on the final tree | 26 of 26 pytest cases pass (PostgreSQL on the throwaway container); 3 consecutive local runs of both files, no flake |
| Full backend suite, PYTHONHASHSEED 0–4, isolation copy `eeb6119` | 2,047 passed at every seed (2,021 + 26) |
| Storage-script rows | 8 of 8: `sqlite-parallel-sigkill` and the CI postgres-conformance list with `postgres-parallel-sigkill`; no source file changed during the run |
| D01 | A restored from its checkpoint with no new request (branch-a calls 1), B sent again (branch-b calls 2), the Join ran once, tokens 30 = A 15 once + B's resumed call 15. Stop rule not triggered |
| Frontend | 116 unit tests, typecheck, lint, build, 49 Chromium tests |
| Deployed scenario gate | 24 of 24 rows passed (23 deployed-process scenarios + the M01 probe); `source_files_changed` empty |
| Hosted CI | To be recorded on the exact commit |

## Acceptance count

28 new collected cases, as allocated: 26 pytest cases (18 in `test_parallel_branches.py`, 8 in
`test_parallel_accounting.py`, of which 3 are PostgreSQL parameters) plus the 2 D01 process
rows. The `fork_join` fixture case adds one frontend test through the existing
`bindings.test.ts` loop (115 → 116).

## Baseline (red) classification

Every failure is a behavior refusal on `89129ca`, not a missing interface: the fork is refused
by `fork: connect exactly one next node; parallel branches are not supported yet.` (21
occurrences in the output) and the Join by `Unknown node type: join.` (10). Validation cases
fail by the missing message or an unexpected error; runtime and accounting cases fail
because the run is refused. L01 (the three LangGraph facts) passes, as a control. The TA08
defect itself showed during implementation: before the lock existed, R02 failed with
`The accounting write was refused because it would lower tokens_spent.`, the STALE refusal
the rev-2 probe predicted.

## Mutants

All 17 planned kills (`mutants.txt`). Each oracle failed by assertion, with all 32 cases
collected.

| Mutant | Planned oracle | Also failed |
|---|---|---|
| M1 Join uses the intersection | B01 | every runtime case (validation refuses `out`) |
| M2 a Join may receive two forks' branches | V07 | |
| M3 write detection ignores the attachment closure | V04 | |
| M4 memory refusal checks flow nodes only | V05a | |
| M5 refuse sibling Agents that share a `memory_key` | V05b | |
| M6 branch cap 9 | V06 | |
| M7 branches may meet at an ordinary node | V01 | |
| M8 the Join compiled as plain edges | R02 | |
| M9 no lock | A01 | A02, A03, A04, R04 |
| M10 lock, but commit the caller's snapshot | A04 | |
| M11 lock released when the waiter is cancelled | A03 | R03 |
| M12 a branch may bind an earlier node, including a sibling | B02 | the binding parity test |
| M13 a control node allowed inside a branch | V03 | |
| M14 acknowledge the generation current at commit time | A02 | |
| M15 the newest publication by spend generation | A01 | A04, D02 |
| M16 a branch node may have an incoming edge from outside its region | V01 | |
| M17 the frontend `bindableOutputs` intersects at a Join | `bindings.test.ts` `fork_join` | |

## Deviations (recorded, not hidden)

1. **D01 is two storage-script process rows**, not pytest cases, following the TA02 precedent:
   a real worker process is killed with SIGKILL, and a second one resumes after the real
   30-second lease. They run in `scripts/check_milestone_storage.py`, PostgreSQL in CI.
2. **A04 does not cancel C before it acquires.** No API cancels one waiting branch. B and C
   both publish and wait while A holds the lock; the oracle is unchanged: the commit after A
   carries the newest snapshot (3 actions), not its caller's.
3. **A01 and A04 do not assert the held commit's own counter.** The held commit saves the
   newest publication when it takes the lock, which is 1 or 2 in A01 (1, 2 or 3 in A04)
   depending on whether B debited first. That is the designed behavior. The first draft of
   these tests asserted 1 and failed on a correct schedule; the oracle is now the commit
   after the holder (A01: counter 2; A04: counter 3) and the final stored counter. Both
   still kill M9, M10 and M15.
4. **Message wording.** The design table writes "… in A5.3" in two messages; the product
   messages leave out the roadmap name ("parallel branches cannot stop, route, decide or
   fork.", "an Agent that writes memory cannot run in a parallel branch."). A direct
   fork→Join edge (a branch with no node) is refused with "a parallel branch needs at least
   one node before its Join.", which the design's "one or more nodes" implies but does not word.
5. **One existing test was extended.** `test_every_offered_binding_validates_and_others_do_not`
   now also covers `fork_join`, so the fixture's promise ("every listed binding validates and
   every other one is rejected") holds at a Join. No assertion was changed or removed. No test
   pinned the old `compiler.py:162` message.

## Not claimed

Branches are read-only: writes, approvals and memory-role Agents inside a branch are refused,
not supported (A5.3b). The token limit is an admission limit: inside a fork a run can end above
it by one call per running branch, and one call's size is not bounded (policy C, a strict cap,
is not provided). The editor does not draw forks or Joins (Part 2); it only shows the right
bindings after a Join. A recovered (`continue`) branch node has no checkpoint, so it runs
again on resume, as before.
