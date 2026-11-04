# Independent TA02 implementation review

Commit: `f3afb25e6af8bbc24782390edb65321168f354a0`.
Review: `docs/reviews/ta02-implementation-review-2026-10-31.md`.

No product code or existing evidence was changed. Disposable SQLite databases and scenario
servers were used; the user's Relay servers and PostgreSQL container were not used.

| Artifact | Meaning |
|---|---|
| `focused-tests.txt` | 184 tests passed: accounting durability, S07, token budget/recovery, operations, approvals and Response source boundary |
| `full-suite-seed0.txt` | 1,974 backend tests passed at seed 0; process exit 0 |
| `additional-probes.json`, `probe_additional.py` | Four independent checks: lost post-commit fast-save acknowledgment, missing snapshot, and the two SQLAlchemy commit-hook behaviors |
| `sqlite-process-permitted/report.json` | Two successful real-process SIGKILL rows, before and after commit, with frozen source hashes |
| `preserved-inputs.json` | 77 original source, plan, test and evidence files retain their pre-review SHA256 hashes |
| `hosted-ci.json` | Hosted check status on the exact reviewed commit |

Reproduction commands from the repository root:

```bash
PYTHONHASHSEED=0 .venv/bin/python -m pytest -q backend/tests
PYTHONPATH=. .venv/bin/python evals/results/ta02-review-f3afb25/probe_additional.py
.venv/bin/python -m scripts.check_milestone_storage \
  --out /tmp/ta02-review-process \
  --checks sqlite-accounting-before-commit sqlite-accounting-after-commit
```

The process command needs permission to bind its localhost fixture servers. The initial
file-entry-point import error and sandbox-restricted module run are retained in
`sqlite-process.txt`, `sqlite-process-module.txt`, and `sqlite-process/report.json`.
They are execution setup failures, not product results. The successful permitted run is
kept separately and does not overwrite those failures.

The implementer's five-seed, isolation, 12-mutant and production results were inspected;
this review does not claim to have independently repeated those entire campaigns.
PostgreSQL process coverage is checked through the existing hosted CI job.

Logs and process histories can remain local. Commit the report, this README, probe,
compact JSON results, hash manifest, hosted status and permitted process report if retaining
the review in git. The first two process attempts are explicitly disclosed above.
