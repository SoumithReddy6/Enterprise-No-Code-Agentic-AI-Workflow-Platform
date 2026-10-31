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
the clarifications incorporated into the initial design.

## Design review revision 2

The user approved the design with two required corrections: pause must atomically commit
its supplied frozen snapshot, and no writer may lower `tokens_spent`. Both recommendations
were adopted too: refuse an unverified legacy run at claim before an unstarted approved
write, and reuse the existing process/PostgreSQL harnesses with a test budget.

`review-boundaries.json` checks the current pause API's database-copy behavior and re-runs
the existing controlled out-of-order snapshot test (30 stored, then overwritten to 10).
The pause check is a storage-contract probe with synthetic frame data, not a live Agent
measurement. The concurrency check is a controlled writer ordering, not a claim that an
accepted parallel graph ran. Original evidence remains unchanged.

The revised design is followed by
`docs/superpowers/plans/2026-10-31-token-accounting-durability.md`: three coordinated tasks,
at most 24 new collected pytest cases, four rows in reused harnesses, and twelve targeted
mutants. The plan is written but has not been executed. Application code is unchanged.
