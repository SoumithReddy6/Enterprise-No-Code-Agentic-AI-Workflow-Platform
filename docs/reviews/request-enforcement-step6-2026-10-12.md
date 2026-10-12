# Step 6 — runtime enforcement and durable review

The backend now enforces configured extraction checks before contract-2 business
decisions and external writes. Uncertain interpretations pause for a person; pending
reviews release their leases. The entry router cannot authorize a write. This is the
backend step; the contract-2 editor starter and typed-review controls are Step 7.

Base: `46e65e793e9f249995fb3e6aaf31b2ecd12fe266`. Changes are uncommitted for the
user's manual commit/push. Hosted CI for this implementation remains pending.

## Verified behavior

The shared pre-attempt boundary covers graph nodes, loop bodies and Agent-selected
tools/specialists. It validates the exact consumed value, runtime-issued receipts and
live tenant-scoped human confirmations before action reservation. Model text, a handler
output or a Redis entry cannot issue a grant. Unsafe refusals and typed pauses bypass
retry, error routing, item-error continuation and Agent observation handling.

Verification reviews show the original, interpretation, policy digest and field report.
Decision reviews show the original, resolved values, policy and proposed comparison;
approval requires an explicit JSON boolean. Accepting an interpretation or choosing a
branch retains its original provenance. A manual override does not make a failed check
pass. Checkpoints carry compact choice/id/digest records; exact payloads remain in the
approval table. Operator metrics group durable review rows by kind/status without
counting checkpoint replay as another person or review.

The `0004_approval_kind` revision backfills existing rows as write approvals, constrains
the allowed kinds and widens invocation IDs. Downgrade refuses typed records and IDs
that cannot fit the old field. SQLite migration/adoption tests and real PostgreSQL
upgrade/downgrade/reupgrade show model parity. The existing PostgreSQL CI job now also
runs the typed-review subprocess campaign; no sixth job was added.

## Independent review and corrections

One fresh-context, read-only reviewer independently ran 40 focused tests. It found two
Important issues, both fixed with reproducing tests before closure:

1. A cached ordinary Condition branch could be changed independently of its verified
   input and policy. Ordinary branches are now recomputed on fresh and restored paths;
   a recorded human choice follows a separate confirmation path.
2. A completed manual decision could expire while a later write used an independently
   verified data binding. Executed manual control reviews are now revalidated before
   later decisions/writes, independently of their payload lineage. The reviewer's
   natural one-second TTL reproduction now fails safely with zero receiver requests.

No Critical or Minor findings were reported. A further regression pins the fact that
manual choice and automatic check outcome are distinct. Four malformed array-schema
cases now return validation errors rather than crashing on attribute access.

The reviewer set aside, and this closure explicitly accepts as outside Step 6:

- Step 7 editor setup, presentation, typed-review controls and the contract-1 starter.
- Step 8 live model accuracy, held-out rates, screenshot semantics and universal claims.
- Unchanged Redis infrastructure/image security; its results confer no action authority.
- Deliberate mutation of runtime internals by trusted host Python code. Workflow-configured
  model outputs and sandboxed Python cannot access those objects.
- Saved author policy in template literals, tool settings and Calculate constants. It
  is distinct from model-derived facts; current input/value/policy still bind receipts.
- Broad frontend/browser and contract-1 regression in the independent review. Parent
  verification covered the backend, frontend units/typecheck and deployed scenarios.
- Hosted CI and deployed remote reconciliation, which need their own environment.
- Every conceivable nested crash composition. Recorded windows and focused Agent/loop/
  specialist cases are verified; this does not replace a future milestone audit.

## Evidence

| Check | Result |
|---|---|
| New review, boundary and migration tests | 54 pass |
| Full backend at hash seeds 0, 1 and 2 | 1,586 pass each; process exit 0 |
| Clean `git archive HEAD` plus exact Step 6 file overlay | 1,586 pass; imports proven to come from export |
| Mutations | 9/9 caught, including fencing, eligibility, restore, control expiry and schema constraint |
| SQLite/PostgreSQL process campaign | 8 SIGKILL windows plus PostgreSQL migration round trip, 9/9 |
| Existing deployed scenarios | 20/20 |
| Existing PostgreSQL conformance profile | Pass in a disposable schema with its documented test registration mode |
| Frontend | 79 unit tests and typecheck pass; no editor code changed |
| `git diff --check` | Clean |

The first mutation run revealed that a derivation test relied on a later write guard.
Its assertion now requires rejection before any downstream node starts, and catches
the removed restore guard itself. The first conformance invocation omitted the script's
CI-only open-registration setting; the product correctly refused its second account.
The documented-profile rerun passed. Neither failure was waived.

Reports are under
[`evals/results/request-enforcement-step6-2026-10-12`](../../evals/results/request-enforcement-step6-2026-10-12/report.json).
Use `process-final/report.json` and `production-final/report.json` for final-code
provenance. Earlier process reports are preliminary. Source hashes in both final
reports and the exact-file isolation manifest match the final implementation.

The local campaigns use synthetic Ollama responses and a local-only HTTP receiver.
They exercise the product's API, worker, approvals, storage and policy paths; they are
not evidence of a model's semantic accuracy. Rules must encode real suitability or
ask for human review. Agent tool inputs that change a verified task are refused;
authors needing generated writes should structure the draft and verify it before
the write node. Contract 1 behavior is retained until explicitly adopting contract 2.

No command was issued to restart or stop the real Relay services. Changes remain
uncommitted/unpushed, and the disposable Redis/PostgreSQL services were removed after
verification.
