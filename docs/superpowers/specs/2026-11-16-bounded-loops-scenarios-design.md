# A6.1 Part 3: deployed scenarios for Repeat

**Status:** Revision 2 (2026-11-16), approved for implementation, with three details ("Recheck details").
Rev 1 is `007dac4`. The rev 1 review decided Q1 = B, Q2 = a third lab, Q3 = keep S29, and asked
for two changes (attached writes in the rule; the S26b asserts while waiting) and one stronger
S29 assert. "Changes from revision 1" lists them.
**Baseline:** `8705b7f` (hosted CI run `43943065132`, 5/5 green). A6.1 Part 1 closed on
`00281fb`, Part 2 on `05aca4a`.
**Parent design:** `docs/superpowers/specs/2026-11-09-bounded-loops-design.md` (rev 5),
"Scope": Part 3 is "deployed scenarios in the release gate".
**Probe:** `evals/results/a61-part3-probe-2026-11-16/`.
**Tier:** P1 (one validation rule, below). Design recheck, then the rule and the rows tests-first
with their mutants, then an implementation review.

## Recheck details (rev 2 approval)

| # | Detail | Where it is applied |
|---|---|---|
| I1 | G7 fails on the saved iteration, not on the call counts. A resume that ignores the saved rows also makes 3 writer and 2 checker calls, 60 tokens and one POST of draft 2, but it overwrites row 0 and counts one iteration | S28 keeps the exact row comparison and asserts `iterations: 2`; G7's expected failure is updated |
| I2 | V-A6 needs a valid list input: `Repeat.text` is a string, which For each refuses before the approval rule matters | V-A6 reads `tickets.items` from an upstream Jira read, so only the approval rule refuses it |
| I3 | Only the Repeat's own error edge is exempt: a later step's error handler can bind `r.text`. Imported drafts can contain cycles | The walk skips only the Repeat's own `error` edges and keeps a visited set. New cases V-A12 (a write on a later step's error route is refused) and V-A13 (a cycle after the Repeat terminates); mutants G14 and G15 |

## Changes from revision 1

| # | Review item | Change in rev 2 |
|---|---|---|
| D1 | Q1 = B, with explicit `approval: true` | "Decision 1". The rule needs the literal `approval: true`: an unset approval is refused, because validation runs before a run exists, and the unset default depends on the run's authentication (`worker.py:175`) |
| R1 | Writes attached to downstream steps | The rule covers each reachable step's attachment closure (`attachment_closure`, `compiler.py:244`): tools, tools behind specialists, For each bodies. On `8705b7f` no rule walks downstream of a Repeat at all, so `Repeat → Agent (POST attached) → Response` validates. New validation cases name the attached write; mutant G11 |
| R2 | What S26b checks while it waits | S26b runs both fallbacks (the limit and the token budget). It asserts `checkpoints.r.summary` (stop reason, `met: false`), the exact pending payload and 0 deliveries. It asserts that the run-level `truncated` is absent while waiting and after the rejection: that flag is set only when a run succeeds (`run_truncation`, `worker.py:236`). `docs/operations.md` says where a reviewer finds the stop reason |
| R3 | S29 proves checkpoint reuse | S29 asserts that the resumed `r` event is `success` with `cached: true` (`compiler.py:672`). Call counts alone also hold when the Repeat rebuilds its result from saved rows (`repeat.py:337`). Mutant G9 is now that rebuild |
| R4 | The row count | 7 new rows, so the gate has 35 rows. Rev 1 said 34 |

## Outcome

The deployed scenario gate (`scripts.check_production_scenarios`) runs Repeat through separate
API and worker processes, real authentication, SQL storage, leases and HTTP tool transport, and
checks what the receiver got, not only the run status:

1. A writer and a checker loop until the checker accepts, the writer gets the feedback, and the
   write after the Repeat sends the accepted draft once.
2. A Repeat that never meets its rule sends nothing (`on_limit: fail`).
3. Under `on_limit: last`, a write after the Repeat cannot send without a person's approval, and
   the reviewer can see that the draft did not meet the stop rule.
4. A write inside the loop is refused before any run exists.
5. A run-wide budget that stops the checker sends no unchecked draft.
6. A worker killed in an iteration resumes: saved iterations are not called again, and the write
   is sent once.
7. A write after the Repeat that needs approval shows the reviewer exactly the accepted draft,
   and the approval resume reuses the Repeat's checkpoint.

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
model name, clean `git archive 8705b7f`, `RELAY_RUN_TOKEN_LIMIT=100`):

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

The rev 1 review added two facts on `8705b7f` (its own probes, separate API and worker):

- With approval on the write, the limit fallback offers draft 2 and the budget fallback offers
  draft 1 (the last draft the checker evaluated). A rejection sends nothing in both. The
  run-level `truncated` is absent while waiting and after the rejection.
- `Repeat → Agent → Response` with an unapproved POST attached to the Agent validates. The POST
  is not in the flow graph.

## Decision 1 (Q1 = B): a write after an `on_limit: last` Repeat needs approval

Under `on_limit: last`, a Repeat succeeds with an answer that did not meet its stop rule: the
last answer at the limit, or the last checked answer after a budget stop. A write after it then
sends that answer at once (P26b, P27b). The checker is the reason to use a Repeat, so this sends
unaccepted text automatically. It is the documented contract (`docs/operations.md`, "The limit",
"Budgets"), not a regression. The review chose B.

**The rule.** For each Repeat with `on_limit: last`:

1. Find the steps reachable from the Repeat through flow edges. Start at the Repeat's edges that
   are not its own `error` route. A step that only that route reaches runs when the Repeat failed
   and returned no answer. After the first step every edge is followed, also a later step's
   `error` route, because its handler can bind `r.text`. The walk keeps a visited set.
2. For each of those steps, add its attachment closure (`attachment_closure`): the tools of an
   Agent, the tools behind its specialists, a For each body, and the attachments of those.
3. Every write among them (`is_write_node`) must have the literal `approval: true`.

**The message** (one per write, at Validate and at submission, 422, as the other write rules):

```
<write id>: a write after <repeat id> needs approval: true, because <repeat id> can return an answer that did not meet its stop rule (on_limit: last).
```

For a write attached to a later Agent, `<write id>` is the attached tool, so the message names
the node that must change.

**Why the literal `true`.** An unset approval is "on for authenticated writes": the worker reads
`config.approval`, else the run's `approval_required`, which `POST /api/runs` sets from the
authentication setting at run creation (`worker.py:175`, `main.py:159`). Validation has no run.
An unset approval would therefore pass validation and send with no approval on a deployment
without authentication. The rule asks for the setting that holds on every deployment.

**Tradeoff (accepted by the review).** A draft that the checker accepted also waits for approval,
because the workflow allows a fallback. The author who wants automatic sends uses
`on_limit: fail`, the default. The review cites two platforms with the same two patterns: an
explicit fail-on-limit setting for loops ([Azure Logic Apps](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-control-flow-loops#until-loop-considerations))
and a pause for a human decision before the execution continues
([AWS Step Functions](https://docs.aws.amazon.com/step-functions/latest/dg/tutorial-human-approval.html)).

**Not affected.** `on_limit: fail`; a Repeat with no write after it; a write before the Repeat;
a write that only the Repeat's error route reaches. A Repeat with no checker and `on_limit: last`
is covered: its answer also did not meet the rule. No runtime, storage or editor change: the
editor shows the backend's message.

**Existing workflows.** A workflow saved since Part 1 with this shape becomes invalid and says
why. No current test or fixture has the shape: the `on_limit: last` cases in `test_repeat.py`
have no write after the Repeat, and the recorded frontend fixtures (`repeat-issues.json`,
`request-nodes.json`) use `on_limit: fail` or none. The full suite confirms this.

**Where it goes.** `repeat_errors` (`compiler.py:253`) already has the deps and
`attachment_closure`. It also needs the flow graph, so `validate_workflow` passes `flow`.

**Validation cases** (`backend/tests/test_repeat_write_approval.py`, which uses the builders of `test_repeat.py`; tests first):

| Case | Shape | Expected |
|---|---|---|
| V-A1 | `r (last) → send (approval false) → out` | Refused, names `send` |
| V-A2 | Same, approval unset | Refused, names `send` |
| V-A3 | `r (last) → agent → send → out` (a write two steps later) | Refused, names `send` |
| V-A4 | `r (last) → agent → out`, POST `post` attached to `agent` as a tool | Refused, names `post` |
| V-A5 | `r (last) → lead`, specialist `helper` with POST `post` attached | Refused, names `post` |
| V-A6 | `tickets (Jira read) → r (last) → each (For each over tickets.items, body POST `post`) → out` | Refused, names `post` |
| V-A7 | V-A1, V-A4, V-A5 and V-A6 with `approval: true` on each write | Valid |
| V-A8 | V-A1 with `on_limit: fail` | Valid |
| V-A9 | `send (approval false) → r (last) → out` (the write is before) | Valid |
| V-A10 | `r (last, on_error route)`: error route → `send (approval false)` → `fallback`; normal → `out` | Valid |
| V-A11 | V-A1 through `/api/validate` and `POST /api/runs` | The message at Validate, 422 with the same message, no run; with `approval: true` the message is gone (the API adds workspace checks, so the list is not compared exactly) |
| V-A12 | `r (last) → agent (on_error route)`: the agent's error route → `send (binding r.text, approval false)` → `fallback` | Refused, names `send` |
| V-A13 | `r (last) → a → b → a` (a cycle) `→ send` | Validation finishes (a thread with a 10 s limit) with the cycle error and the approval message |

## Design

### Lab and fixture (Q2: a third lab)

The rows run in a **new third lab**, as the Switch rows run in their own lab:

- the worker and API get `OLLAMA_BASE_URL` of a model fixture and `RELAY_RUN_TOKEN_LIMIT=100`;
- the 28 existing rows keep their environment unchanged;
- a new module `scripts/scenarios/repeat_loops.py` has the fixture and
  `repeat_scenarios(lab, models)`; `check_production_scenarios.main` runs it after the Switch lab.

**`LoopModels`** (an HTTP server for the worker's Ollama client), keyed by model name. Each row
(and each S26b variant) registers its own `<row>-writer` and `<row>-judge` models, so call
counts are exact per row.

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

`send` has `enable_writes: true` and an explicit `approval` (`false` unless the row says
`true`), so no row depends on the authentication default.

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

**S26b — An unaccepted reply under `on_limit: last` waits for a person.** One row, three parts.
Each part has its own models and its own receiver path.

1. **Refused without approval.** The S26a workflow with `on_limit: last` and `approval: false`.
   `/api/validate` returns exactly the Decision 1 message for `send`; `POST /api/runs` returns
   422 with the same text; the run count does not change; no model call; 0 POST.
2. **The limit fallback.** `max_iterations: 2`, the checker rejects twice, `approval: true`.
   - The run is `awaiting_approval`. Writer 2 calls, checker 2.
   - `checkpoints.r.summary` is `{iterations: 2, max_iterations: 2, met: false, stopped_by:
     limit, truncated: true, truncation_reason: "r reached 2 iterations without meeting its stop
     rule; the last answer is returned."}`.
   - The pending approval's payload body is exactly `{"input":"<draft 2>"}`. 0 POST.
   - The run has no `truncated` key while it waits.
   - Reject. The run is `cancelled` with `Approval rejected. No external request was sent.`;
     0 POST; still no run-level `truncated`; `POST /resume` returns 409.
3. **The budget fallback.** 40 tokens per call, limit 100, the checker rejects iteration 1,
   `approval: true`.
   - The run is `awaiting_approval`. Writer 2 calls, checker 1: draft 2 was never checked.
   - `checkpoints.r.summary` has `iterations: 1`, `met: false`, `stopped_by: budget`,
     `truncated: true`, and the exact budget reason (`r: the run budget stopped iteration 2
     before it was checked; the answer from iteration 1 is returned. Run token budget exhausted;
     raise RELAY_RUN_TOKEN_LIMIT.`).
   - The pending payload body is exactly `{"input":"<draft 1>"}`: the last draft the checker
     evaluated, not draft 2. 0 POST. No run-level `truncated` while it waits.
   - Reject: as part 2.

The summary values are the probe's and the review's observations on `8705b7f`. The implementation
records them from the row, and the row asserts them exactly.

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

**S29 — The approval after a Repeat shows the accepted draft (Q3: kept).** As S25, with
`approval: true` on `send`.

Asserts:
- the run is `awaiting_approval`; the pending payload's body is exactly `{"input":"<draft 2>"}`;
  0 POST;
- after approval: the run succeeds, with one POST of that draft;
- writer and checker are still at 2 calls;
- the events after the approval include `r` `success` with `cached: true`: the resume used the
  Repeat's checkpoint. The call counts alone also hold when the Repeat rebuilds its result from
  its saved rows (`repeat.py:337`), so only this assert proves the checkpoint reuse.

### Docs

- `docs/production-scenario-testing.md`: catalogue rows S25–S29, and a realism note: the Repeat
  rows use a model fixture, so they measure loop control, persistence and delivery, not a
  checker's judgment.
- `docs/operations.md`, "The limit":
  - the approval rule and its reason;
  - where a reviewer finds the stop reason. The approval card shows the outgoing request only
    (`approval-review.tsx:68`). In the run's execution log, the Repeat's `success` event has
    `outputs.summary` with `met`, `stopped_by` and `truncation_reason`.
- `README.md`: the roadmap marks A6.1 Part 3 done (A6.1 complete) and A6.2 next; the S-range in
  §2 becomes S01–S29; the bounded-loop evidence names S25–S29; the approval rule in the Repeat
  item; the counts.
- `docs/platform-reference.md`: the roadmap row (line 481), and the Repeat validation rules.

## Tests and evidence plan

**Baseline.** The new tests on a clean archive of `8705b7f` plus the test and gate changes only.

- Expected pass (**control**: the behavior exists): S25, S26a, S26c, S27, S28, S29, and the
  valid cases V-A7 to V-A10.
- Expected fail (**red**): S26b part 1 (the shape is accepted and the draft is sent), and V-A1 to
  V-A6 and V-A11.
- S26b parts 2 and 3 pass on the baseline: approval already holds the draft. The row as a whole
  fails at part 1.

This is recorded, not hidden.

**Mutants.** Each mutant goes on a fresh scratch copy, with an M00 control (no change: every
planned oracle passes) and a guard that stops the run if a planned oracle selected no test or
row. The planned oracle must fail, and every oracle's result is recorded:

| Id | Mutant (product code) | Planned oracle | Why it fails |
|---|---|---|---|
| G1 | The next body input is the Repeat input only (no previous, no feedback) | S25 | The second writer request differs |
| G2 | The stop rule reads the body output, not the checker's verdict | S25 | Draft 1 is not JSON: the Repeat fails |
| G3 | The first iteration always counts as met | S25, S26a | One iteration; draft 1 is sent |
| G4 | `on_limit: fail` behaves as `last` | S26a | The run succeeds and the rejected draft is sent |
| G5 | The write rule ignores the Repeat's closure | S26c | Validation accepts; submission creates a run |
| G6 | An iteration stopped before its checker returns its unchecked draft as met | S27 | Draft 2 is sent |
| G7 | Resume ignores saved rows | S28 | Row 0 is overwritten and the summary counts one iteration (the call counts, tokens and POST are the same as without the mutant, I1) |
| G8 | The replacement worker claims 25 s before the lease ends | S28 | The claim is earlier than `lease_until` |
| G9 | The approval resume does not restore a Repeat's checkpoint; the Repeat rebuilds its result from its saved rows | S29 | No `cached: true` event for `r` (the call counts still hold) |
| G10 | The approval rule is removed | S26b, V-A1 | Validation accepts the write without approval |
| G11 | The rule walks flow steps only, not their attachments | V-A4, V-A5, V-A6 | The attached write is accepted |
| G12 | The rule accepts an unset approval | V-A2 | The write with no approval setting is accepted |
| G13 | The rule also walks the Repeat's error route | V-A10 | The valid error-route write is refused |
| G14 | The rule skips every error edge, not only the Repeat's own | V-A12 | The write on the later step's error route is accepted |
| G15 | The walk has no visited set | V-A13 | Validation does not finish |

If a mutant survives, the oracle is made stronger before review, and the change is recorded.

**Gates.**
- The deployed scenario gate: **35 of 35 rows** (28 + 7), `source_files_changed` empty,
  **3 consecutive runs** (S28 depends on a kill and a lease).
- The full backend suite at PYTHONHASHSEED 0–4 in an isolation copy, PostgreSQL cases on a
  throwaway container (55433). `test_scenario_gate.py` imports the gate script, so this is
  required.
- The frontend checks (unit, typecheck, lint, build, all browser tests), because the validation
  rules change. The recorded fixtures are regenerated from the real validator; no change is
  expected, and a change is recorded.
- Hosted CI 5 of 5 on the exact commit. Hosted CI does not run the scenario gate, so the gate
  evidence is local and recorded.
- Evidence: `evals/results/a61-part3-<date>/` with the baseline, mutants, three gate reports and
  a README.

**Cost.** S28 waits for the real lease, and the third lab boots once: the gate takes about 40 s
longer (today about 100 s).

## Decisions (rev 1 review)

1. **Q1:** B, with the literal `approval: true` and attached writes included (Decision 1).
2. **Q2:** a third lab. The Repeat rows need a model fixture and a token limit; the Switch lab
   keeps its environment.
3. **Q3:** keep S29, with the `cached: true` assert.

## Not claimed

- A checker's judgment quality. The rows use a model fixture; live models are out of the gate.
- The approval card does not show the Repeat's stop reason. Part 3 documents where to find it
  (the execution log). Showing it on the card is a separate change.
- PostgreSQL in the gate (the gate is SQLite; `postgres-repeat-sigkill` covers a Repeat crash
  on PostgreSQL).
- The answerability-guard stop in the gate (it needs a knowledge base; R27a–j cover it in the
  backend suite). A guard stop returns the refusal under either `on_limit`, not an earlier
  draft (`repeat_outcome`), so the approval rule is not needed for it.
- The same question for a truncated For each batch before a write (S09 shape). Not changed here.
- Nested loops, writes or approvals inside a loop, a wait between iterations.

After Part 3 closes, A6.1 is complete. The next roadmap item is A6.2 (subgraphs), which needs
published workflow versions first.
