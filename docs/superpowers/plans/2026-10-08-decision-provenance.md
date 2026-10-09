# Decision provenance: no automatic decision on a guessed value

> Proposed plan for review. No product code has started. It replaces the approach in
> [new-workflow input validation](2026-10-08-new-workflow-input-validation.md), which stays
> in the repository as the record of what was considered.
>
> Baseline: `477e698` plus later commits on `codex/retrieval-answerability-safety`.

## 1. The problem, stated for the platform

An Agent can return a value that looks valid but that nothing in its input supports. The
purchase example approved "How do AI workflows work?" because the Agent returned
`{"amount": 0}`. The same failure applies to any workflow that branches or acts on
model output: a ticket priority, a refund decision, a yes/no eligibility flag.

The platform cannot know business rules, such as what a total, a priority or an eligible
refund is. It can know **where a value came from**. This plan gives every value that
reaches an automatic decision a label saying where it came from, and applies one rule to
those labels across the whole platform.

## 2. The rule

> An automatic decision may use a value only if it is **source**, **quoted** or
> **calculated**, and only if every fact the decision **requires** is present and equally
> trusted. Otherwise the run pauses for a person to decide, unless the workflow author has
> explicitly accepted guessed values for that decision.

Three layers answer three different questions:

| Question | Answered by |
| --- | --- |
| Where did this value come from? | Labels (section 3) |
| Is the arithmetic right? | The Calculate node: code computes, never a model (section 5) |
| Is the request complete enough to decide? | The decision's `requires` list (section 6) |

"Automatic decision" means a Condition today, and the A5.2 switch when it lands.
Write approvals already keep a person in the loop for side effects; this rule covers the
branch choice that comes before them.

## 3. Labels

Labels are assigned by deterministic code, never by a model.

| Label | Meaning | Assigned when |
| --- | --- | --- |
| `source` | Data that entered the workflow from outside a model | Trigger messages, tool and HTTP results, Jira items, retrieved passages |
| `quoted` | A model output value that appears in the model's own **trusted** input | The value is found in a `source`, `quoted` or `calculated` part of that Agent's input (rules in section 4) |
| `calculated` | Produced by deterministic code from trusted values only | Output of a Calculate node (section 5) whose every operand is trusted |
| `guessed` | Everything else | Model values that could not be found, free model text, anything computed from a guessed value |

**Trusted** means `source`, `quoted` or `calculated`. Propagation is conservative: a
deterministic node's output is as trusted as its *least* trusted input. Copying a guessed
value from one Agent into another can never make it quoted, because quoting only counts
against trusted parts of the input. That closes the "laundering" path, where one Agent
repeats another Agent's invention.

`null` is not a value to verify. It means "not found" and is always allowed, so a workflow
can branch on its absence with the `empty` operator. This keeps "I could not find it" an
honest answer instead of pushing the model to invent something.

Free model text (an Agent's whole `text` output without an output schema) is `guessed`.
Per-field labels exist only for structured output, which is the extraction and
classification roles or an `output_schema`.

## 4. How "quoted" is checked

Pure functions in a new `backend/app/provenance.py`. No model, no author-supplied regex.

| Value type | Quoted when |
| --- | --- |
| Number | Its exact decimal value equals a number written in the trusted input. Recognised forms: digits with optional `,` thousands groups (each group 3 digits), optional decimals, optional sign; optional currency symbol (`$ € £ ¥ ₹`) or ISO code (`USD`, `EUR`, …) and optional `%`; and the number words zero to twenty plus `a dozen`. Comparison uses `exact_json` decimals, never floats. |
| String | It appears in the trusted input after Unicode case-folding and whitespace normalisation, on word boundaries, and is at least 2 characters long. |
| Boolean | Only when copied from a structured `source`: an input JSON leaf with the same key and value. Text such as "yes" never quotes a boolean. |
| Object or array | Each leaf is labelled separately. The container is trusted only if every leaf is. |

**Limit, stated plainly:** `quoted` proves a value was **written** in the input, not that it
means what the field name says. A model could return a unit price as a total. That was not
observed in 24 measured runs (section 12), but it is possible. The platform does not try
to understand roles. Authors protect consequential decisions with a cross-check (section
5's `check` mode), and `requires` covers missing facts. The evaluation in Phase 5 keeps
measuring it.

## 5. Calculate node (deterministic, generic)

A new `calculate` node, category Transform, so derived values never come from a model.

- **Input:** one port, `value`, bound to a JSON object, usually an extraction Agent's `text`.
- **Config:** `expression`, plus `mode` of `compute` or `check`.
- **Expression language:** field paths (`order.quantity`), decimal literals, `+ - * /`,
  parentheses, `min`, `max`, `abs`, `round(x, places)`, comparisons
  `== != < <= > >=`, and `and`, `or`, `not`. It's a hand-written parser with no `eval`,
  at most 500 characters and nesting depth 20. Arithmetic uses exact decimals at bounded
  precision. Division by zero and missing or `null` fields are named errors.
- **`compute`** outputs `{"result": <exact number>}`, labelled `calculated` if every
  referenced field is trusted, otherwise `guessed`.
- **`check`** evaluates a comparison. False means the values disagree, which is
  treated like a guessed decision: the run pauses for review with the reason. For example,
  `total == quantity * unit_price`.

Business logic stays in the author's workflow. For the purchase example, the author
extracts `quantity`, `unit_price` and optional `total`, uses a Calculate `check` when a
total is given, and a Calculate `compute` for the total otherwise. The platform knows
nothing about purchases.

## 6. Decision prerequisites: `requires`

A trusted deciding value is not enough on its own. "buy $450" has a quoted amount, so it
would be auto-approved, but nothing says what is being bought. The platform cannot know
what a decision needs, so the author declares it:

- A Condition (and the A5.2 switch) gets an optional `requires` list of field paths, for
  example `["item"]`. These are fields of the same structured value the decision reads.
- Before deciding, each required field must be present (not `null`) and trusted. If one is
  missing, the reason is "item is missing". If one is guessed, it is "item could not be
  confirmed in the input". Either way, the run goes to decision review.
- Strings get the same quote check as numbers, so a model cannot fill a gap by inventing
  an item that is not in the message.
- An empty `requires` list means the behaviour is unchanged. The editor suggests the other
  fields of the same structured output as candidates, without adding them automatically.

## 7. Decision review (the "ask a person" path)

When a Condition's value is guessed, or a Calculate `check` fails, the run does not choose
a branch and does not fail. It pauses, and a person picks the branch.

- **Reuse the durable approval machinery:** fenced pause, lease release, digest, expiry,
  rate limit and resume. An approval record gets `kind: decision`. Its payload shows the
  node, field, value, label, the reason, and the trusted input excerpt the value was
  checked against (bounded to 2,000 characters).
- **`decide` accepts `choice: true | false`** for decision reviews. Choosing resumes
  the run down that branch. Rejection cancels the run, as for writes.
- **Recorded as a human decision:** the branch's event records `decided_by: person`, so
  history shows which branches a model chose and which a person chose.
- **Per-Condition override:** `accept_guessed: true` keeps today's behaviour. The editor
  shows it as an explicit risk acceptance with a warning, never as a default.

## 8. Rollout (nothing breaks on release day)

| Stage | Behaviour | Applies to |
| --- | --- | --- |
| Labels and shadow | Labels are computed and shown. A Condition on a guessed value logs a "would pause for review" event and metric, but behaves as today | All workflows |
| Warn | Validation shows located warnings (section 9). Nothing is blocked | All workflows |
| Enforce | Guessed values pause for review | Workflows with `"contract": 2` only: new workflows by default, older ones when their owner upgrades them |
| Upgrade assistant | The editor lists exactly which Conditions would start pausing, then sets `contract: 2` on confirmation | Existing workflows, one at a time, by their owner |

Workflows without `contract` behave exactly as today forever, unless upgraded. Demo mode
keeps working: demo Agents produce guessed values, which pause for review in contract-2
workflows. That's correct behaviour and a useful demonstration of the review path.

A server setting `RELAY_DECISION_PROVENANCE=off|shadow|enforce` (default `enforce`)
lets an operator roll back enforcement without a code change.

## 9. Editor and validation

- **Static analysis (compiler).** For each Condition, trace its value binding back to its
  source and report the label it can reach:
  - "always source or calculated": no message;
  - "checked at run time" (a structured Agent field): an info note;
  - "always guessed" (free model text, a guessed computation): a warning.

  Messages are located on the node through `locate` and the issues API added earlier.
  Example: *"Over $1,000? decides on `amount` from Extract amount. If it is not found in
  the input, the run will pause for a person to decide. Quote it, calculate it, or accept
  guessed values."*
- **Inspector:** each structured Agent field shows its label after a run. A Condition shows
  the label of the value it used, and whether a person decided.
- **Run view:** "Waiting for a decision" uses the existing approval panel, with branch
  buttons instead of approve/reject.

## 10. Storage and resume

- Labels travel as reserved runtime metadata `_provenance` on node outputs, next to
  `_truncation`. It is never a bindable port, strictly validated, and at most 4 KB with
  64 paths per node.
- It is stored in the existing checkpoint inside the same fenced transaction as the
  outputs, so resume restores labels rather than recomputing them. No new table or column.
- A checkpoint without `_provenance` (made before this change) is treated as `guessed` in
  contract-2 workflows and ignored in others.
- Loops: the results of a for-each whose body is an Agent are labelled as a collection.
  The collection is trusted only if every item's decision fields are trusted. Phase 1
  stores one label per item in the existing item row, at most 64 bytes, within the
  existing 2 MB ceiling.

## 11. Phases

Each phase is independently shippable, reviewed and green on hosted CI before the next.

**Phase 1: labels and shadow mode, no behaviour change.** *Implemented 2026-10-08:
`backend/app/provenance.py`, wired in `compiler.py`; tests in `backend/tests/test_provenance.py`.*
- `provenance.py`: number and string grammars, boolean and structured matching, propagation.
- Labels for every node type: trigger, tools, retrieval, prompt, Agent (structured and
  free), condition, response, loops.
- `_provenance` metadata: contract, bounds and checkpoint persistence.
- Shadow events and a metric: `decision.would_review`, by workflow and node, with the
  reason (guessed value, missing or guessed required fact, failed check).
- *Tests:* a grammar matrix including exact-decimal edges, `$1,350` versus `1350.0`,
  number words, the string word-boundary rule, and no float use. Also a laundering test
  where Agent A invents and Agent B copies, which must stay guessed. Plus propagation per
  node type, resume restoring labels, and bounds.

**Phase 2: Calculate node.** *Implemented 2026-10-09: `backend/app/calculate.py`; tests in
`backend/tests/test_calculate.py`. The purchase example now extracts parts and computes the
total in code. Two details differ from the sketch above: `null` propagates through
arithmetic (a missing part gives a null result, not a guess), and `coalesce` takes the
first non-null argument. Only the fields a calculation actually reads count toward its
label.*
- Parser, exact evaluator, `compute` and `check` modes, label propagation, editor form.
- *Tests:* grammar fuzzing (no `eval` reachable), division by zero, missing and `null`
  fields, precision, depth and length bounds, and labels on every operand combination.

**Phase 3: warnings and editor labels**
- Static provenance analysis in the compiler, located warnings, inspector and run-view
  labels.
- *Tests:* the backend analysis matrix, plus a browser test showing the warning on the
  right node and labels after a run.

**Phase 4: enforcement and decision review**
- The `contract: 2` field, the operator setting, `accept_guessed`, Condition `requires`,
  decision approvals with `choice`, the run-view panel, and the upgrade assistant.
- *Tests:*
  - deployed scenarios: a guessed value pauses with no branch and no write; a person
    chooses true and then false; resume after a worker crash during the pause;
    `accept_guessed` behaves as today;
  - contract-1 workflows show no change in behaviour;
  - the operator setting `off` disables enforcement;
  - digest and expiry rules match write approvals.

**Phase 5: measurement and documentation**
- An invalid-request suite of at least 40 cases with expected outcomes written before any
  run. It spans purchases, tickets, refunds and yes/no questions; greetings and
  off-topic questions; messages with irrelevant numbers ("I have 3 meetings"); unit price
  plus total; and missing information.
- Report **false automatic decisions** (target: 0), unnecessary reviews (measured and
  reported, not hidden) and role confusion.
- The live-model campaign and the 53-question grounding gate are rerun unchanged. This
  plan doesn't touch the grounded-answer path.
- Documentation: the rule, the labels, the quote grammar, Calculate, decision review,
  rollout, and the limits in section 4.

## 12. Evidence behind this plan (measured 2026-10-08, local `llama3.1:latest`, temperature 0)

| Observation | Result | Consequence for the plan |
| --- | --- | --- |
| A non-order ("How do AI workflows work?") with a required numeric `amount` | The model invented `{"amount": 0}`; the run auto-approved | Labels: `0` is not in the input, so it is guessed |
| A greeting with the same schema | `amount` was omitted on both the answer and its repair; the run failed safely | Separate repair-prompt item (section 14) |
| A nullable `amount` and an explicit null instruction, over 10 messages × 3 runs | All routed correctly | Allow `null` as honest absence |
| A computed total, over 16 messages × 3 runs | **4 of 16 wrong, every run.** 7 × $142.86 gave 999.72 instead of 1,000.02 and **auto-approved over the limit**; 9 × $111.11 gave 9,999.99 | Calculate node: models must not do arithmetic |
| Unit price taken as the total, over 8 phrasings × 3 runs | **Not observed** | Kept as a documented possibility, not the main risk |
| A missing item ("buy $450", "spend $1,200") with a nullable `item` | `item: null` in all 3 runs; items were found when present | `requires` (section 6) |

**Phase 1 shadow measurement** (same model, the purchase example, 11 messages): both wrong totals
(999.72 for 7 × $142.86, which is currently auto-approved, and 9,999.99 for 9 × $111.11) were labelled
`guessed` and would have gone to a person. Written totals ($1,350, $1,200, $320, $300) were `quoted` and
would decide automatically. Non-orders were `absent`. Three **correct** totals the model computed (1,350,
1,050 and 300) were also `guessed`: unnecessary reviews, which the Phase 2 Calculate node removes by
computing them in code.

**Phase 2 measurement** (same model, the rebuilt example, 20 messages): **0 wrong totals,
0 wrong routes and 0 decisions needing a person**. Every computed total, including the four
the model had miscalculated, is exact and labelled `calculated`. The live run also found a
grammar defect: a trailing comma, as in "$300, quantity 4", hid the number. It is fixed,
with that case as a unit test.

**Open finding: quoted is not the right role, for text.** On vague requests the model puts
a verb or fragment in `item`, despite instructions: "order $450" gave `item: "order"`, and
"purchase 3 for $100 each" gave `item: "3 for $100 each"`. Both are written in the message,
so both are `quoted`, and "order $450" was auto-approved. `requires: ["item"]` does not
catch this, because the item is present and quoted. This is the limit stated in section 4,
now observed. It needs its own decision before Phase 4; see section 13.

These are small, author-defined probes on one model, not a calibration set. Phase 5 repeats
them as a fixed suite, with expected outcomes written before running.

## 13. Open: checking meaning, not just presence

Candidates for the item-role finding, none chosen yet:

1. **Check against authoritative data.** A field is trusted only if it matches a record from
   a `source`, such as a product catalogue fetched by a tool, so `"order"` is not an item.
   This is generic (an `in` check against a source list) and strongest, but needs the data.
2. **Policy: free text never authorises on its own.** An automatic decision may rely on
   quoted numbers, but any required text field also needs a source match (option 1) or a
   person.
3. **Prompt guidance only.** Already tried; it reduced but did not remove the problem.

## 14. What this plan deliberately does not do

- **No extra model call per Agent** to judge input validity. That may come later as an
  optional per-Agent setting, never as the safety boundary.
- **No mandatory per-Agent policy, and no breaking change to saved workflows.**
- **No platform knowledge of business concepts.** Totals, priorities and eligibility are
  built by authors from generic nodes.
- **No claim that `quoted` means correct.** It means "written in the input". Meaning is
  protected by cross-checks and review.
- **No change to the structured-output repair prompt.** That's tracked separately,
  because it affects the calibrated grounding gate.

## 15. Decisions (agreed 2026-10-08)

1. **Review mechanism:** pause, and a person chooses the branch.
2. **Number words:** accepted. Zero to twenty and "a dozen" quote their numbers.
3. **Default for new workflows:** `contract: 2`, so enforcement is on by default.
4. **Booleans from text:** always need confirmation. Only a structured source quotes a boolean.

## 16. Risks

| Risk | Mitigation |
| --- | --- |
| Too many reviews make people approve without reading | Measure the review rate in shadow mode first; Calculate and quoting reduce it; reviews show the evidence excerpt |
| A quoted value has the wrong role | Calculate `check` cross-checks; the evaluation measures role confusion; the limits are documented |
| Label metadata grows | Strict bounds, per-item labels capped at 64 bytes, existing ceilings kept |
| Static and runtime analysis disagree | One shared rule module; a contract fixture as for bindings; the runtime is authoritative |
| Operators turn enforcement off and forget | The setting is shown on `/api/ready` and in the editor footer when not `enforce` |
