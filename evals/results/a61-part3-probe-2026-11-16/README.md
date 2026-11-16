# A6.1 Part 3 design probe (2026-11-16)

`probe.py` runs each planned Repeat row through the gate's deployed `Lab` (separate API and
worker processes, real authentication, SQLite, leases, HTTP tool transport) on a clean
`git archive 8705b7f`. The model is a local fixture keyed by model name: each row scripts its
writer and checker answers, the tokens each answer reports, and which request is held open.
The worker runs with `RELAY_RUN_TOKEN_LIMIT=100`. Writes go to the gate's HTTP receiver.

Files: `notes.json` (what each probe observed), `probe-output.txt` (the console),
`runs.json` (every run history), `provider-requests.json` (the receiver's requests).

| Probe | Workflow | Observed |
|---|---|---|
| P25 | Writer + checker, rejected once then accepted; one write after the Repeat | Success. Writer 2 calls, checker 2. The second writer request carried `{"input","previous","feedback"}` with draft 1 and the rejection. Rows 0 (`met: false`) and 1 (`met: true`), summary `iterations: 2`, `stopped_by: rule`. One POST with the accepted draft. 60 tokens (4 × 15). Action counter 1 (`send#0`): model calls are not actions |
| P26a | Never accepted, `max_iterations: 2`, `on_limit: fail` | Failed: `r: the stop rule was not met after 2 iterations.` Writer 2, checker 2. `send` has no event. No POST |
| P26b | Never accepted, `max_iterations: 2`, `on_limit: last`, write after | **Success, `truncated: true`. The rejected draft 2 was sent: one POST, no approval.** Summary `met: false`, `stopped_by: limit` |
| P26c | An HTTP write as the Repeat body | Validate and submission refused with exactly `writer: external writes belong after the Repeat.` (422). No run, no request, no model call |
| P27a | 40 tokens per call, limit 100, `on_limit: fail` | Failed: `r: the run budget stopped iteration 2 before it was checked. Run token budget exhausted; raise RELAY_RUN_TOKEN_LIMIT.` Row 1 `interrupted`, `budget_halt: tokens`, `calls: 1`. Checker called once: draft 2 was never checked. No POST. 120 tokens (the admission limit; one call of overshoot) |
| P27b | Same, `on_limit: last` | **Success, `truncated: true`. The rejected draft 1 was sent: one POST, no approval.** Reason: `r: the run budget stopped iteration 2 before it was checked; the answer from iteration 1 is returned. …` |
| P28 | SIGKILL while the writer's iteration-2 request is held; write after | At the kill: row 0 saved, no `r` checkpoint, 0 POST, 30 tokens, job `running` with an owner. After the real lease (30.2 s; the replacement's claim came 0.088 s after the killed worker's `lease_until`, by another worker): success, writer 3 calls (iteration 2 again), checker 2, 60 tokens (the killed request was never answered, so never charged), one POST with draft 2 |
| P29 | Accepted on iteration 2; the write after needs approval | `awaiting_approval`; the pending payload is exactly the accepted draft; 0 POST before approval. After approval: one POST with that draft; writer and checker not called again (2 and 2). `r` shows `running, success, success` (the restore after the approval adds one success) |

## Consequence

Every planned check holds on `8705b7f` except a question of contract. P26b and P27b show
that, under `on_limit: last`, a draft that the checker rejected (or never checked) reaches an
external write with no approval. The only signal is `truncated: true` on the run. This is the
documented behavior (`docs/operations.md`, "The limit" and "Budgets"), not a regression, but it
lets an automatic send bypass the checker. The design asks for a decision before the rows are
written (Q1).
