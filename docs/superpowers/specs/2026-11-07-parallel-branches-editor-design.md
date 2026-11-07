# A5.3 Part 2: build parallel branches in the editor

**Status:** Revision 1 (2026-11-07), for design review. No implementation has started.
**Baseline:** `3e5ae46` (`main` after PR #7; hosted CI run `42328866534`, 5/5 green).
A5.3 Part 1, TA08 and A53-I01 are closed (`docs/reviews/a53-i01-recheck-2026-11-06.md`).
**Parent design:** `docs/superpowers/specs/2026-10-24-parallel-branches-design.md` (rev 3),
"Scope": Part 2 is "editor: Join in the palette, multi-edge sources, issue messages, browser
tests".
**Tier:** P2. Editor only: no change to the runtime, validation, storage, accounting or the
API. Design review, then failing tests, then implementation, then an implementation review.

## Outcome

A user builds, checks and runs a fork in the editor, with no JSON import:

1. Connect one node to 2–8 next nodes.
2. Add a **Join** from the palette and connect the last node of each branch to it.
3. After the Join, bind any branch output.
4. Validate: a fork mistake is shown on the node that causes it.
5. Run: branches that run at the same time show as running at the same time; a sibling
   stopped by a failure shows as cancelled.

The backend stays the only authority for graph rules. The editor does not copy them.

## Evidence

### Probe of the current editor (2026-11-07)

`evals/results/a53-part2-probe-2026-11-07/` (Playwright, mocked API, baseline `3e5ae46`):

| Question | Result | Consequence |
|---|---|---|
| Join in the palette | Not offered | **Gap E1.** |
| Fork from an import | Drawn; one bottom handle carries both branch edges | Multi-edge sources already work. No change. |
| Join card | Generic icon and the registry description; no branch count | **Gap E2.** |
| Bindings after the Join | Input plus every output of both branches | Part 1 parity holds. No change. |
| Bindings inside a branch | Input only, no sibling output | No change. |
| Duplicate drag `a → join` | Ignored (5 edges stay) | No change. |
| New drag `b → out` | Added (6 edges) | Real drags work, so browser tests can draw edges. |

From the code at `3e5ae46`:

- Every branch error in `compiler.branch_errors` starts with a node id, so `/api/validate`
  returns located issues, and the editor marks those nodes invalid
  (`workflow-editor.tsx:883`). Expected to work; not yet proved for fork messages.
- Card status is the latest event per node (`workflow-editor.tsx:880`). Two branches can show
  `running` at once. A sibling cancelled by a failure emits `cancelled` (compiler node
  wrapper). There is no `.workflow-card.cancelled` style; the card shows the word.
- The quick guide still says "Loops, parallel execution, approvals and team permissions are
  future releases." (`workflow-editor.tsx:2127`). **Gap E3.** `docs/operations.md:358` says
  forks are built through the API until Part 2. **Gap E4.**

### Prior art (read 2026-11-07)

| Editor | Start branches | Join | What a later step can read |
| --- | --- | --- | --- |
| [Azure Logic Apps / Power Automate](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-control-flow-branches) | **+** on an arrow, then "Add a parallel branch" | An action whose "Run after" lists the last action of each branch; it waits for all branches | Outputs of the branches it runs after |
| [Dify](https://dify.ai/blog/accelerating-workflow-processing-with-parallel-branch) | **+** on a node adds another next node | Branches meet at the downstream node | [Parallel nodes cannot read each other's variables; the converging node reads all](https://docs.dify.ai/en/cloud/use-dify/build/orchestrate-node) |
| [Step Functions Workflow Studio](https://docs.aws.amazon.com/step-functions/latest/dg/workflow-studio.html) | Drag the **Parallel** flow state from the States browser | The Parallel state ends when all branches end | The array of branch outputs |

What Relay takes:

1. **A second connection starts a branch**, as in Dify. Relay already supports this gesture.
2. **The join is a visible palette item**, as Parallel is in Workflow Studio's flow states.
3. **The join names its arrivals explicitly**, as Logic Apps' "Run after" does. In Relay
   the arrivals are the edges into the Join, and the card shows how many there are.
4. **Sibling isolation and the union after the join** match Dify; Part 1 already does both.

Not taken: an "Add parallel branch" button (a second gesture for what a drag already does),
and a container box around branches (a canvas layout change, out of proportion for P2).

## Changes

| Id | Change | Where |
|---|---|---|
| E1 | `paletteCategory('join')` returns `'Control'`, so the Join is offered with Condition, Calculate, Switch and Request reader. Icon `GitMerge`. Adding it from the palette uses the existing `addNode` (no inputs, no settings). | `lib/workflow.ts`, `workflow-editor.tsx` (icon map) |
| E2 | The Join card shows **"Waits for N branches"** in place of the description. N counts the Join's distinct sources over normal flow edges (not error routes, tool or specialist edges), from the editor's current edges. N is a count, not a judgment: the card does not say whether the branches are valid. | `lib/workflow.ts` (`joinArrivals`), `workflow-editor.tsx` (card) |
| E3 | Quick guide: "Compose your workflow" adds one sentence: "To run steps at the same time, connect one node to 2–8 next nodes and end every branch at one Join." The "future releases" note drops "parallel execution". | `workflow-editor.tsx` |
| E4 | Docs: `docs/operations.md` "Parallel branches" says how to build a fork in the editor (and keeps JSON import). `docs/platform-reference.md` roadmap row and README roadmap mark Part 2 done; counts updated. | docs |

**No change, by decision:**

- **No client-side graph rules.** The editor does not block a connection that breaks a fork
  rule. Validate shows the backend's located message. Two copies of the rules would drift;
  binding parity was needed only because the editor *offers* bindings.
- **No automatic binding after a Join.** `flowBinding` uses the source's first output; a Join
  has none, so a node connected after a Join starts with an empty input, and the inspector
  lists every branch output. Choosing a branch for the user would be a guess.
- **No new card style for `cancelled`.** The card already shows the word; B04 proves it.
- **The stale-binding text** "(not available on every path here)" stays. It also covers a
  sibling binding, which the backend refuses with its existing binding message.

## Acceptance (tests first)

Every new case fails on `3e5ae46` by behavior, except the named controls. The browser cases
use the existing harness (mocked `/api/**`, `request-nodes.json` as the catalog).

| Id | Kind | Case | Red on baseline because |
|---|---|---|---|
| U01 | unit | `paletteCategory('join') === 'Control'` | returns `null` |
| U02 | unit | `joinArrivals` counts distinct normal flow sources: two branch edges → 2; plus an error-route edge, a tool edge and a duplicate source → still 2 | import error: a new helper (see below) |
| B01 | browser | Build `input → A, B → Join → Response` from the palette with real drags. Validate sends exactly that graph. The Response's binding list offers both branches' outputs; choosing Branch B → text sends `b.text`. | no Join in the palette |
| B02 | browser | The Join card reads "Waits for 2 branches"; after a third branch is drawn, "Waits for 3 branches". | the card shows the description |
| B03 | browser, **control** | Located fork issues. The fixture `frontend/tests/browser/parallel-issues.json` holds real `/api/validate` results for three broken forks (a branch that ends without the Join, an external write inside a branch, nine branches). The editor lists each message and marks exactly the named nodes invalid. | expected green: proves existing behavior with real messages |
| B04 | browser, **control** | Mocked run events: A and B both `running`, then A `failed` and B `cancelled`. Both cards show running together, then failed and cancelled. | expected green |
| B05 | browser | The quick guide contains the fork sentence and no longer calls parallel execution a future release. | old text |
| K01 | backend | `parallel-issues.json` equals the live `/api/validate` output for its three workflows (`RELAY_REGENERATE_FIXTURES` regenerates it), as `example-catalog.json` is guarded. | control (new contract guard) |

U02 adds a helper, so its red is an import error, not a behavior. B02 is the behavior
oracle for E2; U02 pins the counting rule at unit level.

Budget: **2 unit, 5 browser, 1 backend case; 4 mutants.**

| Mutant | Change | Planned oracle |
|---|---|---|
| M21 | Join not in the palette | B01 |
| M22 | `joinArrivals` counts every incoming edge (error routes, tools, duplicates) | U02 |
| M23 | The editor's `bindableOutputs` intersects at a Join (Part 1's M17, now through the UI) | B01 |
| M24 | The "future releases" note keeps "parallel execution" | B05 |

## Gates

- Frontend: unit tests, typecheck, lint, build, every browser test.
- Backend: the full suite at PYTHONHASHSEED 0–4 (one new contract case), in an isolation copy.
- Deployed scenario gate (`scripts.check_production_scenarios`): 24 of 24, unchanged.
- Hosted CI 5 of 5 on the exact commit.
- Evidence: `evals/results/a53-part2-<date>/`.

## Not in Part 2

- **Part 3** (deployed scenarios S22–S24 for forks, from the parent design) stays separate:
  it changes the deployed gate, not the editor.
- Writes and approvals in branches (A5.3b), optional branches (A5.3c), nested forks.
- A container or auto-layout for branches; an "Add parallel branch" button; fork templates.

## Questions for the review

1. Is E2 (the branch count on the Join card) worth its two cases, or should the card keep
   the registry description?
2. Are B03 and B04 as controls enough, or should either drive a change (for example a
   `cancelled` card style)?
