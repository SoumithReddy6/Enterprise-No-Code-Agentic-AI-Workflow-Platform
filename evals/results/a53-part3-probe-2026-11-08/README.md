# A5.3 Part 3 design probe (2026-11-08)

Question: do forks behave as the Part 1 design says when they run through the deployed API and
worker processes of the scenario gate? Baseline `36afe6b` (same application code as `main`
`2c3f26d`). No product or gate file was changed.

`probe.py` uses the gate's `Lab` (separate API and worker processes, SQLite, real auth and HTTP
tool transport). It replaces the gate's HTTP fixture with one that adds three paths:

- `/meet/<group>/<name>`: waits until two requests of the group have arrived (10 s), else
  answers 504. A name that starts with `fail` then answers 500; `slow` then waits 5 s.
- `/lookup/<name>`: answers `{"lookup": "<name>"}` at once.
- `/hold/<name>`: waits 5 s, then answers.

Run: `PYTHONPATH=. .venv/bin/python -u <probe.py> <out>` from the repository root. Result:
`notes.json`.

| Probe | Shape | Observed |
|---|---|---|
| S22 | input forks to `a1` (meet) and `b1` (meet) → `b2` (lookup); Join; write reads `a1.text`; Response reads `b2.text` | Valid. Success. Both branch reads met at the fixture (no 504). Join succeeded once. Receiver got one POST, body `{"lookup":"a"}`. Output `{"lookup": "b2"}`. Action counter 4, charged `a1#0`, `b1#0`, `b2#0`, `send#0` |
| S23a | `a1` meets then 500; `b1` meets then waits 5 s | Failed, `Tool provider returned HTTP 500`. `a1` running → failed; `b1` running → cancelled. No Join event. No delivery. The run was failed before the slow branch answered; it answered later |
| S23b | a write as branch `b1` | `/api/validate`: `b1: external writes belong after the Join.`; `POST /api/runs`: 422 with the same text; no run created; no request sent |
| S24 | `a1` lookup, `b1` hold; SIGKILL the worker when `a1`'s checkpoint is saved and `b1`'s read is outstanding; start a new worker | Checkpoints at the kill: `input`, `a1`. Success after 35.2 s (the real 30 s lease). `a1` read once, `b1` read twice, Join once, one delivery `{"lookup":"s24-a"}` |

Consequence: the behavior that Part 3 must prove is already present. Part 3 adds release-gate
evidence, not behavior. The new rows are expected to pass on the baseline, so their power to
detect a defect is shown with mutants (see the Part 3 design).
