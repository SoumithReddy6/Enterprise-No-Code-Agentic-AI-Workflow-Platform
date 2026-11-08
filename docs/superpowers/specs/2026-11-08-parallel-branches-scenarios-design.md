# A5.3 Part 3: deployed scenarios for parallel branches

**Status:** Revision 1 (2026-11-08), for design review.
**Baseline:** `36afe6b` (application code identical to `main` `2c3f26d`, PR #8; main CI run
`42438718978`, 5/5 green). A5.3 Part 1 and Part 2 are closed.
**Parent design:** `docs/superpowers/specs/2026-10-24-parallel-branches-design.md` (rev 3),
"Scope": Part 3 is "deployed scenarios S22–S24, README".
**Tier:** P2. Release-gate evidence only: no change to the runtime, validation, storage,
accounting, API or editor. Design review, then the new rows and their mutants, then an
implementation review.

## Outcome

The deployed scenario gate (`scripts.check_production_scenarios`) runs forks through separate
API and worker processes, real authentication, SQL storage, leases and HTTP tool transport, and
checks what the receiver got, not only the run status:

1. A business fork runs its branches at the same time, the Join runs once, and the write after
   the Join is sent once with a branch's value.
2. A failing branch fails the run, cancels its sibling without waiting for it, and nothing after
   the Join is sent.
3. A write inside a branch is refused before any run exists.
4. A worker killed in the middle of a fork resumes: a finished branch is not read again, the
   unfinished one is, and the write after the Join is sent once.

The gate policy (`docs/production-scenario-testing.md`, "Policy for each new phase") requires a
business workflow and its negative and recovery counterparts. Today the gate has 24 rows and
none of them runs a fork.

## Evidence

### What is already proven, and where

| Guarantee | Existing evidence | Level |
|---|---|---|
| Branches run at the same time; the Join waits and runs once | R01, R02 (`test_parallel_branches.py`) | Real Worker and Store in one process, monkeypatched model |
| Failure cancels the sibling | R03 | Same |
| Writes refused in branches | V04 | `validate_workflow` only |
| Ordered accounting commits | A01–A06, D02 (`test_parallel_accounting.py`) | In process, SQLite and PostgreSQL |
| Crash in a fork | D01 (`check_milestone_storage.py`, `*-parallel-sigkill`) | Deployed API and worker, SQLite and PostgreSQL, model branches, **no write** |
| Editor builds a valid fork | B01–B05, K01 | Browser with a mocked API, plus a backend contract |

**Gap.** No row in the release gate runs a fork. No deployed check shows that:
- nothing after the Join is sent when a branch fails;
- cancellation does not wait for a slow sibling;
- the deployed API refuses a write in a branch;
- the write after the Join is sent exactly once after a crash in the fork.

### Probe (2026-11-08)

`evals/results/a53-part3-probe-2026-11-08/` (the gate's `Lab` with a replacement HTTP fixture,
baseline `36afe6b`). Every planned check holds today:

| Probe | Observed |
|---|---|
| S22 | Both branch reads met at the fixture's barrier. Join once. One POST with branch A's body. Output from branch B's last node. Action counter 4, each node charged once |
| S23a | `HTTP 500` failed the run; the sibling showed `cancelled`. No Join event, no delivery. The run was failed before the slow sibling answered |
| S23b | Validate and submission refused with `b1: external writes belong after the Join.`; no run, no request |
| S24 | Checkpoints at the kill: `input`, `a1`. Resumed after the real lease (35.2 s). `a1` read once, `b1` twice, Join once, one delivery |

**Consequence.** Part 3 adds evidence, not behavior. The new rows are expected to **pass on the
baseline**. A test that is green from the start proves nothing alone, so each row's ability to
detect a defect is shown by a planned mutant (below) that the row must fail.

## Design

### Fixture

The gate's `Provider` (in `check_production_scenarios.py`) gets three new path prefixes.
Existing prefixes and their answers do not change, so the 24 existing rows see the same fixture.

| Path | Answer |
|---|---|
| `/meet/<group>/<name>` | Waits until 2 requests of `<group>` have arrived, at most 10 s. If the other request does not arrive: **504**, so a sequential run fails with a clear reason. After the meeting, a name that starts with `fail` gets 500, one that starts with `slow` waits 5 s, any other gets 200. The body is `{"lookup": "<name>"}` |
| `/lookup/<name>` | 200, `{"lookup": "<name>"}`, at once |
| `/hold/<name>` | Unchanged: waits 5 s, then 200. A **GET** now gets `{"lookup": "<name>"}` instead of `accepted`; a POST still gets `accepted`. S16 is the only existing user, with POST, and asserts nothing on the body |

The fixture also records when each answer was sent, so a row can show that the run finished
before a slow answer.

The barrier is the concurrency proof. It does not use a timing threshold: two requests are either
open together or the row fails with 504.

### Rows

They run in the main lab (no model is needed), in a new module
`scripts/scenarios/parallel_branches.py` with `parallel_scenarios(lab)`, after the existing main-lab
rows. Contract 1, HTTP read tools in the branches, one HTTP write after the Join.

**S22 — Order desk: look up customer and order at the same time, then send one delivery.**

```
input ─┬─► a1 customer lookup  /meet/s22/a ─────────────────────────┐
       └─► b1 order lookup     /meet/s22/b ─► b2 stock lookup ──────┴─► Join ─► send POST /deliver/s22 (a1.text) ─► out (b2.text)
```

Branch B is one node longer on purpose: a Join that ran once per arriving branch would run twice
here, and the write would be sent twice (M28).

Asserts:
- `/api/validate` accepts it;
- the run succeeds, and the fixture saw both meet requests;
- the Join has exactly one success event;
- the receiver got exactly one POST, and its body equals branch A's answer;
- the run output equals `b2`'s answer, so both branches' outputs were bound after the Join;
- the action counter is 4, and each of `a1#0`, `b1#0`, `b2#0`, `send#0` was charged once: the branches share one budget.

**S23a — A failed branch cancels its sibling, and nothing after the Join is sent.** The same shape
without `b2`. `a1` is `/meet/s23/fail-a` and `b1` is `/meet/s23/slow-b`.

Asserts:
- the run fails with `Tool provider returned HTTP 500`;
- `a1`'s outcome is `failed` and `b1`'s is `cancelled`;
- the Join has no event, and `/deliver/s23` got 0 requests;
- the run is `failed` before the fixture sent the slow answer, so cancellation did not wait for the sibling;
- `b1` was requested once and never retried.

**S23b — A write inside a branch is refused before any run.** Branch `b1` is an HTTP write.

Asserts:
- `/api/validate` returns exactly `['b1: external writes belong after the Join.']`;
- `POST /api/runs` returns 422 with the same text;
- the run count does not change, and the fixture got no request.

**S24 — A worker killed in a fork resumes without repeating finished work.** `a1` is
`/lookup/s24-a` and `b1` is `/hold/s24-b`. The write after the Join reads `a1.text`.

Steps and asserts:
1. Wait until `GET /api/runs/{id}` shows `a1`'s checkpoint while `b1`'s read is outstanding (the fixture saw it).
2. SIGKILL the worker, then start a new one. Wait for the real 30 s lease, with no database change (the S15 precedent).
3. The run succeeds. `/lookup/s24-a` was requested once, so `a1` was restored. `/hold/s24-b` was requested twice. The Join has one success.
4. The receiver got exactly one POST, with `a1`'s answer.

Difference from D01: D01 restores model branches and checks tokens on SQLite and PostgreSQL. S24
uses HTTP tool branches and checks the external write after the Join. Both stay.

### Docs

- `docs/production-scenario-testing.md`: catalogue rows S22, S23a, S23b, S24, plus a realism note: the fork rows use HTTP read branches, not model branches.
- `README.md`:
  - the roadmap marks A5.3 Part 3 done and A6 next;
  - the S-range in §2 becomes S01–S24;
  - the §2 parallel-branches evidence names S22–S24;
  - the counts.
- `docs/platform-reference.md`: the roadmap row.

## Tests and evidence plan

**Baseline.** The new rows on a clean archive of `36afe6b` plus the gate changes only. Expected:
all four pass (**control**: the behavior exists). This is recorded, not hidden.

**Mutants.** Each mutant goes on a fresh scratch copy. The planned row must fail, and every row's
result is recorded:

| Id | Mutant (product code) | Planned oracle | Why it fails |
|---|---|---|---|
| M27 | One node handler at a time (a module-level `asyncio.Lock` around node execution) | S22 | The second meet request never opens with the first: 504, run fails |
| M28 | The Join is entered by one edge per branch instead of one list edge | S22 | Branch B is longer, so the Join runs twice and the write is sent twice |
| M29 | A failed branch node is swallowed as if `on_error: continue` | S23a | The Join runs and the write is sent |
| M30 | The "external writes belong after the Join" rule is removed | S23b | Validation accepts; submission creates a run |
| M31 | Restore ignores saved checkpoints of branch nodes | S24 | `/lookup/s24-a` is requested twice |
| M32 | Join bindings use the intersection, not the union | S22 | `send` cannot bind `a1.text`: refused at validation |

If a mutant survives, the row is strengthened before review, and the change is recorded. M28's
failure mode (two Join runs with unequal branches) is a prediction from LangGraph's superstep
model. It is checked first, and if it does not hold, the row and the table are corrected.

**Gates.**
- The deployed scenario gate: 28 of 28 rows, `source_files_changed` empty, **3 consecutive runs** (the new rows depend on concurrency, so one green run is not enough).
- The full backend suite at PYTHONHASHSEED 0–4 in an isolation copy, PostgreSQL cases on a throwaway container (55433). `test_scenario_gate.py` imports the gate script, so this is required.
- Hosted CI 5 of 5 on the exact commit. Hosted CI does not run the scenario gate (it is the manual `production-scenarios.yml`), so the gate evidence is local and recorded.
- Evidence: `evals/results/a53-part3-<date>/` with the baseline, mutants, three gate reports and a README.

**Cost.** S24 waits for the real lease, so the gate takes about 40 s longer (today about 70 s).

## Review questions

1. **Contract 1 tool branches, not a contract-2 Request reader fork.** I recommend contract 1 in
   the main lab:
   - The rows measure fork, Join, cancellation and resume. HTTP tool branches give exact,
     per-branch answers.
   - The model fixture answers from one queue, so with concurrent model branches the answer order
     is not fixed. That makes per-branch assertions weaker.
   - The contract-2 fork is covered by R06 (runtime), B01 and K01 (editor and real validator).

   Accept, or ask for one contract-2 row in the Switch lab?
2. **The real 30 s lease in S24.** I recommend keeping it, as S15 does (no database change to
   speed recovery). The cost is about 35 s per gate run. Accept?

## Not claimed

- PostgreSQL in the scenario gate (the gate is SQLite; D01 covers a fork crash on PostgreSQL).
- Model branches and token accounting in the gate (A01–A06, D01, D02 cover them).
- Any speed claim. The rows prove overlap, not a time saving.
- Writes or approvals in branches (A5.3b), optional branches (A5.3c), nested forks.

After Part 3 closes, A5.3 is complete. The next roadmap item is A6 (subgraphs and bounded loops).
