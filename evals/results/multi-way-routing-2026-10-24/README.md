# A5.2 multi-way routing: final evidence (2026-10-24)

Deployed scenario gate: `python -m scripts.check_production_scenarios --out evals/results/multi-way-routing-2026-10-24/production`,
run on `e4d3ee1` plus the Task 5 working-tree changes (scenario scripts and docs only).

| Result | Value |
| --- | --- |
| Gate | **passed**: 24 of 24 rows (S01–S21c and M01) |
| New scenarios | S20, S21a, S21b, S21c (second lab, model fixture, structured JSON input) |
| Source changed during the run | none |
| `production/report.json` SHA-256 | `f239b793cbb42971b3e59f2cd2bcbc84a3a80bd3e61e2f1297addd6e3f7e189f` |

Mutant checks (scratch copies, Switch lab only): removing the restore recomputation makes
S21a fail (the forged route ran to `success`); removing the validation rule makes S21b fail.

Other checks on the same tree:

- Request review scenario, SQLite and a throwaway PostgreSQL 17 container: 9 of 9 passed.
- Backend `PYTHONHASHSEED` 0–4: 1,828 passed each.

Logs, run histories and `provider-requests.json` stay local (ignored or untracked). The
scenarios use synthetic data and a model fixture; they claim nothing about how well a model
classifies prose.
