# TA02 design research and reproduction

Baseline: `f3e09238de9eb822713d50f2e5a3170ef25dc5f0`.
Proposed design: `docs/superpowers/specs/2026-10-31-token-accounting-durability-design.md`.
Status: design only, pending approval. No product code or dependency changes.

`reproduction.json` is a fresh run of only the original TA02 probe function from
`evals/results/token-accounting-review-2026-10-25/probe.py`. It uses temporary SQLite,
real Worker and provider adapter logic, synthetic provider HTTP responses and a commit
barrier. The original probe module's main routine is not invoked, so its saved evidence
is not rewritten. Exit 0 means the old defect still reproduces, not that TA02 is fixed.

Observed: allowance 15; two responses report 30 tokens; first checkpoint survives with
null accounting; resumed workflow succeeds; final accounting holds 15 while reporting
rows hold 30. This confirms the need for a shared durable commit boundary, without making
the reporting rows an authoritative replacement for accounting.

Reproduce from the repository root (prints output; does not overwrite saved evidence):

```sh
.venv/bin/python - <<'PY'
import asyncio, importlib.util, json
from pathlib import Path
path = Path('evals/results/token-accounting-review-2026-10-25/probe.py').resolve()
spec = importlib.util.spec_from_file_location('ta02_original_probe', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps(asyncio.run(module.persistence_failure_resume()), indent=2))
PY
```

Primary documentation was checked directly on 2026-10-31:

- AWS Prescriptive Guidance: transactional outbox and atomic state/event persistence.
- Stripe: usage event identifiers/idempotency and asynchronous summaries.
- Microsoft Azure Architecture Center: event sourcing, authoritative events and complexity.
- PostgreSQL 17 and SQLite: transaction atomicity and crash-recovery scope.
- Temporal: durable completed activity results and retry behavior.

Direct source links, what each establishes and what Relay adapts are in the design.
There is no recommendation to add those services or copy their architecture wholesale.
The user chose atomic result/accounting commits over a new physical-call ledger.
They also selected blocking further model calls for unverified older runs while keeping
saved results available. `builder-review.md` records the independent builder check and
the clarifications incorporated into the design. Approval of the complete design remains
the next step; no implementation plan or application code has been produced.
