# A6.1 Part 2: build a Repeat in the editor

**Status:** Revision 2 (2026-11-13), for a recheck. Revision 1 was approved after changes
(review of `16c1f45`, four findings R1–R4 and decisions Q1–Q3, recorded below). No
implementation has started.
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
   Agent. The editor sets the Repeat's `body` and `check` settings from these connections,
   and clears them when the connection or the node is deleted, in one undoable change.
3. Edit the stop rule with the same fields as a Condition, and the limit and `on_limit`.
4. Validate: a Repeat mistake is shown on the node that causes it, with the backend's message.
5. Run it. The Repeat card shows `running` until it ends. The execution log shows which
   iteration each body and checker event belongs to, whether each iteration met the rule (or
   that its verdict is unavailable), and why the Repeat stopped.

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
- `docs/operations.md` says Part 1 "is backend only … the editor palette does not offer it
  yet", and README calls Part 1 "in review" (**Gap G9**).

### Design review of revision 1 (2026-11-13)

The review submitted the default Q&A draft, rewired with a Repeat, to the real
`/api/validate` at `16c1f45` under contract 2 with a fixture model. The endpoint accepted it
with the rule `pass eq true` and a checker schema that requires a boolean `pass`. The same
graph with the draft's original `reader → Agent` and `Agent → Response` edges left in place
was refused: the Agent was both a flow step and an attachment, and the reader had two
`continue` edges. Each finding below was checked again for this revision.

| Id | Finding | Checked for rev 2 | Change |
|---|---|---|---|
| R1 (P2) | Imported loop edges enter a port named `input`; the target card's top port has no id, so a new `body` source handle alone still gives error 008. The backend also accepts For each loop edges with no source handle; with two source handles on the card React Flow attaches an unnamed edge to the first one | `platform_graph.py:23-48`: flow and loop `targetHandle` may be `None` or `'input'`; For each `sourceHandle` may be `None` or `'body'`; tool `sourceHandle` may be `None` or `'output'`. `workflow-editor.tsx:204`: the top target has no id. The probe's saved edges carry `targetHandle: 'input'` | E2 maps each accepted handle to a drawn port for display only. B02 checks where each line attaches |
| R2 (P2) | Deleting a checker must clear `config.check` in the same change. Keyboard deletion sends node and edge changes separately; the inspector's Delete node has its own path. A second connection to an occupied port is not defined | `workflow-editor.tsx:1277-1291`: `onNodesChange` and `onEdgesChange` each call `snapshot()` on a remove, so one keyboard Delete of a connected node can push two undo entries; `workflow-editor.tsx:1985-1996` filters nodes and edges by itself | E4: one removal edit for every path; an occupied port is refused. B03 expanded |
| R3 (P2) | The stored and streamed copy of a loop event is bounded; a large `item_result` becomes a string, so a missing `met` must not read as "not met" | `storage.py:160` `observed()` with `PREVIEW_CHARS` 2,000: a `loop_item` with a 6,000-character escape-heavy draft and a 3,000-character note gives `item_result` as a string, `preview_of` `['item_result.value.text', 'item_result.verdict.notes', 'item_result']`, the same for `met: true` and `met: false`. With a 3,800-character draft the object is kept and only the draft is shortened | E7 fallback note. U05 adds that payload |
| R4 (P3) | Switch blocks Validate and Run while a case's Options JSON is unfinished (`lib/workflow.ts:431`, `cases[].when` only). The shared rule editor needs the same check for `until` | Confirmed | E10. U08 and B07 |

Also found while checking R3 and Q3: a guard stop is a complete answer
(`repeat.py:repeat_outcome`, `truncated: False`), so it is never an incomplete-run cause. Its
explanation goes in the execution log (E7), not in the incomplete-run panel (E8). A Repeat's
incomplete-run cause is `limit` or `budget`, and its recorded reason already names the outcome
(`worker.py`, from `summary.truncation_reason`).

### Prior art (read 2026-11-13)

| Tool | How the author sets the loop | How a run shows iterations |
|---|---|---|
| [Azure Logic Apps, Until loop](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-control-flow-loops) | A loop box holds the actions; the stop condition is set in the box; **Change limits** sets Count (default 60, max 5,000) and Timeout ([limits](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-limits-and-config)) | [Run history for Until iterations](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-control-flow-loops#review-run-history-for-until-loop-iterations): select the loop to see each iteration and the status of its child actions |
| [Dify Loop node](https://docs.dify.ai/en/cloud/use-dify/nodes/loop) | A container node with a termination condition and a maximum loop count | Not read for this design |

What Relay takes:

1. **The stop rule and the limit live on the loop node**, as in both tools. In Relay they are
   the Repeat's settings.
2. **Each child event is tied to its iteration**, as Logic Apps' run history is. In Relay the
   execution log names the iteration on each body and checker event.
3. **Reaching the limit is visible, not a silent success.** Logic Apps succeeds by default at
   its limit and needs a code-view option to fail; Relay already fails by default
   (`on_limit: fail`) and marks `last` as incomplete (Part 1). The log says which stop it was.

Not taken: a container box around the body (a canvas layout change, as for forks in A5.3), an
iteration pager, and a timeout (Relay has the run-wide budgets instead). The attachment
changes (E2, E4) come from Relay's own saved graph format, not from prior art.

## Decisions (review of rev 1)

- **Q1. For each:** draw its `body` connection, older saved edges included (E2). Keep For each
  out of the palette in this part.
- **Q2. Default rule:** keep `pass eq true` for the writer-and-checker setup. With no checker
  the inspector says "The stop rule reads the body's output. This rule expects a JSON object
  with a `pass` field." (the field name is the rule's, E5).
- **Q3. Wording:** the incomplete-run heading is "Repeat did not meet its stop rule", with the
  specific outcome beneath it (E8). The log notes are in E7.

## Changes

| Id | Change | Where |
|---|---|---|
| E1 | `paletteCategory('repeat')` returns `'Control'`. Icon `Repeat`. A new Repeat starts with `until: {operator: 'eq', field: 'pass', compare_to: 'true'}` and the schema defaults (`max_iterations` 3, `on_limit` `fail`, `body` and `check` empty) | `lib/workflow.ts` (`newRepeatConfig`), `workflow-editor.tsx` (`addNode`, icon map) |
| E2 | **Loop ports and handle mapping.** A Repeat card draws source handles `body` and `check` on its right side, each with a port label; a For each draws `body`. The top target and the bottom flow source stay. On these two cards the bottom flow source gets the id `flow`, so no edge depends on handle order. A new helper `canvasEdge(edge, sourceType)` maps the handles the backend accepts to the drawn ports, **for display only**: `targetHandle: 'input'` → the top port (no id), for every kind; a For each loop edge with no source handle → `body`; a flow edge with no source handle from a Repeat or For each → `flow`; a tool edge with `sourceHandle: 'output'` → the tool's unnamed source. `savedConnection(connection)` saves a new connection from `flow` with no source handle, as today. The saved and submitted graph keeps the stored handle values unchanged (the mapping is in the `edges` prop of the canvas only). A loop edge is labelled with its port (`body`, `check`), not `loop`. The Repeat card reads **"Up to N iterations"** ("Up to 1 iteration") in place of the description | `lib/workflow.ts`, `workflow-editor.tsx` (`WorkflowCard`, canvas `edges`) |
| E3 | `connectionKind` returns `'loop'` for a source handle `body` on a For each or Repeat, and for `check` on a Repeat, and `'flow'` for `flow`. Any other use of those handles returns `null` ("These ports do not support this connection."). The target type is not checked: Validate shows the backend message | `lib/workflow.ts` |
| E4 | **Attach and detach, one edit each.** Connecting a loop edge clears the target's input bindings (as a tool connection does) and sets the source's `body` or `check` to the target id (`withLoopAttachment`), with one undo entry. A connection to a `body` or `check` port that already has a loop edge is refused with "The body port is already connected. Delete its connection first." (`check` likewise): no edge is added and nothing is recorded, so one setting never has two edges. One function, `removeElements(nodeIds, edgeIds)`, removes the nodes, the listed edges, every edge of a removed node, and clears each loop setting that names a removed target (`withoutLoopAttachment`), in one state change with one undo entry. Keyboard Delete reaches it through React Flow's `onDelete`; `onNodesChange` and `onEdgesChange` stop applying `remove` changes; the inspector's Delete node button calls it too. One Undo restores the lines and the settings together. An import is never changed: a mismatch in a file stays visible to Validate | `lib/workflow.ts`, `workflow-editor.tsx` (`onConnect`, canvas handlers, inspector) |
| E5 | Inspector. Body and Checker are shown as read-only lines from the settings ("Writer" or "Connect a node to the body port"; "Judge" or "No checker"), for For each too. The stop rule is edited with the Condition fields that a Switch case uses, extracted from `switch-cases.tsx` into `ConditionFields` without a change to Switch. Helper line with a checker: "The checker's output schema must require the field the rule reads." With no checker: "The stop rule reads the body's output. This rule expects a JSON object with a `<field>` field." (`<field>` is the rule's `field`; the line is left out while the field is empty). `max_iterations` and `on_limit` keep the generic fields | `components/condition-fields.tsx` (new), `switch-cases.tsx`, `workflow-editor.tsx` |
| E6 | Card status reads node events only: `nodeStatuses` skips events of kind `loop_item` and `loop_summary`. This also removes the `info`/`warning` flash on a For each card | `lib/run-status.ts`, `workflow-editor.tsx` |
| E7 | **Execution log.** An event with `loop_node_id` is titled `<node> · iteration N` when its owner is a Repeat (`· item N` for a For each), N = `item_index + 1`. A Repeat `loop_item` gets one note, read only from an `item_result` object: `met: true` → "Iteration N met the stop rule."; `met: false` → "Iteration N did not meet the stop rule."; status `interrupted` → "Iteration N was stopped by the run budget before it was checked."; status `abstained` → "Iteration N stopped: the answerability guard abstained.". In every other case (an `item_result` that is a string because the stored copy was bounded, missing, or without a boolean `met` on a finished iteration) → **"Iteration N finished. Verdict details are unavailable."** A missing `met` is never read as false. The Repeat's own `success` event (not bounded: it has no `loop_node_id`) gets one note from its `summary` JSON: `rule` → "Stopped by its rule after N of M iterations."; `limit` → "Reached its limit of M iterations without meeting its rule. Returned the answer from iteration M."; `budget` → "The run budget stopped iteration N+1 before it was checked. Returned the answer from iteration N."; `guard` → "The answerability guard stopped the loop and returned an abstention.". A summary that does not parse gives no note. "Answer", not "draft": the body's output is not always a draft, and the backend says "answer" | `lib/run-status.ts` (`eventTitle`, `eventNotes`), `workflow-editor.tsx` |
| E8 | `incompleteRun` takes the run's workflow and labels a `loop` cause on a Repeat node **"Repeat did not meet its stop rule"**. Beneath it the cause's recorded reason is shown, as for every cause; for a Repeat it already names the outcome ("r reached 3 iterations without meeting its stop rule; the last answer is returned." or "r: the run budget stopped iteration 3 before it was checked; the answer from iteration 2 is returned. …"). The editor does not rewrite backend text. A For each keeps its label; a Repeat recovered from a budget failure keeps the `budget` label. A guard stop never reaches this panel (complete answer) | `lib/run-status.ts` |
| E9 | Quick guide: one paragraph on Repeat (body, optional checker, stop rule, limit). Docs: `docs/operations.md` "Bounded loops" says how to build a Repeat in the editor; README roadmap marks Part 1 done and Part 2 done after merge; counts updated | `workflow-editor.tsx`, docs |
| E10 | **Local JSON check for the stop rule.** `localProblems` checks a Repeat's `until` as it checks a Switch case's `when`: a nested JSON field (Options) that is still text gives "Stop rule Options is not valid JSON yet. Fix it before validating or running." One helper checks one nested `ConditionConfig` with a label, used for both. The unfinished text stays visible in the field; Validate and Run send no request while it is there. The backend still owns rule and graph validation | `lib/workflow.ts` |

**No change, by decision:**

- **No client-side graph rules.** The editor does not refuse a tool as a checker, a write in
  the body, nesting, or a stop rule whose field the checker schema does not require. Validate
  shows the backend's located message (B05). The occupied-port refusal (E4) is an editing
  rule for one setting, not a graph rule.
- **No automatic checker schema.** The editor does not write `output_schema` on the judge. A
  generated schema would be a hidden setting on another node.
- **No silent repair on import.** E2 changes what is drawn, never what is saved; E4 acts on the
  user's connect and delete actions only.
- **Bindings stay as they are.** The probe shows parity after the Repeat and empty bindings on
  attached callables.

## Acceptance (tests first)

Every new case fails on `10bf057` by behavior, except the named controls. The browser cases
use the existing harness (mocked `/api/**`, `request-nodes.json` as the catalog).

| Id | Kind | Case | Red on baseline because |
|---|---|---|---|
| U01 | unit | `paletteCategory('repeat') === 'Control'`; `paletteCategory('for_each') === null` (control) | returns `null` |
| U02 | unit | `connectionKind`: Repeat `body` and `check` → `loop`; For each `body` → `loop`; Repeat and For each `flow` → `flow`; For each `check` → `null`; Agent `body` → `null`; Repeat with no source handle → `flow` | returns `flow` |
| U03 | unit | `withLoopAttachment` sets `body`/`check`; `portInUse` is true for a `body` or `check` with an existing loop edge from that node, false for another node's port; `removeElements` removes a node's edges with it and clears only the setting that names the removed target (kept when another target is removed), for edge and node removal | import error from the new helpers |
| U04 | unit | `nodeStatuses`: the latest node event wins; `loop_item` and `loop_summary` events never set a status | import error |
| U05 | unit | `eventTitle` and `eventNotes` on the events of `repeat-run-events.json` (K02): iteration numbers from 1; met and not-met notes; the summary note. Plus hand-made `interrupted` and `abstained` rows, each `stopped_by`, an unparsable summary (no note), and the bounded payload of R3 (`item_result` a string, for `met: true` and `met: false`) and an object without `met`: both give "Verdict details are unavailable." | no iteration text |
| U06 | unit | `incompleteRun` with the workflow: a `loop` cause on a Repeat gets the Repeat label and its recorded reason; on a For each, the existing label; a Repeat `budget` cause keeps the budget label | the For each label for both |
| U07 | unit | `canvasEdge` and `savedConnection` over the handle table of E2: `input` → top port for flow, loop and specialist edges; For each loop with no source handle → `body`; flow from Repeat or For each with no source handle → `flow`; tool `output` → unnamed; a new connection from `flow` saves no source handle; all other values unchanged | import error |
| U08 | unit | `localProblems`: a Repeat whose `until` Options is text gives the stop-rule message on that node; valid JSON gives none; the Switch case message is unchanged | no message for `until` |
| B01 | browser | Start from the Q&A draft with an authorized fixture model. Add a Repeat and a second Agent from the palette. **Delete the draft's `reader → Agent` and `Agent → Response` flow edges.** Connect the reader's `continue` route → Repeat, Repeat `body` → the draft's Agent, `check` → the new Agent, Repeat → Response. Set the new Agent's output schema to require a boolean `pass`, keep the default rule, bind Response text to the Repeat's text. Validate. The submitted graph equals the K01 graph (generated ids resolved): the node types and configs, and for every edge its `kind`, `sourceHandle` and `targetHandle` | no Repeat in the palette |
| B02 | browser | Import the K02 workflow (loop edges with `targetHandle: 'input'`): 4 of 4 edges drawn, labels `body` and `check`, no React Flow 008 warning, and **each line attaches to its port**: the path start of each edge is inside the bounding box of its source handle (Repeat `body`, Repeat `check`, Repeat `flow`) and its end inside the target's top handle. Import a For each workflow whose loop edge has no source handle: its line starts at `body`, and its flow edge starts at `flow`. Export after import: the edges are byte-equal to the file's | 2 of 4 edges; error 008 |
| B03 | browser | The inspector shows Body and Checker as text; change the rule to `gte` on `score` with 0.8 (the stale operand is dropped). (a) Delete the check edge with the keyboard: the submitted Repeat has `check: ''`; one Undo restores the edge and `check: 'judge'`. (b) Delete the Judge node with the keyboard, then (c) with the inspector's Delete node: each time the check edge goes, `check` is `''`, and one Undo restores the node, the edge and the setting. (d) Drag a third Agent to the occupied `check` port: the notice is shown and the edge count and settings are unchanged | `until` is a text box; settings are not tied to edges |
| B04 | browser | Run with the K02 events in two stages. Stage 1 (through iteration 1): the Repeat card shows `running`. Final: the log titles read `writer · iteration 1`, `judge · iteration 2`, the two `loop_item` notes and the summary note | card shows `Info`; no iteration text |
| B05 | browser, **control** | Located Repeat issues. `frontend/tests/browser/repeat-issues.json` holds real `/api/validate` results for four broken Repeats (a checker without an output schema, a write tool in the body, a rule field the schema does not require, and the B01 graph with the old `reader → Agent` and `Agent → Response` edges kept). The editor lists each message and marks exactly the named nodes invalid | expected green: proves existing behavior with real messages |
| B06 | browser | The quick guide contains the Repeat paragraph | old text |
| B07 | browser | Type unfinished JSON in the stop rule's Options: the text stays visible, the Repeat shows the stop-rule message, and Validate and Run send zero requests. Fix it: Validate sends one | `until` is a text box |
| K01 | backend | The canonical B01 graph is valid via `/api/validate` with the authorized fixture model under contract 2, and the four broken graphs of B05 give the recorded issues in `repeat-issues.json`. `RELAY_REGENERATE_FIXTURES` rewrites the fixture | control for the API contract |
| K02 | backend | `frontend/tests/browser/repeat-run-events.json` equals the events of a fresh two-iteration run (the probe's script), apart from `seq` and `timestamp`. Regenerated the same way | control: the frontend reads real event shapes |

U03, U04 and U07 add helpers, so their red is an import error. B01–B04 and B07 are the
behavior oracles.

Budget: **8 unit tests, 7 browser tests, 2 backend cases and 17 mutants.**

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
| M45 | `canvasEdge` keeps `targetHandle: 'input'` | U07, B02 |
| M46 | The flow source of a loop card has no id (an unnamed edge goes to the first handle) | B02 |
| M47 | Keyboard Delete of a node keeps the separate node and edge handlers (`check` not cleared, two undo entries) | B03 (b) |
| M48 | The occupied-port refusal is removed | U03, B03 (d) |
| M49 | A missing `met` is read as false | U05 |
| M50 | `localProblems` ignores `until` | U08, B07 |

## Gates

- Frontend: unit tests, typecheck, lint, build, every browser test.
- Backend: the full suite at PYTHONHASHSEED 0–4 (two new contract cases), in an isolation copy.
- Deployed scenario gate: 28 of 28, unchanged.
- Hosted CI 5 of 5 on the exact commit.
- Evidence: `evals/results/a61-part2-<date>/`.

## Not in Part 2

- **Part 3:** deployed scenarios for Repeat in the release gate.
- For each in the palette (Q1).
- A container box, an iteration pager, a checker-schema template, a prompt template for the
  iteration input (parent design, "Alternatives").
- Live checker behavior with a real model (the review checked the graph and the schema with a
  fixture model only; Part 3 runs it deployed).
