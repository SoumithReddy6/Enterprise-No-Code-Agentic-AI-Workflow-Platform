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

## Follow-up run after the Task 5 review (`production-final/`)

Review `docs/reviews/a52-task5-review-2026-10-24.md` asked for two changes: S21b now also
submits the contract-1 Switch to `/api/runs` (422, no run created, no external call), and
the docs say a case named `approved` cannot bypass any *required* approval (writes follow
their own approval setting; S20 disables it, S21c requires it).

| Result | Value |
| --- | --- |
| Gate on `307d82d` plus these changes | **passed**: 24 of 24 rows; no source change during the run |
| `production-final/report.json` SHA-256 | `518257e244c00a8a8fa0e4db51b740237de8700826aa45660984b3af5a43c59b` |
| Mutant: run submission stops refusing contract 1 (Validate unchanged) | S21b fails: the run was accepted as `queued` |

The first report in `production/` is kept unchanged; the review verified its checksum.
