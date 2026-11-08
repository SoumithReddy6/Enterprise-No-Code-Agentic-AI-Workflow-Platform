# A5.3 Part 3 evidence: deployed scenarios for parallel branches

Design (revision 2, approved by the design review with two test changes and a mutant-table
correction, all applied): `docs/superpowers/specs/2026-11-08-parallel-branches-scenarios-design.md`.
Design probe: `evals/results/a53-part3-probe-2026-11-08/`. Baseline: `f571853` (the Part 3 design
commit; application code identical to `main` `2c3f26d`). Release-gate evidence only: no change to
the runtime, validation, storage, accounting, API or editor.

## What changed

| File | Change |
|---|---|
| `scripts/check_production_scenarios.py` | The fixture `Provider` gets `/meet/<group>/<name>` (a two-request barrier: 504 after 10 s alone; then `fail…` → 500, `hold…` → held until the row releases it) and `/lookup/<name>`, plus `release()` and an `answered` list. Existing paths and answers are unchanged. The main lab runs `parallel_scenarios(lab)` after its existing rows |
| `scripts/scenarios/parallel_branches.py` | New: S22, S23a, S23b, S24 |
| `docs/production-scenario-testing.md` | Catalogue rows S22–S24; realism note (HTTP read branches, holds instead of sleeps) |
| `README.md`, `docs/platform-reference.md` | Roadmap (Part 3 done, A6 next), S01–S24, 27 deployed scenarios, §2.2 and §2.8 evidence |
| Design | Revision 2 (review decisions) |

## The rows

| Row | What it proves (receiver and history, not only run status) |
|---|---|
| S22 | Both branch reads are open together at the receiver (a sequential run gets 504). The Join's `running` event comes after the last success of both branches. The Join runs once. One POST carries branch A's answer; the output is branch B's last node. 4 actions, each charged once |
| S23a | While the sibling's answer is still held, the run is `failed` with `Tool provider returned HTTP 500`, `a1` failed and `b1` cancelled. After the release: no Join event, no POST, `b1` requested once |
| S23b | A write in a branch: Validate returns exactly `b1: external writes belong after the Join.`; submission 422 with the same text; no run; no request |
| S24 | SIGKILL while `b1`'s first answer is held. Before restart: `a1` checkpointed, `b1` not, 0 deliveries. After the real 30 s lease: `a1` not requested again, `b1` requested twice, Join once, one POST with `a1`'s answer |

## Files

| File | Content |
|---|---|
| `isolate.py`, `isolation.txt` | Clean-export proof: git archive `f571853` + the 6 changed files, one commit (`5bc15e1`) |
| `rows.py` | Runs only the four fork rows in one `Lab` (used for the mutants) |
| `mutants.py`, `mutants-run1.txt/.json`, `m27.txt/.json` | M27–M33 on fresh copies of the isolation base |
| `production-1/2/3/report.json` | Three consecutive full gate runs in the isolation copy |
| `seeds.txt` | Full backend suite at PYTHONHASHSEED 0–4, PostgreSQL cases on a throwaway container |

## Baseline (control)

The behavior exists since Part 1, so the rows are expected to pass with unchanged product code,
and they do: the isolation copy has `f571853`'s application code, and all three gate runs pass
the four rows. The probe showed the same on `36afe6b`. The rows' ability to detect a defect is
shown by the mutants.

## Mutants

Each one changes product code (`backend/app/compiler.py`) in a fresh copy; the four rows then run
against deployed API and worker processes.

| Mutant | Planned oracle | Result | Failing assertion |
|---|---|---|---|
| M27 one node handler at a time (shared lock) | S22 | KILLED (`m27.txt`) | `Tool provider returned HTTP 504`: the branches never overlapped. S23a and S24 also failed |
| M28 Join entered by one edge per branch | S22 | KILLED | The Join has two `running`/`success` pairs |
| M29 a failed HTTP tool node recovers as if `continue` | S23a | KILLED | The run failed with `Run failed unexpectedly.`, not the receiver's HTTP 500 |
| M30 the write-in-branch rule removed | S23b | KILLED | Validate accepted the workflow |
| M31 restore ignores HTTP tool node checkpoints | S24 | KILLED | `the restored branch was read again` |
| M32 Join bindings use the intersection | S22 | KILLED | `send: invalid binding input = a1.text`; S23a, S23b and S24 also failed |
| M33 the Join waits only for `a1` (the review's mutation) | S22 | KILLED | Join `running` at event 8; `b2` success at event 10 |

## Gate results

| Gate | Result |
|---|---|
| Fork rows on unchanged product code | S22, S23a, S23b, S24 pass (control), in the working tree and in all three isolation gate runs |
| Mutants M27–M33 | all 7 planned kills (M27 after the correction below) |
| Deployed scenario gate, isolation copy `5bc15e1` | 3 consecutive runs: 28 of 28 rows each, `source_files_changed` empty, clean tree, about 100 s each (S24 about 30.3 s) |
| Full backend suite, PYTHONHASHSEED 0–4 | 2,050 passed at every seed (no new backend test), PostgreSQL cases included; isolation copy `5bc15e1` |
| Hosted CI | recorded after the commit |

## Deviations (recorded, not hidden)

1. **M27 was defective in run 1.** Each node got its own lock, so nothing was serialized and
   M27 "survived" (`mutants-run1.txt`). The corrected mutant shares one lock across all nodes,
   as the design says ("a module-level `asyncio.Lock`"), and it is killed (`m27.txt`). M28–M33
   in run 1 are unaffected: their edits did not change.
2. **M29 and M31 target HTTP tool nodes**, not "branch nodes": the node handler does not know
   branch membership. In these rows the branch nodes are exactly the HTTP tool reads (the write
   after the Join had not run when the S24 kill happened, so M31 cannot affect it).
3. **M29's kill comes from the run error**, which the row checks before the Join events. The
   review's probe showed the same mutant reaching the Join with no POST; the Join-event check
   remains the later line of defense.
4. **Hosted CI does not run the scenario gate** (the manual `production-scenarios.yml`), so the
   gate evidence is these local reports.

## Not claimed

PostgreSQL in the scenario gate (D01 covers a fork crash on PostgreSQL). Model branches and token
accounting in the gate (A01–A06, D01, D02). Any speed claim: the rows prove overlap, not a time
saving. Writes or approvals in branches (A5.3b).
