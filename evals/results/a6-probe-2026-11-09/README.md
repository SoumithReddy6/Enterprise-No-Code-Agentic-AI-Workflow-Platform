# A6 design probe (2026-11-09)

Question: what does Relay do today with a loop, and can the existing typed condition read a
checker's JSON verdict? Baseline `3b89a3e` (same tree as `main` `758f869`). No product file was
changed. Validation only, in-process.

Run: `PYTHONPATH=. .venv/bin/python evals/results/a6-probe-2026-11-09/probe.py` from the
repository root. The condition checks at the end of `output.txt` were run inline with
`backend.app.conditions.evaluate`.

| Probe | Shape | Observed |
|---|---|---|
| Back-edge loop | input → draft → critic → Condition; `false` goes back to draft, `true` to the Response | `Workflow contains a cycle; loops are not supported yet.` |
| Nested For-each | For-each whose body is another For-each | `A loop body must be a tool, retrieval or Agent node.`, `Nested loops are not supported yet.` |
| Checker verdict | `eq` on field `pass`, compare to `true` | `{"pass": true}` → true; `{"pass": false}` → false |
| Score threshold | `gte` on field `score`, compare to `8` | `{"score": 8}` → true |
| Non-JSON verdict | `eq` on field `pass` | Error: `Condition field pass: the value is not JSON, so it has no fields.` |

Consequences for the design:

1. A loop cannot be drawn today, so A6 adds behavior. This is not an evidence-only phase, and
   the new tests must be red on the baseline.
2. The typed condition can be the stop rule without change: a JSON boolean compares as the
   text `true`, and a verdict that is not JSON is an error, not a silent "keep going".
