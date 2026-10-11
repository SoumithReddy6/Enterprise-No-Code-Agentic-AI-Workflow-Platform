# Task 1: provenance foundation repair — 2026-10-10

Base commit: `dad766ed3302441d2ba2503b7457672cd3cba131`. This report describes the
verified working tree; the user will commit and push it. Hosted CI has not run on
these changes.

**Recommendation:** Task 1 is ready to commit. This is a foundation repair, not a
release of request routing, Redis caching, extraction verification or enforcement.
Conditions still take their branches in shadow mode. A quotation establishes that a
value was written, not that it is suitable for a particular business field.

## Repairs

The [independent audit](decision-provenance-phases-1-3-audit-2026-10-09.md) reproduced
12 findings in 17 cases despite the original 1,195-test suite being green. Permanent
acceptance tests now live in `backend/tests/test_provenance_foundation.py`.

| Finding | Repair and acceptance |
|---|---|
| R01: prompt examples become evidence | Eligible input is explicit and invocation-scoped. Prompt literals cannot quote an invented extraction. |
| R02: an echo becomes an independent source | Only triggers and explicit record-read adapters establish source data. Opaque Python/HTTP results and write responses remain guessed. |
| R03: dotted field collision | Version-2 records use JSON Pointer paths. Literal dotted, slash and tilde keys cannot overwrite nested identities, regardless of insertion order. |
| R04: partial numeric token | Whole tokens and structured numeric leaves are parsed exactly. Exponents mean their actual value; malformed prefixes/signs do not quote trailing numbers. |
| R05: unused arithmetic throws | Only the selected operator executes. Large sums, subtraction and fractional intermediates no longer execute unrelated operations. |
| R06: sign operations round | Unary minus and absolute value preserve every digit. The documented 50-digit arithmetic ceiling and explicit division/round policy remain separate. |
| R07: metadata breaks a valid answer | Field maps respect both field-count and 4,096-byte budgets. Omitted coverage becomes unknown. A valid 64-field answer completes through the real worker. |
| R08: warnings claim impossible certainty | Static analysis prunes constant coalesce/boolean short circuits and distinguishes nullable calculations from always-guessed values. |
| R09: cached inspector loses its decision | The checkpoint retains the original decision receipt; cached success emits it again, with no model call. Frontend unit/browser checks cover the current format. |
| R10: traversal overflow looks absent | Incomplete traversal becomes guessed. Actual null and empty containers retain their established absence behavior. |
| R11: attached/item labels missing | Graph and attached execution use the same labelling function. Each loop item sees only its own eligible input; bounded item labels survive durable replay. Collection trust comes from actual item labels. |
| R12: grounding wrapper launders model text | Model-authored grounding is guessed and never quotation evidence for a second Agent. |

The fresh review also found variants not covered by the original probes. Each was
reproduced with a failing regression and fixed:

- Retrieval wrapper questions and filenames are excluded from passage evidence.
  The echoed question keeps its input label; only passage text establishes facts.
- Legacy dotted **parent** keys and malformed exponent signs cannot restore false trust.
- Malformed metadata inside a fully cached loop is refused before downstream nodes.
- Pre-fix version-1 derived labels cannot justify a newly executed decision. Public
  values and already-completed decision receipts are preserved; triggers and explicit
  independent reads retain their source status.
- Downgrading a valid near-limit legacy record stays bounded rather than breaking
  resume. The reviewer independently tested 6,119 valid legacy receipts, including
  37 that required field omission.

No retry, lease, invocation-identity, approval or accounting policy was changed.
There are no new dependencies, ports, migrations or services.

## Verification

Evidence directory: `evals/results/provenance-foundation-2026-10-10/`.

| Check | Result |
|---|---|
| Original audit probes, copied before edits | 17/17 defects reproduced; original artifacts untouched |
| Initial permanent regressions against the base | 23 failed, 3 passed; later review regressions also recorded red |
| Focused final backend checks | 271 passed |
| Full backend suite, hash seeds 0–4 | **1,245 passed in each process, exit 0** |
| Clean export: base plus exact repair files | **1,245 passed, exit 0** |
| Original probe shapes against the repair | **0/17 defects reproduced** |
| Deliberate broken variants in separate scratch exports | **14/14 caught by targeted regressions**, with compiling mutants and no import-error shortcuts |
| Frontend unit tests | **76 passed** |
| Frontend typecheck, lint and production build | All exit 0 |
| Isolated browser suite | **11 passed**, including the cached v2 trust inspector |
| Separately deployed API/worker production scenarios | **20/20 passed**; SQLite, authenticated HTTP and local receiver fixtures; no source changes during the run |
| Fresh reviewer | No remaining actionable Task 1 findings; independently ran 119 provenance/static/foundation tests and 49 approval/execution/iteration tests |
| Whitespace check | `git diff --check` clean |

The full backend processes emitted existing Starlette/SWIG deprecation warnings.
Logs and JUnit remain local under the repository's existing ignore policy; JSON
summaries, hashes and measurement/probe runners can be committed.

## Matched live measurement

The suite was frozen before product edits: **60 labelled cases, 24 held out**, across
purchase, support, refund and yes/no workflows. Its SHA256 is
`d4d8f48b1902a839c1be95814df6df4e42af081ac2b41f5e810323cce1974100`.
The release targets remain unchanged. The later admission features do not exist yet,
so this repair cannot claim those targets are met.

Thirty purchase cases apply to the existing purchase example. Both measurements
use that same workflow through the product compiler and local `llama3.1:latest`,
digest `46e0c10c039e019119339687c3c1757cc81b9da49709a3b3924863ba87ca666e`.
The baseline runs an exported original tree; the after measurement uses the repaired
tree. No external writes or real workspace runs are created.

- All 30 model answers and final routes are identical before/after.
- Both runs contain six model-output failures; these are retained, not filtered out.
- Both record two shadow review decisions. The controlled defect regressions, not
  this unchanged live sample, prove the incorrect-trust repairs.
- Suite, workflow, model and per-message hashes match. No product source changed
  during the after measurement.

See `live-baseline.json`, `live-after.json` and `live-comparison.json`. The result is
evidence of regression stability, not an improved admission rate or a promise that
the model will recognise invalid requests.

## Remaining work

Tasks 2–8 in the [implementation plan](../superpowers/plans/2026-10-10-request-routing-and-extraction-checks.md)
remain pending. In particular, greetings still have no prepared-reply cache, no LLM
router checks workflow purpose, and unverified decisions do not pause. Product
identity and yes/no meaning still require the planned verification rules or review.
Opaque tool outputs and old derived checkpoints deliberately lose trust rather than
claim independence that cannot be established.

This turn did not rerun the entire live-model workflow campaign, hosted CI or
PostgreSQL conformance. The live purchase measurement, full local suites, durable
regressions and 20 deployed scenarios are the evidence stated above.

The shared v2/context contract is handed over as one atomic, independently reviewed
commit command. A partially staged combination of graph and attached producers is
not the tree that was tested. No commit, push, deployment or server restart was made.
