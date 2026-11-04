# TA04 implementation audit evidence

Reviewed commit: `3deb964`. Original implementation, plan, tests and evidence unchanged.

- `probe_edges.py`: diagnostic counterexamples. Real compiler/Worker, provider and HTTP
  transports mocked, actual temporary SQLite approval records. Exit 0 means the asserted
  **defects are present**, not that they are fixed.
- `test_real_source_boundary.py`: independent red regression with real temporary KB
  management/search stores, ingestion, document removal and Worker.
- `test_replayed_attempt.py`: independent red graph and loop regressions with real approvals.
- `probe-results.json`: diagnostic results for Response sources and approval replay.
- `baseline-source.txt`: the source exposure on `51ba164` application code, establishing
  that R01 predates TA04. The new test-helper module was copied into the disposable export;
  baseline application files were unchanged.
- `red-regressions.txt`: three expected assertion failures on the reviewed commit.
- `focused-tests.txt`: the 126-test focused run on the unchanged reviewed implementation.
- `evidence-validation.json`: checks of the author's campaign/mutant evidence; not a rerun.
- `preserved-inputs.json`: original file hashes captured before the review.

From the repository root:

```bash
.venv/bin/python -I evals/results/ta04-fix-review-3deb964/probe_edges.py "$PWD"
.venv/bin/python -m pytest -q evals/results/ta04-fix-review-3deb964/test_real_source_boundary.py evals/results/ta04-fix-review-3deb964/test_replayed_attempt.py --tb=short --show-capture=no
```

The pytest regressions express the desired behavior and should turn green after correction.
They sit outside `backend/tests` and are not installed as product tests by this audit.
All accounts, connections, documents, stores, approvals and deliveries used by these probes
are temporary fixtures. Provider requests use synthetic responses; no real external write occurs.
