# A6.1 Part 3: deployed scenarios for Repeat

**Status:** Revision 1 (2026-11-16), for design review. No row is written yet.
**Baseline:** `8705b7f` (hosted CI run `43943065132`, 5/5 green). A6.1 Part 1 closed on
`00281fb`, Part 2 on `05aca4a`.
**Parent design:** `docs/superpowers/specs/2026-11-09-bounded-loops-design.md` (rev 5),
"Scope": Part 3 is "deployed scenarios in the release gate".
**Probe:** `evals/results/a61-part3-probe-2026-11-16/`.
**Tier:** P2 if Q1 is decided as A (evidence only). P1 if Q1 is decided as B (one validation
rule, below). Design review, then the rows and their mutants, then an implementation review.

## Outcome

The deployed scenario gate (`scripts.check_production_scenarios`) runs Repeat through separate
API and worker processes, real authentication, SQL storage, leases and HTTP tool transport, and
checks what the receiver got, not only the run status:

1. A writer and a checker loop until the checker accepts, the writer gets the feedback, and the
   write after the Repeat sends the accepted draft once.
2. A Repeat that never meets its rule sends nothing (`on_limit: fail`).
3. A write inside the loop is refused before any run exists.
4. A run-wide budget that stops the checker sends no unchecked draft.
5. A worker killed in an iteration resumes: saved iterations are not called again, and the write
   is sent once.
6. A write after the Repeat that needs approval shows the reviewer exactly the accepted draft,
   and the approval resume does not call the models again.

The gate policy (`docs/production-scenario-testing.md`, "Policy for each new phase") requires a
business workflow, its negative and recovery counterparts, and cross-phase scenarios (budgets +
iteration, approvals + iteration). Today the gate has 28 rows and none of them runs a Repeat.

## Evidence

### What is already proven, and where

| Guarantee | Existing evidence | Level |
|---|---|---|
| Iterations, stop rule, typed verdict, limit, `on_limit` | `test_repeat.py` (82 tests) | Real Worker and Store in one process, scripted model |
| Budget stops, guard abstention, restore checks | R17–R28 in `test_repeat.py` | Same |
| Write and memory Agent refused in the loop | V-cases, K01 (`/api/validate`) | Validation only |
| Crash in an iteration | `sqlite/postgres-repeat-sigkill` (`check_milestone_storage.py`) | Deployed API and worker, SQLite and PostgreSQL, real lease, **no write** |
| Editor builds a valid Repeat | B01–B08, K01–K03 | Browser with a mocked API, plus backend contracts |

**Gap.** No row in the release gate runs a Repeat. No deployed check shows what the receiver
gets after a Repeat: the accepted draft once, nothing on an unmet rule or a budget stop, one
write after a crash, the exact draft in an approval.

### Probe (2026-11-16)

`evals/results/a61-part3-probe-2026-11-16/` (the gate's `Lab` with a model fixture keyed by
model name, clean `git archive 8705b7f`, `RELAY_RUN_TOKEN_LIMIT=100`). Every planned check holds
today. One observation needs a decision (Q1):

| Probe | Observed |
|---|---|
| P25 | Rejected once, then accepted. The second writer request carried `{"input","previous","feedback"}`. One POST with the accepted draft. 60 tokens; action counter 1 (`send#0`) |
| P26a | `on_limit: fail`, never accepted: the run fails with `r: the stop rule was not met after 2 iterations.`; no POST |
| P26b | `on_limit: last`, never accepted: **success with `truncated: true`, and the rejected draft is sent, with no approval** |
| P26c | A write as the body: refused at validation and submission with `writer: external writes belong after the Repeat.`; no run, no request |
| P27a | The token limit stops iteration 2's checker, `on_limit: fail`: the run fails with the budget message; the checker was called once; no POST |
| P27b | Same with `on_limit: last`: **success with `truncated: true`, and the rejected draft 1 is sent, with no approval** |
| P28 | SIGKILL with the writer's iteration-2 request held: at the kill, row 0 saved, no POST. After the real lease (30.2 s, claim 0.088 s after `lease_until`, another worker): writer 3 calls, checker 2, 60 tokens, one POST |
| P29 | The write needs approval: the pending payload is the accepted draft; 0 POST before approval; after it one POST, and no further model call |

## Q1 (decision needed): may a write send a draft that the checker did not accept?

Under `on_limit: last`, a Repeat succeeds with the last answer when the rule was not met (limit)
or when a budget stopped an iteration (the last fully evaluated answer). The run is marked
`truncated`. A write after the Repeat then sends that answer at once (P26b, P27b). The checker
is the reason to use a Repeat, so this is a way to send unaccepted text automatically. It is
the documented contract (`docs/operations.md`, "The limit", "Budgets"), not a regression.

| Option | Change | S26b asserts |
|---|---|---|
| **A. Keep the contract** | None (Part 3 stays evidence only) | The run is `truncated` with the limit reason, and the rejected draft is sent once. The gate then records the bypass as passing behavior |
| **B. Require approval (recommended)** | One validation rule: a write that is reachable after a Repeat with `on_limit: last` must have `approval: true`. Message: `send: a write after r needs approval, because r can return an answer that did not meet its stop rule (on_limit: last).` Shown at Validate and at submission (422), as the other write rules | Refused without approval (no run, no request). With approval: the run waits, the payload is the rejected draft, a rejection sends nothing |
| C. Runtime refusal | A write refuses an input from a truncated Repeat | Broader: the same question exists for a truncated For each batch (S09), so this belongs in its own design |

**Why B.** It closes the bypass with the smallest change, in the pattern the platform already
uses ("external writes belong after the Join / the Repeat"): a static rule, checked before any
run, with no runtime or storage change. A person still decides whether an unaccepted draft is
sent. `on_limit: fail` and Repeats with no write after them are not affected. A Repeat with no
checker and `on_limit: last` is covered too: its answer also did not meet the rule.

**Cost of B.** The rule walks the nodes reachable from each `on_limit: last` Repeat (the graph
is acyclic, at most 100 nodes). A workflow saved since Part 1 with this shape becomes invalid
and says why. Tests: one validation case per message path (a direct write, a write two nodes
later, a write in a later fork branch is already refused by the branch rule, `approval: true`
accepted, `on_limit: fail` accepted), the S26b row, and mutant G9. `docs/operations.md` "The
limit" gets one sentence. The editor needs no change: it shows the backend's message.

## Design

### Lab and fixture

The rows run in a **new third lab** (Q2), as the Switch rows run in their own lab:

- the worker and API get `OLLAMA_BASE_URL` of a model fixture and `RELAY_RUN_TOKEN_LIMIT=100`;
- the 28 existing rows keep their environment unchanged;
- a new module `scripts/scenarios/repeat_loops.py` has the fixture and
  `repeat_scenarios(lab, models)`; `check_production_scenarios.main` runs it after the Switch lab.

**`LoopModels`** (an HTTP server for the worker's Ollama client), keyed by model name. Each row
registers its own `<row>-writer` and `<row>-judge` models, so call counts are exact per row.

| Setting | Meaning |
|---|---|
| `script(model, [(content, tokens, hold), ...])` | The answers in order. `tokens` is the reported count. A `hold` answer waits until the row calls `release()`, at most 120 s |
| `count(model)`, `requests(model)` | Calls and their messages, as received |

An unexpected call answers `unexpected model call`, which fails the checker's schema, so a row
never passes by accident. A hold replaces a sleep (the A5.3 Part 3 rule).

**The workflow** (one builder, options per row):

```
input ─► r Repeat (body writer, check judge, until pass eq true, max 3) ─► send POST /deliver/<row> (r.text) ─► out (r.text)
           │ body  ─► writer  Agent, model <row>-writer
           │ check ─► judge   Agent, model <row>-judge, role critic, output_schema {pass: boolean, feedback: string}
```

### Rows

**S25 — A support reply is drafted, checked and sent once.** Writer: draft 1, draft 2. Checker:
`{pass: false, feedback: "add the refund number"}`, then `{pass: true}`. 15 tokens per call.

Asserts:
- `/api/validate` accepts the workflow; the run succeeds;
- writer 2 calls, checker 2: the loop stopped when the rule was met;
- the second writer request's user message is exactly
  `{"input":"Customer case","previous":<draft 1>,"feedback":<first verdict>}`: the feedback reached the writer;
- rows 0 (`met: false`) and 1 (`met: true`); summary `iterations: 2`, `stopped_by: rule`;
- `send`'s `running` event comes after `r`'s `success`;
- the receiver got exactly one POST, and it carries draft 2;
- tokens 60 (each answered call charged once); action counter 1 (`send#0`): model calls are
  not actions.

**S26a — A reply that is never accepted is not sent.** `max_iterations: 2`, `on_limit: fail`,
the checker rejects twice.

Asserts: the run fails with exactly `r: the stop rule was not met after 2 iterations.`; writer 2,
checker 2 (no third iteration); `send` has no event; the receiver got 0 POST.

**S26b — An unaccepted reply under `on_limit: last`.** The same, with `on_limit: last`. The
asserts depend on Q1 (see the table above). Under B: without approval, refused at validation and
submission with the new message, no run, no request; with approval, the run waits, the pending
payload is the rejected draft 2, the run is `truncated` with
`r reached 2 iterations without meeting its stop rule; the last answer is returned.`, and a
rejection ends the run with 0 POST.

**S26c — A write inside the loop is refused before any run.** The body is an HTTP POST.

Asserts: `/api/validate` returns exactly `['writer: external writes belong after the Repeat.']`;
`POST /api/runs` returns 422 with the same text; the run count does not change; no request to
the receiver and no model call.

**S27 — A budget that stops the checker sends no unchecked draft.** 40 tokens per call, limit
100, `on_limit: fail`. Iteration 1: writer (40), checker rejects (80). Iteration 2: the writer is
admitted (80 < 100) and answers (120); its checker is refused.

Asserts: the run fails with exactly `r: the run budget stopped iteration 2 before it was
checked. Run token budget exhausted; raise RELAY_RUN_TOKEN_LIMIT.`; row 1 is
`{status: interrupted, budget_halt: tokens, calls: 1}`; the checker was called once (draft 2
was never checked); 0 POST; tokens 120, and the run's `token_limit` record shows `over: 20`
(the admission limit's documented overshoot of one call).

**S28 — A worker killed in an iteration resumes without repeating saved iterations.** Writer:
draft 1, then a held draft 2, then draft 2. Checker: rejects, then accepts.

Steps and asserts:
1. Wait until the writer's second request is open (it is held).
2. SIGKILL the worker. Before a new worker starts: row 0 is saved, `r` has no checkpoint, the
   receiver has 0 POST, tokens 30, and the job is `running` with an owner. Then release the hold.
3. Start a new worker. Wait for the real 30 s lease, with no database change.
4. The run succeeds. Writer 3 calls (iteration 2 again), checker 2: iteration 1 was not called
   again. Row 0 equals the row before the kill. Tokens 60: the killed request was never
   answered, so never charged.
5. The receiver got exactly one POST, with draft 2.
6. The replacement's claim event is not earlier than the killed worker's `lease_until`, and the
   two claims are by different workers (the S24 check).

Difference from `*-repeat-sigkill`: those rows run on SQLite and PostgreSQL with no write; S28
checks the external write after the Repeat. Both stay.

**S29 — The approval after a Repeat shows the accepted draft.** As S25, with `approval: true`
on `send`.

Asserts: the run is `awaiting_approval`; the pending payload's body is exactly the accepted
draft; 0 POST. After approval: the run succeeds, one POST with that draft, and writer and checker
are still at 2 calls: the resume restored the Repeat's checkpoint and called no model.

### Docs

- `docs/production-scenario-testing.md`: catalogue rows S25–S29, and a realism note: the Repeat
  rows use a model fixture, so they measure loop control, persistence and delivery, not a
  checker's judgment.
- `README.md`: the roadmap marks A6.1 Part 3 done (A6.1 complete) and A6.2 next; the S-range in
  §2 becomes S01–S29; the bounded-loop evidence names S25–S29; the counts.
- `docs/platform-reference.md`: the roadmap row (line 481).
- Under Q1 = B: `docs/operations.md`, "The limit": the approval rule.

## Tests and evidence plan

**Baseline.** The new rows on a clean archive of `8705b7f` plus the gate changes only.
Expected: S25, S26a, S26c, S27, S28, S29 pass (**control**: the behavior exists). Under Q1 = B,
S26b fails on the baseline (the shape is accepted and the draft is sent), and its validation
tests are red. This is recorded, not hidden.

**Mutants.** Each mutant goes on a fresh scratch copy, with an M00 control (no change: every
row passes) and a guard that stops the run if a planned row did not run. The planned row must
fail, and every row's result is recorded:

| Id | Mutant (product code) | Planned oracle | Why it fails |
|---|---|---|---|
| G1 | The next body input is the Repeat input only (no previous, no feedback) | S25 | The second writer request differs |
| G2 | The stop rule reads the body output, not the checker's verdict | S25 | Draft 1 is not JSON: the Repeat fails |
| G3 | The first iteration always counts as met | S25, S26a | One iteration; draft 1 is sent |
| G4 | `on_limit: fail` behaves as `last` | S26a | The run succeeds and the rejected draft is sent |
| G5 | The write rule ignores the Repeat's closure | S26c | Validation accepts; submission creates a run |
| G6 | An iteration stopped before its checker returns its unchecked draft as met | S27 | Draft 2 is sent |
| G7 | Resume ignores saved rows | S28 | Writer 4 calls, checker 3 |
| G8 | The replacement worker claims 25 s before the lease ends | S28 | The claim is earlier than `lease_until` |
| G9 | A checkpointed Repeat runs again after the approval resume | S29 | Writer and checker are called again (the mutation point is confirmed in the implementation) |
| G10 | (Q1 = B) The approval rule is removed | S26b | Validation accepts the write without approval |

If a mutant survives, the row is made stronger before review, and the change is recorded.

**Gates.**
- The deployed scenario gate: 34 of 34 rows (28 + 6, or 35 with S26b's two parts if the review
  splits it), `source_files_changed` empty, **3 consecutive runs** (S28 depends on a kill and a
  lease).
- The full backend suite at PYTHONHASHSEED 0–4 in an isolation copy, PostgreSQL cases on a
  throwaway container (55433). `test_scenario_gate.py` imports the gate script, so this is
  required. Under Q1 = B, the frontend checks too (unit, typecheck, lint, build, browser): the
  K01 fixtures are recorded from the real validator.
- Hosted CI 5 of 5 on the exact commit. Hosted CI does not run the scenario gate, so the gate
  evidence is local and recorded.
- Evidence: `evals/results/a61-part3-<date>/` with the baseline, mutants, three gate reports and
  a README.

**Cost.** S28 waits for the real lease, and the third lab boots once: the gate takes about 35 s
longer (today about 100 s).

## Review questions

1. **Q1** (above): A, B or C. Recommendation: B.
2. **Q2: a third lab.** The Repeat rows need a model fixture and a token limit. Adding them to
   the Switch lab would change that lab's environment (`RELAY_RUN_TOKEN_LIMIT`). Recommendation:
   a new lab, as the Switch rows got one.
3. **Q3: S29 (approvals + iteration).** The policy asks for this cross-phase case, and P29 shows
   the resume calls no model. Recommendation: keep it.

## Not claimed

- A checker's judgment quality. The rows use a model fixture; live models are out of the gate.
- PostgreSQL in the gate (the gate is SQLite; `postgres-repeat-sigkill` covers a Repeat crash
  on PostgreSQL).
- The answerability-guard stop in the gate (it needs a knowledge base; R27a–j cover it in the
  backend suite).
- The same question for a truncated For each batch before a write (S09 shape). Not changed here.
- Nested loops, writes or approvals inside a loop, a wait between iterations.

After Part 3 closes, A6.1 is complete. The next roadmap item is A6.2 (subgraphs), which needs
published workflow versions first.
