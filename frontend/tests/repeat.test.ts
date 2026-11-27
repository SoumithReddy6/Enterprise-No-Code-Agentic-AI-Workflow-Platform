import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import type { Definition, RunEvent, Workflow, WorkflowEdge, WorkflowNode } from '../lib/workflow.ts';
import type { RunCompleteness } from '../lib/run-status.ts';

// A6.1 Part 2 (docs/superpowers/specs/2026-11-13-bounded-loops-editor-design.md, revision 3).
// Helpers are loaded inside each test, so the file still runs where they do not exist yet.
// repeat-run-events.json is a real two-iteration run (rejected, then accepted), kept true by
// backend/tests/test_repeat_editor_contract.py (K02).
const catalog = JSON.parse(readFileSync(new URL('./browser/request-nodes.json', import.meta.url), 'utf8')) as Definition[];
const recorded = JSON.parse(readFileSync(new URL('./browser/repeat-run-events.json', import.meta.url), 'utf8')) as {
  workflow: Workflow;
  events: (RunEvent & Record<string, unknown>)[];
};

type Graph = { nodes: WorkflowNode[]; edges: WorkflowEdge[] };
type Lib = {
  newRepeatConfig: () => Record<string, unknown>;
  connectionKind: (s: string, t: string, sh?: string | null, th?: string | null) => WorkflowEdge['kind'] | null;
  edgePort: (edge: WorkflowEdge, sourceType: string) => string | null;
  canvasEdge: (edge: WorkflowEdge, sourceType: string) => WorkflowEdge;
  savedConnection: <T extends { sourceHandle?: string | null; targetHandle?: string | null }>(c: T, sourceType: string) => T;
  portInUse: (graph: Graph, source: string, port: string) => boolean;
  withLoopAttachment: (graph: Graph, edge: WorkflowEdge) => Graph;
  removeElements: (graph: Graph, nodeIds: string[], edgeIds: string[]) => Graph;
};
type Status = {
  nodeStatuses: (events: RunEvent[]) => Record<string, string>;
  eventTitle: (event: RunEvent, workflow: Pick<Workflow, 'nodes'>) => string;
  eventNotes: (event: RunEvent, workflow?: Pick<Workflow, 'nodes'>) => string[];
  incompleteRun: (run: RunCompleteness | undefined, workflow?: Pick<Workflow, 'nodes'>) => {
    causes: { node: string; label: string; reason: string }[];
  } | null;
};
const lib = async () => (await import('../lib/workflow.ts')) as unknown as Lib;
const status = async () => (await import('../lib/run-status.ts')) as unknown as Status;

const node = (id: string, type: string, config: Record<string, unknown> = {}, inputs: Record<string, string> = {}): WorkflowNode =>
  ({ id, type, version: 1, label: id, position: { x: 0, y: 0 }, inputs, config });
const loop = (id: string, source: string, target: string, sourceHandle?: string | null): WorkflowEdge =>
  ({ id, source, target, kind: 'loop', sourceHandle, targetHandle: 'input' });
const config = (graph: Graph, id: string) => graph.nodes.find((n) => n.id === id)!.config;

void test('U01 Repeat is offered in Control and starts with the stop rule pass eq true; For each stays out', async () => {
  const { paletteCategory } = await import('../lib/workflow.ts');
  assert.equal(paletteCategory('repeat'), 'Control');
  assert.equal(paletteCategory('for_each'), null);
  const { newRepeatConfig } = await lib();
  assert.deepEqual(newRepeatConfig(), {
    body: '', check: '', max_iterations: 3, on_limit: 'fail',
    until: { operator: 'eq', field: 'pass', compare_to: 'true' },
  });
});

void test('U02 the body and check ports make loop connections; other uses of them are refused', async () => {
  const { connectionKind } = await lib();
  assert.equal(connectionKind('repeat', 'agent', 'body', null), 'loop');
  assert.equal(connectionKind('repeat', 'agent', 'check', null), 'loop');
  assert.equal(connectionKind('for_each', 'tool_jira', 'body', null), 'loop');
  assert.equal(connectionKind('repeat', 'response', 'flow', null), 'flow');
  assert.equal(connectionKind('for_each', 'response', 'flow', null), 'flow');
  assert.equal(connectionKind('for_each', 'agent', 'check', null), null);
  // On other cards these names are ordinary route ids (a Switch case may be named body): U09.
  assert.equal(connectionKind('repeat', 'response', null, null), 'flow');
  // Unchanged: tools and specialists.
  assert.equal(connectionKind('tool_jira', 'agent', null, 'tools'), 'tool');
  assert.equal(connectionKind('agent', 'agent', 'agents', null), 'agent');
});

void test('U03 attaching sets the setting, an occupied port is reported, removal clears only its own setting', async () => {
  const { withLoopAttachment, portInUse, removeElements } = await lib();
  const empty: Graph = {
    nodes: [node('r', 'repeat', { body: '', check: '' }), node('writer', 'agent', {}, { input: 'input.message' }),
      node('judge', 'agent', {}, { input: 'input.message' }), node('other', 'agent')],
    edges: [],
  };
  const one = withLoopAttachment(empty, loop('e1', 'r', 'writer', 'body'));
  assert.equal(config(one, 'r').body, 'writer');
  assert.deepEqual(one.nodes.find((n) => n.id === 'writer')!.inputs, {}, 'an attached callable has no input bindings');
  const both = withLoopAttachment(one, loop('e2', 'r', 'judge', 'check'));
  assert.deepEqual([config(both, 'r').body, config(both, 'r').check], ['writer', 'judge']);
  assert.equal(both.edges.length, 2);
  assert.equal(empty.edges.length, 0, 'the input graph is not changed');
  assert.equal(portInUse(both, 'r', 'body'), true);
  assert.equal(portInUse(both, 'r', 'check'), true);
  assert.equal(portInUse(one, 'r', 'check'), false);
  assert.equal(portInUse(both, 'other', 'body'), false);
  // Edge removal clears its own setting only.
  const noCheck = removeElements(both, [], ['e2']);
  assert.deepEqual([config(noCheck, 'r').body, config(noCheck, 'r').check], ['writer', '']);
  // Node removal takes its edges and clears the setting that names it.
  const noJudge = removeElements(both, ['judge'], []);
  assert.deepEqual(noJudge.edges.map((e) => e.id), ['e1']);
  assert.deepEqual([config(noJudge, 'r').body, config(noJudge, 'r').check], ['writer', '']);
  // Removing another node keeps both settings.
  const noOther = removeElements(both, ['other'], []);
  assert.deepEqual([config(noOther, 'r').body, config(noOther, 'r').check], ['writer', 'judge']);
  // A setting that names another node than the removed edge's target is kept.
  const mismatch: Graph = { ...both, nodes: both.nodes.map((n) => n.id === 'r' ? { ...n, config: { ...n.config, check: 'other' } } : n) };
  assert.equal(config(removeElements(mismatch, [], ['e2']), 'r').check, 'other');
  // The older For each form: a loop edge with no source handle occupies `body`.
  const legacy: Graph = { nodes: [node('each', 'for_each', { body: 'summarize' }), node('summarize', 'agent'), node('second', 'agent')],
    edges: [loop('d', 'each', 'summarize', null)] };
  assert.equal(portInUse(legacy, 'each', 'body'), true);
  assert.equal(config(removeElements(legacy, [], ['d']), 'each').body, '');
  assert.equal(config(removeElements(legacy, ['summarize'], []), 'each').body, '');
  assert.deepEqual(removeElements(legacy, ['summarize'], []).edges, []);
});

void test('U04 card statuses come from node events only', async () => {
  const { nodeStatuses } = await status();
  const throughIteration1 = recorded.events.filter((e) => (e.seq as number) <= 8);
  assert.equal(throughIteration1.at(-1)!.kind, 'loop_item');
  assert.deepEqual(nodeStatuses(throughIteration1), { input: 'success', r: 'running', writer: 'success', judge: 'success' });
  assert.equal(nodeStatuses(recorded.events).r, 'success');
  const forEach = [
    { seq: 1, node_id: 'each', status: 'running', timestamp: '' },
    { seq: 2, node_id: 'each', kind: 'loop_summary', status: 'warning', timestamp: '' },
    { seq: 3, node_id: 'each', kind: 'loop_item', status: 'info', timestamp: '' },
  ] as RunEvent[];
  assert.deepEqual(nodeStatuses(forEach), { each: 'running' });
});

void test('U05 the execution log names iterations and explains each verdict and the stop', async () => {
  const { eventTitle, eventNotes } = await status();
  const wf = recorded.workflow;
  const titles = recorded.events.map((e) => eventTitle(e, wf));
  assert.deepEqual(titles.slice(0, 8), ['input', 'input', 'r', 'writer · iteration 1', 'writer · iteration 1',
    'judge · iteration 1', 'judge · iteration 1', 'r']);
  assert.ok(titles.includes('judge · iteration 2'));
  const notes = (e: RunEvent) => eventNotes(e, wf);
  const items = recorded.events.filter((e) => e.kind === 'loop_item');
  assert.deepEqual(items.map(notes), [['Iteration 1 did not meet the stop rule.'], ['Iteration 2 met the stop rule.']]);
  const done = recorded.events.find((e) => e.node_id === 'r' && e.status === 'success')!;
  assert.deepEqual(notes(done), ['Stopped by its rule after 2 of 3 iterations.']);
  // Hand-made rows and stops.
  const item = (index: number, item_result: unknown) => ({ seq: 1, timestamp: '', kind: 'loop_item', status: 'info', node_id: 'r',
    item_index: index, item_result }) as unknown as RunEvent;
  assert.deepEqual(notes(item(2, { index: 2, status: 'interrupted', budget_halt: 'tokens' })),
    ['Iteration 3 was stopped by the run budget before it was checked.']);
  assert.deepEqual(notes(item(1, { index: 1, status: 'abstained', value: 'Not enough evidence.' })),
    ['Iteration 2 stopped: the answerability guard abstained.']);
  // The stored copy of a loop event is bounded: a large item_result becomes a string (R3).
  const bounded = '{"index": 1, "status": "success", "value": {"text": "\\"\\\\\\"\\\\… [shortened]';
  for (const unreadable of [bounded, { index: 1, status: 'success', value: 'draft' }, undefined])
    assert.deepEqual(notes(item(1, unreadable)), ['Iteration 2 finished. Verdict details are unavailable.']);
  const stop = (summary: unknown) => notes({ seq: 1, timestamp: '', node_id: 'r', status: 'success',
    outputs: { text: 'x', feedback: '', summary: typeof summary === 'string' ? summary : JSON.stringify(summary) } });
  assert.deepEqual(stop({ iterations: 3, max_iterations: 3, met: false, stopped_by: 'limit' }),
    ['Reached its limit of 3 iterations without meeting its rule. Returned the answer from iteration 3.']);
  assert.deepEqual(stop({ iterations: 2, max_iterations: 3, met: false, stopped_by: 'budget' }),
    ['The run budget stopped iteration 3 before it was checked. Returned the answer from iteration 2.']);
  assert.deepEqual(stop({ iterations: 1, max_iterations: 3, met: false, stopped_by: 'guard' }),
    ['The answerability guard stopped the loop and returned an abstention.']);
  assert.deepEqual(stop('{not json'), []);
  // A For each keeps its own titles and gets no Repeat notes.
  const each = { nodes: [node('each', 'for_each'), node('lookup', 'tool_jira')] };
  const nested = { seq: 1, timestamp: '', node_id: 'lookup', status: 'success', loop_node_id: 'each', item_index: 4 } as unknown as RunEvent;
  assert.equal(eventTitle(nested, each), 'lookup · item 5');
  assert.deepEqual(eventNotes({ ...item(0, { index: 0, status: 'success', value: 'x' }), node_id: 'each' }, each), []);
  assert.equal(eventTitle({ seq: 1, timestamp: '', status: 'failed' }, each), 'Workflow');
});

void test('U06 an incomplete Repeat is labelled by its stop rule, with its recorded reason', async () => {
  const { incompleteRun } = await status();
  const wf = { nodes: [node('r', 'repeat'), node('each', 'for_each')] };
  const limit = 'r reached 3 iterations without meeting its stop rule; the last answer is returned.';
  const view = incompleteRun({ truncated: true, truncation_source: 'confirmed', truncation_causes: [
    { node_id: 'r', kind: 'loop', source: 'confirmed', reason: limit },
    { node_id: 'each', kind: 'loop', source: 'confirmed', reason: 'each accepted 1 of 3 items.' },
    { node_id: 'r', kind: 'budget', source: 'confirmed', reason: 'r did not run: budget.' },
  ] }, wf);
  assert.deepEqual(view!.causes.map((c) => [c.node, c.label, c.reason]), [
    ['r', 'Repeat did not meet its stop rule', limit],
    ['each', 'Loop did not deliver every item', 'each accepted 1 of 3 items.'],
    ['r', 'Skipped by the action budget', 'r did not run: budget.'],
  ]);
});

void test('U07 saved handles map to drawn ports for display only', async () => {
  const { edgePort, canvasEdge, savedConnection } = await lib();
  const cases: [WorkflowEdge, string, string | null, string | null][] = [
    // edge, source type, drawn source port, drawn target port
    [loop('a', 'r', 'w', 'body'), 'repeat', 'body', null],
    [loop('b', 'r', 'j', 'check'), 'repeat', 'check', null],
    [loop('c', 'each', 'w', null), 'for_each', 'body', null],
    [loop('d', 'each', 'w', 'body'), 'for_each', 'body', null],
    [{ id: 'e', source: 'r', target: 'out' }, 'repeat', 'flow', null],
    [{ id: 'f', source: 'r', target: 'out', sourceHandle: 'output', kind: 'flow' }, 'repeat', 'flow', null],
    [{ id: 'g', source: 'each', target: 'out', sourceHandle: 'output' }, 'for_each', 'flow', null],
    [{ id: 'h', source: 'agent', target: 'out', sourceHandle: 'output', targetHandle: 'input' }, 'agent', null, null],
    [{ id: 'i', source: 'lookup', target: 'agent', kind: 'tool', sourceHandle: 'output', targetHandle: 'tools' }, 'tool_jira', null, 'tools'],
    [{ id: 'j', source: 'boss', target: 'helper', kind: 'agent', sourceHandle: 'agents', targetHandle: 'input' }, 'agent', 'agents', null],
    [{ id: 'k', source: 'reader', target: 'agent', sourceHandle: 'continue' }, 'request_reader', 'continue', null],
    [{ id: 'l', source: 'agent', target: 'out' }, 'agent', null, null],
  ];
  for (const [edge, type, sourcePort, targetPort] of cases) {
    const before = structuredClone(edge);
    assert.equal(edgePort(edge, type) ?? null, sourcePort, `${edge.id} source port`);
    const drawn = canvasEdge(edge, type);
    assert.equal(drawn.sourceHandle ?? null, sourcePort, `${edge.id} drawn source`);
    assert.equal(drawn.targetHandle ?? null, targetPort, `${edge.id} drawn target`);
    assert.deepEqual([drawn.id, drawn.source, drawn.target, drawn.kind], [edge.id, edge.source, edge.target, edge.kind]);
    assert.deepEqual(edge, before, `${edge.id} is not changed`);
  }
  assert.equal(savedConnection({ source: 'r', target: 'out', sourceHandle: 'flow', targetHandle: null }, 'repeat').sourceHandle, null);
  assert.equal(savedConnection({ source: 'each', target: 'out', sourceHandle: 'flow', targetHandle: null }, 'for_each').sourceHandle, null);
  assert.equal(savedConnection({ source: 'r', target: 'w', sourceHandle: 'body', targetHandle: null }, 'repeat').sourceHandle, 'body');
  assert.equal(savedConnection({ source: 'reader', target: 'r', sourceHandle: 'continue', targetHandle: null }, 'request_reader').sourceHandle, 'continue');
});

void test('U08 unfinished JSON in the stop rule blocks Validate and Run locally', async () => {
  const { unparsedJsonSettings } = await import('../lib/workflow.ts');
  const workflow = (until: unknown, type = 'repeat'): Workflow => ({ version: 1, contract: 2, name: 'x', description: '', edges: [],
    nodes: [node('r', type, { body: 'w', check: '', until })] });
  const rule = { operator: 'in', field: 'tier', options: ['gold'] };
  assert.deepEqual(unparsedJsonSettings(workflow(rule), catalog), []);
  assert.deepEqual(unparsedJsonSettings(workflow({ ...rule, options: '["gold",' }), catalog), [
    { node_id: 'r', message: 'Stop rule Options is not valid JSON yet. Fix it before validating or running.' },
  ]);
  assert.deepEqual(unparsedJsonSettings(workflow(undefined), catalog), []);
});

// Review of 7c4aa62, finding 1: the loop port names are ordinary route ids on other cards. A
// Switch case may be named body, check, flow or output (backend K03), and its edge keeps it.
// The same holds for agents, the Agent card's specialists port (open after that review).
void test('U09 Switch case ids named like editor ports connect, save and draw unchanged', async () => {
  const { connectionKind, edgePort, canvasEdge, savedConnection } = await lib();
  for (const id of ['body', 'check', 'flow', 'agents', 'output']) {
    assert.equal(connectionKind('switch', 'response', id, null), 'flow', `${id} connects`);
    assert.equal(connectionKind('switch', 'agent', id, null), 'flow', `${id} connects to an Agent`);
    assert.equal(savedConnection({ source: 'route', target: 'out', sourceHandle: id, targetHandle: null }, 'switch').sourceHandle, id, `${id} is saved`);
    const saved: WorkflowEdge = { id: `to_${id}`, source: 'route', target: 'out', sourceHandle: id, kind: 'flow' };
    assert.equal(edgePort(saved, 'switch'), id, `${id} occupies its own route`);
    assert.equal(canvasEdge(saved, 'switch').sourceHandle, id, `${id} is drawn at its own route`);
  }
  // The loop cards keep their mapping.
  assert.equal(connectionKind('repeat', 'agent', 'body', null), 'loop');
  assert.equal(connectionKind('for_each', 'agent', 'check', null), null);
  assert.equal(edgePort({ id: 'o', source: 'r', target: 'out', sourceHandle: 'output' }, 'repeat'), 'flow');
  assert.equal(edgePort({ id: 'o', source: 'a', target: 'out', sourceHandle: 'output' }, 'agent'), null);
  // The Agent card keeps its specialists port: Agent to Agent only.
  assert.equal(connectionKind('agent', 'agent', 'agents', null), 'agent');
  assert.equal(connectionKind('agent', 'response', 'agents', null), null);
});

// Review of 7c4aa62, finding 2: only a real attachment setting is cleared or set: body on a
// For each, body or check on a Repeat. A malformed loop edge names some other setting.
void test('U10 removing or adding a malformed loop edge leaves the other settings unchanged', async () => {
  const { removeElements, withLoopAttachment } = await lib();
  const settings = { body: 'writer', check: '', max_iterations: 3, on_limit: 'last', until: { operator: 'eq', field: 'pass', compare_to: 'true' } };
  const graph: Graph = {
    nodes: [node('r', 'repeat', settings), node('writer', 'agent'), node('last', 'agent')],
    edges: [loop('b', 'r', 'writer', 'body'), loop('m', 'r', 'last', 'on_limit')],
  };
  const removed = removeElements(graph, [], ['m']);
  assert.deepEqual(removed.edges.map((e) => e.id), ['b']);
  assert.deepEqual(config(removed, 'r'), settings, 'on_limit stays "last"');
  assert.deepEqual(config(removeElements(graph, ['last'], []), 'r'), settings, 'removing the node keeps on_limit');
  assert.equal(config(removeElements(graph, [], ['b']), 'r').body, '', 'a real attachment is still cleared');
  // For each has no check setting: a loop edge named check clears nothing.
  const each: Graph = {
    nodes: [node('each', 'for_each', { body: 'summarize', check: 'judge' }), node('summarize', 'agent'), node('judge', 'agent')],
    edges: [loop('d', 'each', 'summarize', 'body'), loop('x', 'each', 'judge', 'check')],
  };
  assert.deepEqual(config(removeElements(each, [], ['x']), 'each'), { body: 'summarize', check: 'judge' });
  assert.equal(config(removeElements(each, [], ['d']), 'each').body, '');
  // Attaching follows the same rule: a malformed port sets nothing, and the edge is still added.
  const added = withLoopAttachment({ ...graph, edges: [] }, loop('n', 'r', 'last', 'max_iterations'));
  assert.deepEqual(config(added, 'r'), settings);
  assert.deepEqual(added.edges.map((e) => e.id), ['n']);
});
