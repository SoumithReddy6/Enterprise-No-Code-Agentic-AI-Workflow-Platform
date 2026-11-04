# Token accounting production comparison: evidence

Reviewed application commit: `eb4417a6cc83e50b6562cee8869a06274de5a00e`.
Review date: 2026-10-25.

```sh
.venv/bin/python evals/results/token-accounting-review-2026-10-25/probe.py
```

Exit zero means all observations reproduce, including the defects. It does not
mean those behaviors meet the desired product contract. results.json records
nine groups, controls, measured counters and application-source hashes.

The reproducer uses temporary SQLite databases, actual application adapters and
Worker execution, and synthetic HTTP responses. No API key or live provider is
used. The accounting-outage case interrupts a worker coroutine at a committed
checkpoint barrier; it is not an OS-level SIGKILL experiment.

The existing focused suite passed 39 tests:

```sh
.venv/bin/python -m pytest backend/tests/test_token_budget.py backend/tests/test_observability.py backend/tests/test_openai.py backend/tests/test_claude.py backend/tests/test_s07_boundaries.py -q --junitxml=evals/results/token-accounting-review-2026-10-25/junit.xml
```

The detailed findings, official sources and scope distinctions are in
`docs/reviews/token-accounting-production-review-2026-10-25.md`.
