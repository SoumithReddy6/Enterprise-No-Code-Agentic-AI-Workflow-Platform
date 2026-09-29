# S07 fault-injection audit — 2026-09-27

Verdict: S07 is not closed. Six targeted safety assertions fail on the current working tree, even though all 590 existing backend tests pass. No production code was changed during this audit.

Base commit: `ea09756fa003a3235d1204a302d39133dfd51177`. The checkout also contains uncommitted changes; this is a working-tree audit, not certification of that commit alone.

## Evidence and scope

Reproductions: `evals/results/s07-complete-probe-2026-09-27/test_boundaries.py`. JUnit outputs: `probes.xml` and `backend.xml` in the same directory. Assertions describe the required invariant and deliberately remain red.

Five probes use the real Store, SQLite transactions, Worker, lease acquisition, checkpoint restoration and resume/approval APIs. Model outputs and external transports are controlled test doubles; no real customer messages were sent. The lease-propagation probe uses the compiler with an injected reservation failure. These tests do not establish PostgreSQL concurrency behavior or prove the absence of other vulnerabilities.

## P1 — R01: a settled agent write is sent again after resume

Locations: `backend/app/agent_runtime.py:292`, `backend/app/worker.py:105`.

Probe: `test_agent_settled_write_keeps_identity_after_crash`.

The agent sends an HTTP POST. Settlement durably records `agent:1:calc`, then interruption occurs before the tool success event and parent checkpoint. The test confirms the durable result exists. Resume restores the action counter, but without an approval frame the agent replans. Its next invocation becomes `agent:2:calc`; the lookup cannot find the old outcome under this new identity. The same payload is delivered twice.

Observed: `duplicate deliveries: ['same reply', 'same reply']`.

This is outside the deliberately uncertain external-response/commit window: the first result is already durable. Atomic settlement alone is insufficient when replay changes its lookup key. Persist the agent's action identity and continuation before execution, and restore that continuation/result before another planning step. Do not deduplicate by payload alone: intentional repeated writes must remain possible.

## P2 — R02: a reexecuted read bypasses the action ceiling

Location: `backend/app/execution_policy.py:116`.

Probe: `test_reexecuted_read_is_not_free_on_resume`.

A standalone tool completes under budget 1, then interruption prevents its checkpoint. Accounting has counter 1 and the charged attempt key. Resume skips the debit for that same key but executes the tool again because its output is not cached.

Observed: `2 real executions under action budget 1`.

An already charged key proves a reservation, not that this invocation will replay a saved result. Distinguish durable result replay from a new external execution. Either restore a persisted read result or assign and debit a new execution attempt.

## P2 — R03: the agent catches LeaseLost after the shared policy rethrows it

Location: `backend/app/agent_runtime.py:348` (also inspect the write conversion at line 308).

Probe: `test_agent_does_not_swallow_reservation_lease_loss`.

An injected reservation callback raises `LeaseLost`. The shared policy propagates it, but the enclosing agent catches it as an ordinary tool failure, adds an observation, and proceeds to another model step.

Observed: expected `LeaseLost`, but no exception escaped.

The tool itself was not called. This proves broken control-flow propagation, not an actual unauthorized network call after a stolen production lease; other worker fences may stop that. Make lease loss escape every enclosing dispatcher, including both agent exception handlers, without retry, recovery or conversion to an uncertain-write error.

## P2 — R04: approval resume counts previously reported tokens again

Locations: `backend/app/compiler.py:277`, `backend/app/execution_policy.py:64`, `backend/app/operator_metrics.py:25`.

Probe: `test_approval_resume_usage_not_counted_twice`.

Planning uses 10 tokens, then the agent pauses for approval and reports them. After approval, a second planning call uses another 10 tokens. The resumed success event reports cumulative usage of 20, so additive journal/metrics accounting totals 30 for 20 provider tokens.

Observed: `30 journal tokens for 20 provider tokens`.

The earlier removal of retry-event usage does not address cross-resume cumulative outcomes. Persist a reported-spend watermark and emit deltas, or use a durable unique spend ledger that metrics aggregate exactly once.

## P2 — R05: token persistence is not actually best-effort

Location: `backend/app/registry.py:95`.

Probe: `test_token_persistence_failure_preserves_paid_response`.

The provider returns a paid answer and 10 tokens. The probe then fails the first accounting write, while allowing later writes. `record_spend` propagates the error and the node loses its successful response.

Observed: provider called once; run status `failed`, error `Node execution failed.`

The docstring and stated design promise best-effort persistence specifically to avoid this behavior, but there is no exception handling. The test intentionally fails only after the provider returns; an earlier version failed during input bookkeeping and was corrected before accepting this finding. Define a durable response/spend policy and report persistence degradation explicitly. Simply swallowing every error would conceal accounting loss; do not swallow cancellation or lease loss.

## P2 — R06: an approval frame restores an obsolete budget ceiling

Locations: `backend/app/agent_runtime.py:223`, `backend/app/compiler.py:185`.

Probe: `test_approval_frame_does_not_restore_old_budget_ceiling`.

A run pauses under action limit 3 after reserving its first write. The operator lowers the limit to 1 before approval/resume. The compiler correctly clamps remaining budget, then the agent overwrites it with `frame['budget']`. After the already-reserved action executes, a second action is reserved and another approval is created.

Observed: counter 2 despite the current ceiling of 1.

This demonstrates inconsistent limit enforcement, not an unapproved second delivery. Accounting must remain the sole source of budget state; an agent continuation should not overwrite the compiler's restored and clamped budget.

## Verification

- Existing backend suite: **590 passed**, four dependency deprecation warnings; JUnit retained.
- New targeted probes: **6 failed**, each at its stated safety assertion; JUnit retained.
- `git diff --check`: passed.
- The live-model `scripts/check_workflows.py` attempt stopped during corpus indexing, before workflow execution. No fresh 15/15 claim is made from that attempt.
- Isolated production campaign: **16/18 passed**; only known S08 (recovery binding validation) and S09 (run truncation metadata) failed. `production/report.json` retains the results. The initial sandbox attempt could not bind localhost; the authorized rerun completed with isolated local services.

Reproduce the targeted checks:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  evals/results/s07-complete-probe-2026-09-27/test_boundaries.py \
  --tb=short --show-capture=no
```

## Recommended sequence

Fix R01 first: it violates the external-write safety invariant. Then resolve invocation/reservation replay semantics (R02), lease propagation (R03), and single-authority budget restoration (R06). Fix spend persistence and reporting together (R05/R04), with worker-level approval and crash tests. Keep S08/S09 separate; their known failures do not explain these six results.
