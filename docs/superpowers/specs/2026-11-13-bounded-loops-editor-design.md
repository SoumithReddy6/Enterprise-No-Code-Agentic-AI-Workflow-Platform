# A6.1 Part 2: build a Repeat in the editor

**Status:** Revision 1 (2026-11-13), for design review. No implementation has started.
**Baseline:** `10bf057` (hosted CI run `43309358527`, 5/5 green). A6.1 Part 1 is closed on
`00281fb`; the three follow-up fixes (`0423b66`, `35ba222`, `55a49f0` + `10bf057`) are pushed.
**Parent design:** `docs/superpowers/specs/2026-11-09-bounded-loops-design.md` (rev 5),
"Scope": Part 2 is "editor: palette, the `body` and `check` ports, inspector, iterations in
the run view".
**Probe:** `evals/results/a61-part2-probe-2026-11-13/`.
**Tier:** P2. Editor only: no change to the runtime, validation, storage, accounting or the
API. Design review, then failing tests, then implementation, then an implementation review,
as in A5.3 Part 2.

## Outcome

A user builds, checks and runs a writer-and-judge loop in the editor, with no JSON import:

1. Add **Repeat until** from the palette. It starts with a stop rule that reads the field
   `pass` of the checker's answer.
2. Connect the Repeat's **body** port to a writer Agent and its **check** port to a judge
   Agent. The editor sets the Repeat's `body` and `check` settings from these connections.
3. Edit the stop rule with the same fields as a Condition, and the limit and `on_limit`.
4. Validate: a Repeat mistake is shown on the node that causes it, with the backend's message.
5. Run it. The Repeat card shows `running` until it ends. The execution log shows which
   iteration each body and checker event belongs to, whether each iteration met the rule,
   and why the Repeat stopped.

The backend stays the only authority for graph rules. The editor does not copy them.

## Evidence

### Probe of the current editor (2026-11-13)

`evals/results/a61-part2-probe-2026-11-13/` (Playwright, mocked API, baseline `10bf057`). The
run events are a real two-iteration run recorded from the Part 1 test helpers.

| Question | Result | Consequence |
|---|---|---|
| Repeat in the palette | Not offered | **Gap G1** |
| Repeat card ports | One target and one source handle, no ids | **Gap G2** |
| Imported loop edges | 2 of 4 drawn; React Flow error 008 for `body` and `check`. Writer and Judge float unconnected | **G2.** The user cannot see or delete the attachments |
| Drag from the Repeat to the Judge | Adds an edge, which can only be a flow edge | **Gap G3:** no way to draw a loop edge |
| Inspector Body, Check | Free text, not tied to the edges | **Gap G4** |
| Inspector `until` | A one-line text box with an empty value; typing replaces the rule with a string | **Gap G5** |
| New Repeat from the palette | No `until` (required, no default): Validate refuses it (code reading) | **G5** |
| Bindings after the Repeat | Input, `text`, `feedback`, `summary` | Parity holds. No change |
| Bindings of Writer and Judge | Empty (attached callables) | No change |
| Card status after iteration 1 | Repeat shows **Info**: the `loop_item` event (status `info`) is the latest event for `r` | **Gap G6** |
| Execution log | `writer running/success`, `judge …` twice, no iteration number; `r info` twice, no note | **Gap G7** |

From the code at `10bf057`:

- `connectionKind` returns `tool`, `agent` or `flow`, never `loop` (`lib/workflow.ts:287`).
- The card draws named source handles only for Agent (`agents`), Switch and routed controls
  (`workflow-editor.tsx:205-238`). For each has the same gap as Repeat: an imported For each
  draws no `body` edge. For each is not in the palette either.
- `incompleteRun` labels every `loop` cause "Loop did not deliver every item"
  (`lib/run-status.ts:24`). For a Repeat that returned its last answer under `on_limit: last`
  this is wrong: the Repeat delivered one answer, which did not meet its rule. The parent
  design left the Repeat label to Part 2 (**Gap G8**).
- The backend accepts a loop edge whose `targetHandle` is empty or `input`, and refuses input
  bindings on an attached callable (`platform_graph.py`). A drag to the body's top handle
  gives `targetHandle: null`, which is accepted.
- `docs/operations.md` says Part 1 "is backend only … the editor palette does not offer it
  yet", and README calls Part 1 "in review" (**Gap G9**).

### Prior art (read 2026-11-13)

| Tool | How the author sets the loop | How a run shows iterations |
|---|---|---|
| [Azure Logic Apps, Until loop](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-control-flow-loops) | A loop box holds the actions; the stop condition is set in the box; **Change limits** sets Count (default 60, max 5,000) and Timeout ([limits](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-limits-and-config)) | After the loop exits, select it in run history "to view each iteration and the status of the child actions" |
| [Dify Loop node](https://docs.dify.ai/en/use-dify/nodes/loop) | A container node with a termination condition and a maximum loop count | Not read for this design |

What Relay takes:

1. **The stop rule and the limit live on the loop node**, as in both tools. In Relay they are
   the Repeat's settings.
2. **Each child event is tied to its iteration**, as Logic Apps' run history is. In Relay the
   execution log names the iteration on each body and checker event.
3. **Reaching the limit is visible, not a silent success.** Logic Apps succeeds by default at
   its limit and needs a code-view option to fail; Relay already fails by default
   (`on_limit: fail`) and marks `last` as incomplete (Part 1). The log says which stop it was.

Not taken: a container box around the body (a canvas layout change, as for forks in A5.3), an
iteration pager, and a timeout (Relay has the run-wide budgets instead).

## Changes

| Id | Change | Where |
|---|---|---|
| E1 | `paletteCategory('repeat')` returns `'Control'`. Icon `Repeat`. A new Repeat starts with `until: {operator: 'eq', field: 'pass', compare_to: 'true'}` and the schema defaults (`max_iterations` 3, `on_limit` `fail`, `body` and `check` empty) | `lib/workflow.ts` (`newRepeatConfig`), `workflow-editor.tsx` (`addNode`, icon map) |
| E2 | Loop ports on the card: a Repeat draws source handles `body` and `check` on its right side, each with a port label; a For each draws `body`. The top target and the bottom flow source stay. The Repeat card reads **"Up to N iterations"** ("Up to 1 iteration") in place of the description | `workflow-editor.tsx` (`WorkflowCard`) |
| E3 | `connectionKind` returns `'loop'` for a source handle `body` on a For each or Repeat, and for `check` on a Repeat. Any other use of those handles returns `null` ("These ports do not support this connection."). The target type is not checked: Validate shows the backend message | `lib/workflow.ts` |
| E4 | Attach and detach. Connecting a loop edge clears the target's input bindings (as a tool connection does) and sets the source's `body` or `check` to the target id (`withLoopAttachment`). Removing a loop edge, directly or with its target node, clears that setting only when it names the removed target (`withoutLoopAttachment`). An import is never changed: a mismatch in a file stays visible to Validate | `lib/workflow.ts`, `workflow-editor.tsx` (`onConnect`, edge and node removal) |
| E5 | Inspector. Body and Checker are shown as read-only lines from the settings ("Writer" or "Connect a node to the body port"; "Judge" or "No checker: the stop rule reads the body's answer"), for For each too. The stop rule is edited with the Condition fields that a Switch case uses, extracted from `switch-cases.tsx` into `ConditionFields` without a change to Switch. A helper line: "With a checker, its output schema must require the field the rule reads." `max_iterations` and `on_limit` keep the generic fields | `components/condition-fields.tsx` (new), `switch-cases.tsx`, `workflow-editor.tsx` |
| E6 | Card status reads node events only: `nodeStatuses` skips events of kind `loop_item` and `loop_summary`. This also removes the `info`/`warning` flash on a For each card | `lib/run-status.ts`, `workflow-editor.tsx` |
| E7 | Execution log. An event with `loop_node_id` is titled `<node> · iteration N` when its owner is a Repeat (`· item N` for a For each), N = `item_index + 1`. A Repeat `loop_item` gets one note: "Iteration N met the stop rule.", "Iteration N did not meet the stop rule.", "Iteration N was stopped by the run budget before it was checked." or "Iteration N stopped: the answerability guard abstained." A Repeat `success` event with a `summary` gets: "Stopped by its rule after N of M iterations." / "Reached its limit of M iterations without meeting its rule." / "Stopped by the run budget after N checked iterations." / "Stopped by the answerability guard." | `lib/run-status.ts` (`eventTitle`, `eventNotes`), `workflow-editor.tsx` |
| E8 | `incompleteRun` takes the run's workflow and labels a `loop` cause on a Repeat node **"Repeat did not meet its stop rule"**. A For each keeps its label | `lib/run-status.ts` |
| E9 | Quick guide: one paragraph on Repeat (body, optional checker, stop rule, limit). Docs: `docs/operations.md` "Bounded loops" says how to build a Repeat in the editor; README roadmap marks Part 1 done and Part 2 done after merge; counts updated | `workflow-editor.tsx`, docs |

**No change, by decision:**

- **No client-side graph rules.** The editor does not refuse a tool as a checker, a write in
  the body, nesting, or a stop rule whose field the checker schema does not require. Validate
  shows the backend's located message (B05).
- **No automatic checker schema.** The editor does not write `output_schema` on the judge. A
  generated schema would be a hidden setting on another node.
- **No silent repair on import.** E4 acts on the user's connect and delete actions only.
- **Bindings stay as they are.** The probe shows parity after the Repeat and empty bindings on
  attached callables.

## Acceptance (tests first)

Every new case fails on `10bf057` by behavior, except the named controls. The browser cases
use the existing harness (mocked `/api/**`, `request-nodes.json` as the catalog).

| Id | Kind | Case | Red on baseline because |
|---|---|---|---|
| U01 | unit | `paletteCategory('repeat') === 'Control'`; `paletteCategory('for_each') === null` (control) | returns `null` |
| U02 | unit | `connectionKind`: Repeat `body` and `check` → `loop`; For each `body` → `loop`; For each `check` → `null`; Agent `body` → `null`; Repeat default handle → `flow` | returns `flow` |
| U03 | unit | `withLoopAttachment` sets `body`/`check`; `withoutLoopAttachment` clears only the setting that names the removed target, and keeps it when another target is removed | import error from the new helpers |
| U04 | unit | `nodeStatuses`: the latest node event wins; `loop_item` and `loop_summary` events never set a status | import error |
| U05 | unit | `eventTitle` and `eventNotes` on the events of `repeat-run-events.json` (K02): iteration numbers from 1; met and not-met notes; the summary note. Plus hand-made `interrupted` and `abstained` rows and each `stopped_by` | no iteration text |
| U06 | unit | `incompleteRun` with the workflow: a `loop` cause on a Repeat gets the Repeat label; on a For each, the existing label | the For each label for both |
| B01 | browser | Start from the Q&A draft with an authorized fixture model. Add a Repeat and a second Agent from the palette; connect the reader's continue route → Repeat, Repeat body → the draft's Agent, check → the new Agent, Repeat → Response; set the judge's output schema, keep the default rule, bind Response text to the Repeat's text. Validate. The submitted graph equals the K01 graph (generated ids resolved). Not yet checked against the real `/api/validate`: K01 is written first, and if the endpoint refuses this draft under contract 2, the start draft is changed before B01 (as in A5.3 Part 2) | no Repeat in the palette |
| B02 | browser | Import the K02 workflow: 4 of 4 edges drawn, labels `body` and `check`, no React Flow 008 warning. Import a For each workflow: its `body` edge is drawn | 2 of 4 edges; error 008 |
| B03 | browser | The inspector shows Body and Checker as text; change the rule to `gte` on `score` with 0.8 (the stale operand is dropped); delete the check edge. The submitted Repeat has the new rule and `check: ''` | `until` is a text box |
| B04 | browser | Run with the K02 events in two stages. Stage 1 (through iteration 1): the Repeat card shows `running`. Final: the log titles read `writer · iteration 1`, `judge · iteration 2`, the two `loop_item` notes and the summary note | card shows `Info`; no iteration text |
| B05 | browser, **control** | Located Repeat issues. `frontend/tests/browser/repeat-issues.json` holds real `/api/validate` results for three broken Repeats (a checker without an output schema, a write tool in the body, a rule field the schema does not require). The editor lists each message and marks exactly the named nodes invalid | expected green: proves existing behavior with real messages |
| B06 | browser | The quick guide contains the Repeat paragraph | old text |
| K01 | backend | The canonical B01 graph is valid via `/api/validate` with the authorized fixture model, and the three broken Repeats give the recorded issues in `repeat-issues.json`. `RELAY_REGENERATE_FIXTURES` rewrites the fixture | control for the API contract |
| K02 | backend | `frontend/tests/browser/repeat-run-events.json` equals the events of a fresh two-iteration run (the probe's script), apart from `seq` and `timestamp`. Regenerated the same way | control: the frontend reads real event shapes |

U03 and U04 add helpers, so their red is an import error. B01–B04 are the behavior oracles.

Budget: **6 unit tests, 6 browser tests, 2 backend cases and 11 mutants.**

| Mutant | Change | Planned oracle |
|---|---|---|
| M34 | Repeat not in the palette | U01, B01 |
| M35 | The Repeat card draws no `check` handle | B02, B01 |
| M36 | `connectionKind` returns `flow` for the `body` handle | U02, B01 |
| M37 | A loop connection does not set `body` | U03, B01 |
| M38 | `withoutLoopAttachment` clears the setting when another target is removed | U03 |
| M39 | `nodeStatuses` keeps `loop_item` events | U04, B04 |
| M40 | The iteration number is `item_index` (from 0) | U05, B04 |
| M41 | The summary note reads `limit` as `rule` | U05 |
| M42 | `incompleteRun` ignores the node type | U06 |
| M43 | `ConditionFields` loses `withConditionOperator` (stale operand kept), for Switch too | B03, existing Switch tests |
| M44 | A new Repeat gets no default `until` | B01 (Validate refuses it) |

## Gates

- Frontend: unit tests, typecheck, lint, build, every browser test.
- Backend: the full suite at PYTHONHASHSEED 0–4 (two new contract cases), in an isolation copy.
- Deployed scenario gate: 28 of 28, unchanged.
- Hosted CI 5 of 5 on the exact commit.
- Evidence: `evals/results/a61-part2-<date>/`.

## Not in Part 2

- **Part 3:** deployed scenarios for Repeat in the release gate.
- For each in the palette (see Q1).
- A container box, an iteration pager, a checker-schema template, a prompt template for the
  iteration input (parent design, "Alternatives").

## Open questions for the review

- **Q1. For each.** E2 draws the For each `body` port too, because the card code is shared
  and an imported For each draws no body edge today. Recommendation: fix the port (it is a
  display defect) but keep For each out of the palette; offering it is a separate, small step.
- **Q2. The default stop rule** `pass eq true` fits the writer-and-judge case. Without a
  checker, it reads the body's answer as JSON and fails loudly if that is not JSON (Part 1
  rule). Recommendation: keep it; the inspector shows it at once.
- **Q3. Wording** of the E7 notes and the E8 label.
