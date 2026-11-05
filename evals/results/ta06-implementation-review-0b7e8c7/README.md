# TA06 independent implementation review

Reviewed commit: `0b7e8c7e09fd660a2d8cad106a48e2e2097910ee`.
Report: `docs/reviews/ta06-implementation-review-2026-11-05.md`.

## Independent checks

| Check | Result |
|---|---|
| `test_usage_status`, providers, token budget, usage attribution, accounting durability, operator metrics, schema migrations | 126 passed |
| Observability, operations, execution policy, token-budget recovery | 84 passed |
| Full frontend unit suite | 115 passed |
| `probe_edges.py` | 15 passed, 3 failed, across 18 collected rows |
| Hosted CI, run `42110749689`, exact reviewed SHA | All five jobs passed |

The hosted backend suite ran twice: 2,000 passed and 2 skipped each time (2,002 collected).
The native-macos job covers its platform-specific tests. Browser-layout reports 49 passed;
frontend reports 115 unit tests plus successful typecheck, lint and build. PostgreSQL
conformance passed its migration round trip/model parity and process checks at revision
`0006_usage_status`.

The three failures reproduce two findings: the real journal omits `usage_status`, and
two locally rejected URL forms create phantom unanswered calls. Three of the passing
rows are isolated controls that apply the smallest correction with pytest monkeypatch;
the other 12 pass against unchanged application code. See `probe-results.json` for each
row and `probe-output.txt` for the pytest summary.

Run the independent probes:

```bash
.venv/bin/python -m pytest evals/results/ta06-implementation-review-0b7e8c7/probe_edges.py --import-mode=importlib -q --tb=short --show-capture=no
```

Expected on the reviewed commit: exit 1, with exactly the three current-code rows red.
The patched controls alter no repository source and send no real model requests.

## Preservation and provenance

`preserved-inputs.json` records SHA-256 hashes of 180 original application, frontend,
migration, design and builder-evidence files before the independent probes. Generated
Python caches are excluded. `checks.json` records the final comparison and other gate
results. The original builder/design-review evidence is not rerun or overwritten.

Builder evidence inspected: all eight planned mutant kills, five 2,002-test local suite
runs, the 11-file isolation proof, and the 24-row deployed report. These are distinguished
from the auditor's independent checks above. `hosted-ci.json` records the exact-SHA hosted
job status and verification time; the run URL is included there.
