# TA03 diagnostic evidence and design inputs

Date: 2026-11-02. Checkout: `74ad48a630694d597dbef2ef80fbdf9eb1ca6804`.
No application code changed. All provider responses are synthetic and databases are
temporary. No live model, existing Relay run or workspace database was used.

## Confirmed cause

`execution_policy.usage_for` returns invocation-only usage. Normal outcomes and approval
pauses already use it. The compiler's cancellation and exceptional
`UnsafeExecutionError`/`UncertainWriteError` emissions instead read the checkpoint owner's
inclusive `run_state['usage'][node.id]`. SQL metrics sum those events with the already
reported child outcomes. The known cumulative budget remains correct.

There is a second constraint: nested cancellation passes through the attached invocation
handlers' `CONTROL_FLOW` clause without publishing that child's usage. Changing only the
compiler cancellation record to invocation-only usage therefore loses the child's known
tokens when it was still active at cancellation.

## Observations

| Sequence | Known/saved tokens | Current SQL metrics | Cancellation-only counterfactual |
|---|---:|---:|---:|
| Parent calls specialist; specialist succeeds; parent cancelled on next request | 30 | 45 | 30 |
| Parent calls specialist; specialist calls another specialist; cancelled before the last response | 30 | 30 | 15, incorrect |
| Cancel while the completed specialist's event acknowledgment is held | 30 | 45 | 30 |
| Unsafe finalization after three paid responses | 45 | 60 | Not changed/tested |
| Uncertain-write finalization error after three paid responses | 45 | 60 | Not changed/tested |
| Ordinary nested success control | 45 | 45 | Not changed/tested |

The last two failure cases deliberately inject the typed exception at
`request_safety.finalize`. They prove the compiler's exceptional usage emitters also
overlap; they do not claim an actual write was sent or an authorization incident occurred.

The cancellation-only counterfactual is compiled in a copied Python namespace and used
only in the diagnostic process. No compiler file is patched. It is not a proposed product
fix or a passing product gate.

## Artifacts and checks

- `reproduction.json`: original TA03 reproduction plus success control, calling only the
  relevant functions in the old probe; the old script's main is never run.
- `probe_nested_boundaries.py`, `nested-boundaries.json`: eight diagnostic rows across the
  cases above. The script reports current defective behavior and a deliberately incomplete
  counterfactual; exit 0 does not mean TA03 is fixed.
- `baseline-tests.txt`: 75 existing tests pass (execution policy, operator metrics, token
  budget, operations, accounting durability). They do not cover the reproduced gap.
- `preserved-inputs.json`: original application sources and original auditor evidence are
  unchanged after the probes.

Commands from the repository root:

```bash
PYTHONPATH=. .venv/bin/python evals/results/ta03-design-2026-11-02/probe_nested_boundaries.py
.venv/bin/python -m pytest -q backend/tests/test_execution_policy.py \
  backend/tests/test_operator_metrics.py backend/tests/test_token_budget.py \
  backend/tests/test_operations.py backend/tests/test_accounting_durability.py
```

## Primary references inspected

- [Genkit's OpenTelemetry conventions](https://genkit.dev/docs/observability/otel-genai-semantic-conventions/):
  model operations have their own spans and response-reported usage. This supplies a
  concrete framework example of attributing usage at the operation that incurred it.
- [LangSmith cost tracking](https://docs.langchain.com/langsmith/cost-tracking):
  child usage is presented alongside aggregated parent and trace totals. A displayed
  parent rollup is therefore distinct from another independent unit of consumption.
- [OpenTelemetry's GenAI observability example](https://opentelemetry.io/blog/2026/genai-observability/):
  agent spans contain model/tool spans; usage attributes describe individual model calls.
  The conventions are still evolving; their exact names are not a reason to install or
  replace Relay's telemetry stack for this fix.

Applying the non-overlap principle to Relay's existing invocation counters is a proposed
adaptation, not a claim that these systems implement Relay's cancellation or commit logic.
The user has now selected new-run behavior only. Historical metrics, backfill and deletion
are explicitly excluded. The focused draft is
`docs/superpowers/specs/2026-11-02-token-outcome-attribution-design.md` records the November 3
approval, lease fence and loop progress clarifications, and tests-first P2 review sequence.
