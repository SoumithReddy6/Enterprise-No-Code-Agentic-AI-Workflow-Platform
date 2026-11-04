# Decision provenance phases 1–3 — independent audit, 2026-10-09

Reviewed commit: `dad766ed3302441d2ba2503b7457672cd3cba131`.

**Recommendation: fix the incorrect labels and the shadow-mode regression before Phase 4 uses these records to permit or stop decisions.** The three phases provide useful machinery, but the completion claims are stronger than what the implementation currently guarantees.

## Evidence and scope

The findings below were reproduced against the current code. Model responses and the echo/Jira transports were deliberately controlled; these are tests of the product's behavior when it receives particular data, not claims that an unmodified live model naturally emitted every answer. Arithmetic probes invoke the actual evaluator. The size-limit probe executes the actual worker against a temporary SQLite database. No external writes or real workspace runs were created.

The test suite remains green:

- Backend suite: **1,195 passed**, exit 0, Python 3.14.6 on macOS, one fresh run with `PYTHONHASHSEED` unset.
- Focused provenance/Calculate/static tests: **140 passed**.
- Frontend unit tests: **75 passed**; typecheck, lint and production build exit 0.
- Existing Playwright suite: **11 passed**, on its isolated port 3317 with mocked APIs. The initial attempt could not bind that port inside the sandbox; the authorized retry passed.
- The new audit probes reproduce **12 findings in 17 cases**. A zero exit from the audit script means all expected defects reproduced; it is not an acceptance gate.

Artifacts:

- [Rerunnable probes](../../evals/results/decision-provenance-audit-2026-10-09/probe_phases.py)
- [Per-case results and reviewed commit](../../evals/results/decision-provenance-audit-2026-10-09/probe-results.json)
- [Frontend replay probe](../../evals/results/decision-provenance-audit-2026-10-09/probe_frontend.mjs)
- [Backend log](../../evals/results/decision-provenance-audit-2026-10-09/backend-suite.log)
- [Backend JUnit](../../evals/results/decision-provenance-audit-2026-10-09/backend-junit.xml)
- [Verification manifest, environment and source hashes](../../evals/results/decision-provenance-audit-2026-10-09/verification.json)

From the repository root, rerun the defect probes with:

```bash
.venv/bin/python evals/results/decision-provenance-audit-2026-10-09/probe_phases.py
node --experimental-strip-types evals/results/decision-provenance-audit-2026-10-09/probe_frontend.mjs
```

These write only this audit's results; they do not overwrite earlier auditors' evidence.

No application files or existing evidence were edited. Hosted CI, a fresh live-model campaign, and PostgreSQL fault/concurrency testing were not run in this audit.

Priority P1 here means the provenance boundary should not be used for enforcement until corrected. P2 identifies arithmetic, coverage or presentation defects that also need regressions.

## R01 — P1: a Prompt node makes its examples eligible quotation evidence

Location: [provenance.py:224](../../backend/app/provenance.py#L224), [provenance.py:237](../../backend/app/provenance.py#L237).

Reproduction:

1. Input is `Hello!`.
2. A Prompt node renders `Example: {"amount":0}\nRequest: Hello!`.
3. An extraction Agent returns `{"amount":0}`.

Without the Prompt, the amount is `guessed` and `would_review: true`. With the Prompt, it is `quoted` and `would_review: false`.

The code correctly excludes the Agent's system prompt, but searches the entire bound string from any trusted upstream port. Prompt output is marked `calculated` from its source input, so literals inserted by the template enter that trusted string. This contradicts the module's promise that author-written prompt/examples are never searched.

Required correction: distinguish eligible source-data spans from template/configuration text, or conservatively refuse to use an opaque Prompt result as quotation evidence. Add the actual Prompt → Agent boundary to the regression suite, not just the Agent's `system` setting.

## R02 — P1: tool results erase the origin of echoed guesses

Location: [provenance.py:152](../../backend/app/provenance.py#L152), [provenance.py:210](../../backend/app/provenance.py#L210).

Reproduction: Agent A returns `0` for `Hello!`; Python's default `print(input_text)` echoes it; Agent B extracts `{"amount":0}` from that result. The records become:

```text
Agent A: guessed → tool: source → Agent B amount: quoted
Condition: would_review = false
```

The probe controls the transport to perform the same echo as the configured Python code; it does not require Docker or send a network request. All provenance and routing run through the actual compiler.

Every tool output receives `source` regardless of its input. Deterministic transformations and echoed request bodies must retain their input origin. Independent external records need an explicit source rule; leaving the process is not itself evidence that a value is independent of a model.

## R03 — P1: dotted JSON keys overwrite the label for another value

Location: [provenance.py:139](../../backend/app/provenance.py#L139), [provenance.py:195](../../backend/app/provenance.py#L195).

Source input: `Order monitors for $1200.` Agent output:

```json
{"order":{"amount":0},"order.amount":1200}
```

Both JSON locations map to the metadata path `order.amount`. The second overwrites the first's label. The Condition actually reads nested `order.amount = 0`, but receives the `quoted` label belonging to the separate literal key containing 1200. It takes false on `> 1000` with `would_review: false`.

Required correction: represent JSON locations without separator collisions, or reject ambiguous keys before creating labels. The label must refer to exactly the value the Condition reads, independently of object insertion order.

## R04 — P1: unsupported numeric syntax is accepted as a different number

Location: [provenance.py:42](../../backend/app/provenance.py#L42), [provenance.py:52](../../backend/app/provenance.py#L52).

For source JSON `{"amount":1e3}`, Relay's exact JSON parser reads 1000. The quotation grammar instead finds 1. An Agent returning `{"amount":1}` is labelled `quoted` and needs no review; the correct 1000 is not found by this grammar.

This is not field-role ambiguity: it is a partial numeric-token match. Required correction: parse numeric leaves of structured input with the exact JSON parser, and reject an unsupported numeric token as a whole rather than accepting its prefix. Include exponent forms and token boundaries in the grammar matrix.

## R05 — P2: valid arithmetic fails because every operator is evaluated

Location: [calculate.py:260](../../backend/app/calculate.py#L260).

```python
return {'+': left + right, '-': left - right, '*': left * right}[node.value]
```

Python evaluates all three values before selecting the requested operator. An unused operation can therefore raise `Inexact`.

Reproduced cases:

- Two 30-digit numbers added together have a valid 31-digit sum, but their unused product causes an error.
- Subtracting the same 30-digit number from itself fails instead of returning zero.
- `(1 / 3) - (1 / 3)` fails instead of zero.
- `round((1 / 3) * 3, 2)` fails instead of 1.00 under the documented rounded-division rule.

Required correction: evaluate only the selected operator. Keep tests with ordinary fractional intermediates as well as precision-limit values.

## R06 — P2: unary minus and abs silently round exact values

Location: [calculate.py:247](../../backend/app/calculate.py#L247), [calculate.py:275](../../backend/app/calculate.py#L275).

Decimal unary minus and `abs()` use the current 50-digit context. A valid 61-digit input is silently changed instead of preserved or refused. A compiled `abs(a) == b` check returns true when `b` is the rounded value and the exact absolute value of `a` is different. It is labelled `calculated`, `passed: true`, `would_review: false`.

These deliberately large inputs are within the exact JSON magnitude range. This is an arithmetic contract failure, not an observed everyday-money or live-model failure.

Required correction: use exact sign operations, or enforce the stated precision ceiling with a named error; do not return a rounded value as an exact result. Preserve explicit rounding only for the documented division/round operations.

## R07 — P1: shadow metadata can fail an otherwise valid run

Location: [provenance.py:142](../../backend/app/provenance.py#L142), [provenance.py:259](../../backend/app/provenance.py#L259), [compiler.py:425](../../backend/app/compiler.py#L425).

A schema-valid structured answer with 64 ordinary string keys, each under 128 characters, is 7,980 bytes. Field-count/path-length checks admit its labels, but the resulting metadata exceeds 4 KB. The real worker then finishes with:

```text
status: failed
error: provenance record exceeds its size limit
```

The model call has already returned. The Agent receives no successful checkpoint and the Response never runs. This contradicts Phase 1's "no change in behaviour" requirement.

Required correction: build metadata within both its field and serialized-byte budgets; mark omitted coverage unverified/guessed. In observational mode, valid public outputs must survive metadata overflow. Continue refusing genuinely malformed restored metadata.

## R08 — P2: static warnings disagree with actual execution

Location: [provenance.py:294](../../backend/app/provenance.py#L294).

An Agent supplies free JSON text to `coalesce(1200, amount)`. Static analysis includes the unreachable `amount` branch and warns that the result is always a model guess and every run will pause. Runtime reads no field, returns 1200, labels it `calculated`, and records no review.

Required correction: distinguish possible dependence from guaranteed dependence, and account for statically decidable short circuits. An analysis that is uncertain should not say "always guessed" or "every run". Expand the static/runtime matrix to `coalesce` and `and`/`or` short circuits.

## R09 — P2: replay drops the decision explanation from the inspector

Location: [compiler.py:339](../../backend/app/compiler.py#L339), [workflow-editor.tsx:833](../../frontend/components/workflow-editor.tsx#L833).

A fresh Condition success event contains its guessed-value decision and review reason. A cached replay emits outputs and labels without `decision`. The editor selects the latest successful event, so `trustSummary` now returns only the branch label and loses the "a person would need to decide" explanation.

The probe confirms no model is called on replay. The actual frontend helper also reproduces the explanation disappearing.

Required correction: persist/replay the decision receipt with the checkpoint, or select the matching original decision for that cached execution. Never invent a review reason from the cached branch's label alone.

## R10 — P1: a traversal limit becomes false evidence of absence

Location: [provenance.py:79](../../backend/app/provenance.py#L79), [provenance.py:119](../../backend/app/provenance.py#L119), [provenance.py:123](../../backend/app/provenance.py#L123).

An Agent outputs a nonempty array nested 40 levels deep for input `Hello!`. `_leaves` stops at its depth limit without reporting incomplete traversal. The resulting empty label set becomes `absent`.

The Condition's `empty` operator correctly sees a present nonempty value and takes false, while its provenance says `absent` and `would_review: false`.

Required correction: traversal overflow must produce unverified/guessed coverage, never absence. Preserve the distinction between an actual null, an empty container, and a container whose leaves were not inspected.

## R11 — P2: attached nodes and loop items are outside the labelling boundary

Location: [execution_policy.py:244](../../backend/app/execution_policy.py#L244), [iteration.py:294](../../backend/app/iteration.py#L294), [provenance.py:230](../../backend/app/provenance.py#L230).

Two source Jira items contain amounts 1200 and 1350. A loop's extraction Agent returns those same amounts. Both successful body events have no `_provenance`; returned item entries contain no per-item label. The collection is simply `guessed` because its body's type is Agent.

The metadata decorator exists only in the compiler's graph-node handler. Attached callables bypass it. Therefore "every successful node output" and the plan's per-item label/replay contract are not implemented.

Required correction: use one shared labelling boundary for graph and attached execution, with the resolved input's origin available. Persist bounded per-item records and compute collection trust from actual item labels, not just the callable's type. Cover specialist Agents and Agent-attached tools too.

## R12 — P1: model-written answers are trusted through the grounding port

Location: [provenance.py:216](../../backend/app/provenance.py#L216), [agent_runtime.py:131](../../backend/app/agent_runtime.py#L131).

The `grounding` output is not solely a machine-created audit record: it includes the grounded contract's model-written `answer` and `reason`. Nevertheless every non-`text` model port gets `source`.

Reproduction:

1. Source passage lists laptops and contains no prices.
2. Controlled Agent A returns the contract answer `The amount is $0 [S1]`.
3. Agent B reads `AgentA.grounding` and extracts amount 0.

Agent A's text is `guessed`, but its grounding port is `source`. Agent B's 0 becomes `quoted`, and the Condition records no need for review.

This audit does not claim the citation contract proves entailment or that the live model emitted that claim; the defect is provenance promoting the same model-written answer when it appears inside another public port.

Required correction: label model-authored content within metadata as model-derived. A JSON wrapper or audit record must not change a value's origin. Add a second-Agent laundering regression using the grounding port, not only the text port.

## Known semantic limitation, kept separate

The reported `item: "order"` for `order $450` is already a live-observed limitation documented in the plan. A `requires: ["item"]` presence/quotation check cannot establish product identity. A catalogue or other independently authoritative field check is the strongest listed option where that data exists; otherwise a person must resolve unverified meaning for consequential automation.

The cases above are additional implementation defects, not a relabelling of that known limitation. The comparison of 16 earlier messages with 20 later messages also does not establish a matched improvement rate. Keep the claim as results on those two small measured sets; use one fixed, prelabelled suite for the eventual before/after acceptance comparison.

## Repair order and acceptance

1. Fix R07 so observational metadata cannot break valid runs.
2. Fix incorrect trust at the data boundaries: R01, R02, R03, R04, R10 and R12.
3. Fix Calculate's actual operator/sign behavior: R05 and R06.
4. Complete the shared attached/item boundary, replay receipts and static analysis: R11, R09 and R08.
5. Turn these audit cases into permanent regressions, then run the fixed, labelled measurement suite before enabling Phase 4.

Enforcement should consume records only after these cases fail safely. A green existing suite is compatible with all twelve findings; the missing cases need to enter normal discovery.
