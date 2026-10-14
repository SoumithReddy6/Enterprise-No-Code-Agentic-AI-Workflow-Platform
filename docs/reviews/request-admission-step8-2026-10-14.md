# Request admission measurement — 2026-10-14

**Release recommendation: do not accept the current measured router configuration.**
The final live suite admitted 9/20 valid requests (45%), against the frozen requirement
of at least 18/20 (90%). Safety boundaries passed, but rejecting most valid requests is
not a successful operating point. No prompt, model, expected label or threshold was
changed to make the observed result pass.

The implementation and evidence are ready for review/commit. Hosted CI for Step 8 is
pending the user's commit and push. Step 7 `e1e158e` passed all five hosted jobs in run
`38462767646`; that is not a hosted result for these new files.

## Measured configuration and results

The original 60 questions retain SHA256
`d4d8f48b1902a839c1be95814df6df4e42af081ac2b41f5e810323cce1974100`.
There are 20 proceed, 20 clarify, 10 out-of-scope and 10 prepared-reply cases, with 24
held out. Companion fact/action expectations were authored before measurement and
are independently hash-pinned. They do not read model outputs to establish truth.

The measured workflows use the contract-2 editor templates, an enabled local
`llama3.1:latest` (digest retained in the artifact), and explicit local test records.
Router examples are omitted uniformly, including development cases; no held-out input
is used as a router example. Nine explicitly configured normalized greeting aliases
cover the ten prepared-message cases. These are declared measurement settings, not a
claim about every user-authored workflow or the templates' default few-shot examples.
Both business branches have a real local HTTP receiver to make sends observable.
No customer's workspace, remote account or real business action was used.

| Final live measurement | Result |
|---|---:|
| Valid requests admitted | **9/20 (45%); fails the 90% gate** |
| Purchase / support / refund / yes-no admission | 3/5, 1/5, 3/5, 2/5 |
| All routing mismatches | 22/60 |
| False router acceptance of non-valid requests | 0 |
| Runs failed after bounded model-output handling | 2 |
| Valid requests paused for interpretation review | 8 |
| Prepared replies | 10/10, zero model calls |
| Automatic business decisions / deliveries | 0 / 0 |
| Unsafe automatic cases | 0 |
| Provider-reported tokens | 28,926 |
| End-to-end latency p50 / p95 | 4.46 s / 8.10 s |

The absence of unsafe automatic cases is a finite observation, not universal correctness.
Natural-language role mapping under the templates' source-field rules remains
conservative. Generic eligibility is also not interchangeable with active/paid/resolved
status. Review on a valid request is reported separately; it is not silently called an
unnecessary review or excused as correct admission. The router's own false rejections
and invalid outputs remain failures. The next work should improve the saved routing
brief/model operating point on development inputs, retaining the frozen held-out cases
and gate. The current evidence does not identify a stronger model as a proven fix.

## Boundary evidence

The separate deterministic profile forces `proceed` on all 50 non-greetings and supplies
invented extractions. All 50 reached verification and paused before a business branch
or POST; this is enforced by the gate rather than inferred from zero sends. Ten prepared
inputs bypass the model. Three independent structured-source controls (purchase,
support and refund) each deliver exactly once, with the expected body and destination.
They prove the receiver and successful execution path are reachable.

Manual actions are excluded from automatic safety only when the saved confirmation
matches this run, its consumed checkpoint, original input, interpretation and exact
payload digest. Actual human-confirmed sends, idempotent confirmation, lease release,
expiry/digest refusal and SIGKILL windows are covered separately by nine SQLite/
PostgreSQL process checks. Uncertain writes still refuse automatic resume.

The new content-free `run_admission_metrics` table is installed by Alembic revision
`0005_admission_metrics`. It stores stage, status, outcome and replay only, in the
fenced event transaction. The operator endpoint uses SQL over these columns; it does
not load or expose run/event content. Counts start with this revision, with no backfill.

CI now runs the deterministic profile using separate API/worker/model-fixture/receiver
processes. It cannot masquerade as a live-model acceptance result.

## Verification

| Check | Final result |
|---|---|
| Backend, seeds 0–4, JUnit retained | 1,637 pass + one optional Redis skip each; exit 0 |
| Dedicated real Redis profile | 55 pass, disposable container |
| Frontend unit / browser | 93 / 31 pass |
| Typecheck / lint / build | Pass |
| Production campaign with live models | 27/27, exit 0, no source changes |
| Knowledge service check | All five areas pass with real local embeddings |
| PostgreSQL conformance | Pass in disposable database/schema |
| PostgreSQL migrations, four workers, stale lease, SIGKILL loop | 4/4 |
| Typed review/crash probes, SQLite + PostgreSQL | 9/9 |
| Fresh product-path grounding suite | 53 questions, unchanged gate accepted |
| Grounding substring / abstention / false abstention / citations | .848 / .700 / .061 / .939 |
| Targeted mutations | 8/8 detected |
| Exact-file clean export | Backend and frontend checks pass |
| Dependency gate | Six high accepted braces findings; **not a clean audit**, expires 2026-12-04 |
| `git diff --check` | Clean |

Live and fault artifacts' source hashes match the final runtime/evaluation files.
The clean-export proof records the tested file bytes. Documentation completion after
that proof does not change executable files. Model sampling defaults were retained;
the earlier 5/20 and 9/20 measurements are preserved rather than treating reruns as a
search for a passing score. Only the final frozen-source run is the closing evidence.

## Independent review and rulings

One read-only independent reviewer reported four Important findings, no Critical/Minor
findings and no new runtime authority bypass. All four are fixed and tested RED→GREEN:

1. Delivery oracle now rejects duplicated sends, wrong branch destinations and wrong bodies.
2. Fault CI requires actual verification review outcomes and real positive-control results.
3. Manual exclusions require the matching run/checkpoint/input/payload, not an unrelated approval.
4. Provenance requires full execution dependency hashes, model identity, timestamps and environment.

Rulings and their limits:

- Original routing labels stay unchanged; fact/action truth is a pinned companion file.
  A mistaken manual label would affect the semantic oracle and must be reviewed as a
  new label correction, never silently fitted to model output.
- Current templates are measured with examples omitted and explicit greeting aliases;
  these settings are disclosed. Results cannot certify every configuration.
- Natural-language role inference remains reviewable; rejecting valid inputs still fails
  the admission gate. This may produce extra reviews, not an automatic bypass.
- Metrics have no historical backfill. Older runs will not appear in the new counters.
- Revision-0004 downgrade tests target 0004 explicitly. Dropping disposable 0005 metrics
  before refusing a destructive typed-review downgrade does not reinterpret approvals.
- Browser tests observe validation completion before reading captured requests or
  opening fields. No production timing deadline was relaxed; UI code is unchanged.
- The initial dependency invocation used the wrong cwd. The real frontend gate passes
  with the existing accepted residual; no advisory exception was added.
- PostgreSQL was absent on the default port. Disposable random-port PostgreSQL and
  Redis supplied the final profiles; no real deployment services/data were changed.
- The first reference fixture omitted its product field. It was corrected, with earlier
  failures and live results retained. No model/prompt/gate was tuned afterward.
- The reviewer set aside unfinished parent-owned checks; these now have the results
  above. Universal semantic accuracy, every possible nested crash composition, and
  real external integrations remain unproven, rather than implicitly certified.
- The first positive-control gate mutation survived. A direct gate assertion was added;
  the final eight mutants all fail their targeted assertions.
- Existing feature checkout/manual commit workflow is preserved. No automatic commit,
  push, stash/reset, live-workspace test run, or Relay restart was performed.

Initial failures remain recorded: three migration-test assertions in early seeds,
async browser selection/request recording, absent PostgreSQL, wrong-cwd audit,
incomplete reference fixture, and a campaign rejected because evaluator source changed
while it ran. The final checks replace none of those files.

## Artifacts and reproduction

Evidence: `evals/results/request-admission-step8-2026-10-14/report.json`.
Authoritative live artifact: `live-release/report.json` and `gate.json`.
Authoritative boundary artifact: `fault-current/report.json` and `gate.json`.
Other retained artifacts include model/source/policy/case provenance, process reports,
clean-export hashes, mutation results and earlier failed measurements. Raw local logs,
JUnit and duplicate row files need not be committed.

```bash
.venv/bin/python -m scripts.check_request_admission_eval evals/results/request-admission-step8-2026-10-14/live-release/report.json
# Expected exit 1: valid-request admission is 9/20, below 18/20.
.venv/bin/python -m scripts.eval_request_admission --profile fault --out /private/tmp/relay-admission-fault-new
.venv/bin/python evals/results/request-admission-step8-2026-10-14/verify_tree.py
.venv/bin/python evals/results/request-admission-step8-2026-10-14/verify_tree.py --mutations
```

Keep the safety guard. Do not lower the 90% admission gate or market this configuration
as released. Commit the measurement/tooling and check all five hosted jobs on that
exact pushed revision before further implementation.
