# A5.2 multi-way routing: implementation plan

> Design: [multi-way routing](../specs/2026-10-18-multi-way-routing-design.md), approved
> 2026-10-21 with three fixes. Baseline: `5cdcd64` (hosted CI run `39174617614`, 5/5 green).

**Goal:** a **Switch** node picks exactly one of several named paths, using the Condition
predicates. It accepts only checked or trusted values, only in contract-2 workflows.

**Out of scope:** routing review, approval API changes, database migration, parallel
branches (A5.3), regex, model-based routing.

## Rules for every task

1. Write the tests first. Show that they fail on the baseline, then make them pass.
2. Run mutants on each safety rule (scratch copies only).
3. Run the full backend suite at `PYTHONHASHSEED` 0–4 before each commit.
4. The user commits and pushes. Check the five hosted CI jobs after each push.
5. No change to the running Relay, its database or its history.

## Shape of the node

```json
{"id": "route", "type": "switch", "inputs": {"value": "reader.text"},
 "config": {"cases": [
   {"id": "case_billing",   "label": "Billing",   "when": {"field": "category", "operator": "eq", "compare_to": "billing"}},
   {"id": "case_technical", "label": "Technical", "when": {"field": "category", "operator": "eq", "compare_to": "technical"}}
 ]}}
```

- Output: `branch` = the first matching case `id`, else `default`.
- Edges: one for each case `id` and one for `default`. No others.
- `when` is a full `ConditionConfig`, so every Condition rule and error applies unchanged.

## Task 1: the node-aware route contract

**Why first:** Switch routes come from the node's settings, not from its type. Every place
that reads routes must ask the node. One missed place is a bypass.

**Files:** `request_contracts.py`, `registry.py`, `compiler.py`, `platform_graph.py`,
`frontend/lib/workflow.ts`, `frontend/components/workflow-editor.tsx`.

1. Add `NodeDefinition.configurable_routes: bool = False` and publish it in `public()`.
2. Replace `control_routes(definition)` with two functions:
   - `route_contract(definition)`: type-level check only (used by registration). A type
     has static routes, or `configurable_routes`, never both; either way it needs a
     string `branch` output.
   - `node_routes(node)`: the routes of one node. Static types return their
     `branch_routes`. Configurable types read their validated config. Invalid config
     returns no routes, so validation reports the config error, not a route error.
3. Change all six backend callers to `node_routes(node)`:
   - `compiler.py` validation (~line 144), cached restore (~455), fresh output (~525),
     graph edges (~643);
   - `platform_graph.py` attachment refusal (~24): "is a control node" means
     `node_routes(node)` is not empty or the type is configurable;
   - `registry.py` (~83) keeps only `route_contract(definition)`.
4. Frontend: replace `controlRoutes(definition)` with `nodeRoutes(definition, node)`, with
   the same rules. The canvas draws handles from it.
5. Delete `control_routes`. A test asserts that the name is gone from `backend/app`.

**Tests (fail on baseline where marked):**
- Existing Condition, Request reader and router workflows validate and run unchanged
  (all current tests stay green).
- Registry: static plus configurable on one type is refused; configurable without a
  string `branch` output is refused.
- Backend/frontend parity: a shared JSON fixture of nodes and their expected routes; both
  sides must produce the same routes (fails on baseline: no `nodeRoutes`).

## Review changes (2026-10-21, after Task 1)

1. **Tasks 2 and 3 land in one commit.** If the Switch were registered before its safety
   rule, one pushed version would let an unchecked model value choose a business path.
2. **Task 1 fix:** a configured-route callback must return a list or tuple of 2–16 valid
   handles. A string, a mapping, `None` or fewer than 2 routes after valid settings is an
   error; only invalid settings give no routes.
3. **Positive parity fixture:** Task 2 adds Switch nodes with valid cases to
   `frontend/tests/fixtures/node-routes.json`, so backend and editor are proven to agree on
   configured routes, not only on static ones.

## Task 2: the Switch node and its execution (same commit as Task 3)

**Files:** new `backend/app/switch.py`; `registry.py`; `compiler.py`; `request_safety.py`;
`provenance.py`; tests `backend/tests/test_switch.py`.

1. `SwitchCase(id, label, when: ConditionConfig)`:
   - `id`: control-handle grammar, not `default` or `error`, unique;
   - `label`: 1–80 characters, nonblank; labels need not be unique;
   - 1–15 cases (16 handles with `default`).
2. `switch_node`: evaluate cases in order with `conditions.evaluate`; return the first
   true `id`, else `default`. A comparison error raises and fails the run; it never
   selects `default` and never tries the next case.
3. Register `switch` (category Control, input `value`, output `branch`,
   `configurable_routes=True`).
4. `request_safety.finalize`: on fresh and cached output, recompute the branch from the
   input and config, and refuse a mismatch (as Condition does). A cached branch outside
   `node_routes(node)` is refused before this check.
5. Provenance: the `branch` port is `calculated` when the input is eligible (Task 3
   guarantees this); journal line `switch.route` with node id and case id only.

**Tests:**
- Every operator matches Condition on the same values: exact numbers, text, `in`,
  `empty`; `0` and `false` are real values.
- First match wins when two cases overlap; the second predicate is not evaluated
  (a predicate that would raise, placed second, does not raise).
- No match → `default`. A missing field in an evaluated case → the run fails with the
  Condition error; `default` is not taken.
- Config errors: duplicate id, reserved id, 0 cases, 16 cases, invalid predicate.
- Edges: a missing case edge, an extra handle, a duplicate handle, a `default` missing.
- Only the selected path runs: the other paths make no model call and no tool call.
- Cached checkpoint with an undeclared branch, or a branch that differs from the
  recomputed one, is refused.
- **Conformance test:** one Switch workflow through validation, platform graph,
  compilation, fresh output and cached output, with a forged branch at each boundary.

## Task 3: only checked or trusted values (the safety rule, same commit as Task 2)

**Files:** `compiler.py` (`request_contract_errors`), `request_safety.py`.

1. **Contract 1:** validation refuses any Switch: "Switch requires a contract-2 workflow."
2. **Validation (contract 2):** the `value` binding must be either
   `checked_dependency(ref)` (a verifier's `text`, or Prompt/Calculate built only from
   checked values) or `static_label(ref) == STATIC_TRUSTED`
   (`provenance.independent_source`). Otherwise: "Route on checked values, or use a
   Condition, which supports review." Reuse these functions; add no new trust list.
3. **Runtime (fresh and resume):** in `check_execution_eligibility`, treat `switch` as a
   decision node. Allowed only when the input has a valid binding (`binding_valid`), or it
   has no model input and its runtime label is trusted. Otherwise raise
   `UnsafeExecutionError`. Never create a review.
4. A Switch adds no authority: writes after any case keep their own eligibility and
   approval checks.

**Tests (all must fail on baseline once Task 2 exists without Task 3):**
- Allowed: Request reader `text`; Verify extraction `text`; Calculate over the reader;
  chat message; Jira search output.
- Refused at validation: raw Agent or LLM output; HTTP tool output; Python tool output;
  a Switch in a contract-1 workflow; a Switch attached as a tool or loop body.
- Refused at runtime: a forged `_verification` receipt; a receipt for another value; a
  stale receipt on resume after the input changed.
- A case with id `approved` before an HTTP write with `approval: true` still pauses for
  write approval; a write after the Switch still needs a valid binding.
- Durable worker: a run that resumes after a crash takes the same case and sends once.
- Parity: `node-routes.json` gains Switch nodes with 1, 3 and 15 valid cases (and one with
  invalid settings); the editor's `nodeRoutes` and the backend's `node_routes` must agree.

## Task 4: the editor

**Files:** `frontend/lib/workflow.ts`, new `frontend/components/switch-cases.tsx`,
`workflow-editor.tsx`, `frontend/lib/switch.ts`; tests in `frontend/tests/switch.test.ts`
and `frontend/tests/browser/switch.spec.ts`; fixture `switch-node.json` (generated from
the registry, with a parity test like `condition-node.json`).

1. Palette: Switch under Control.
2. Case list: add, rename label, move up/down, remove. Each case reuses the Condition
   operator controls (`conditionSettingVisible`).
3. New cases get a generated id (`case_` + 6 random characters). The id is shown read-only;
   renaming or moving a case never changes its id or its edge.
4. A case with a connected edge has no plain delete. The button reads "Remove case and
   its connection" and removes both together.
5. Handles: one per case (label shown), plus an always-visible **Default**.
6. Warning, not an error: a later case whose normalized predicate equals an earlier one
   ("This case can never match: case 1 has the same rule").

**Tests:**
- Unit: id generation is unique; two predicates count as duplicates only when field,
  operator, operands and comparison options are equal after the backend's own trimming;
  reordering keeps ids.
- Browser: build a Switch with three cases, connect them, rename and reorder, validate,
  and check the saved JSON keeps ids and edges; remove a connected case; duplicate
  warning shows; old workflows open unchanged.

**Done (2026-10-23), with two changes from this task text:**
1. No separate `switch-node.json`: the browser tests serve `request-nodes.json`, which
   `test_request_examples.py` already pins to the whole registry, Switch included.
2. Duplicate rules are flagged only when they are exactly equal after the backend's
   defaults are filled in. Rules that differ only in spacing are not flagged, so the
   warning never names a case that could still match.

## Task 5: deployed scenario, docs and evidence

**Files:** `scripts/check_production_scenarios.py` (scenario S20, S21),
`docs/typed-conditions.md`, `docs/request-contract-interfaces.md`,
`docs/platform-reference.md`, `README.md` roadmap.

1. **S20 support routing:** contract 2, Request reader (support template rules) → Switch
   on `category` (billing, technical, account, default) → four local receivers. Input is
   JSON (`{"summary":"Invoice download is broken","category":"billing"}`) so the
   `source_field` rules pass. Model answers come from the scenario's model fixture.
   Result: exactly one receiver gets exactly one request, on the billing path.
2. **S21 fault cases:** a forged branch in a saved checkpoint, an unchecked HTTP value, a
   case named `approved`: zero sends on any unselected or unauthorized path.
3. Docs: Switch rules, the trusted-input rule, the contract-2 requirement, and that it
   claims nothing about prose classification quality.
4. Evidence folder `evals/results/multi-way-routing-2026-10-<day>/`: summaries and gate
   results in git, full logs ignored (the existing evidence rule).

## Done when

1. All acceptance items in the design pass, with tests that fail on the baseline.
2. Backend `PYTHONHASHSEED` 0–4, frontend unit, typecheck, lint, build and browser tests
   pass; isolation copy of the commit files passes.
3. Deployed scenario gate (S01–S21) and the SQLite/PostgreSQL review scenario pass.
4. Hosted CI is green on the exact pushed commit.

## Commits

One commit per task, except Tasks 2 and 3, which are one commit (4 commits). Each has its own tests. The user commits and pushes;
the next task starts after hosted CI passes.

| Task | Size estimate | Main risk |
|---|---|---|
| 1. Route contract | Small–medium | A missed caller; the conformance test in Task 2 closes it |
| 2. Switch node | Medium | Cached-branch recompute must match fresh evaluation |
| 3. Safety rule | Medium | Runtime label vs validation label disagreeing |
| 4. Editor | Medium–large | Edge stability when cases change |
| 5. Scenario and docs | Small | Scenario needs JSON input; documented |
