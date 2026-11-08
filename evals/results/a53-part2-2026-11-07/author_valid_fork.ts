// Authors the "valid" graph of frontend/tests/browser/parallel-issues.json once (A5.3 Part 2).
// It applies the editor's own steps to the default Q&A draft: the draft gets the first
// enabled model (loadDocument), two Agents are added with addNode's defaults and that model,
// each is connected after the draft Agent (flowBinding gives agent.text), both end at a Join,
// the Agent → Response edge is removed and the Response binds Branch B → text.
// Run from frontend/: node --experimental-strip-types ../evals/results/a53-part2-2026-11-07/author_valid_fork.ts
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequestWorkflow } from '../../../frontend/lib/request-routing.ts';
import { modelConfig } from '../../../frontend/lib/models.ts';
import type { Definition, Workflow, WorkflowNode } from '../../../frontend/lib/workflow.ts';

const catalog = JSON.parse(readFileSync('tests/browser/request-nodes.json', 'utf8')) as Definition[];
const model = { id: 'model-tiny', provider: 'ollama', model: 'tiny:latest', credential_id: '', enabled: true };
const draft = createRequestWorkflow('qa');
const nodes: WorkflowNode[] = draft.nodes.map((n) =>
  ['agent', 'request_router', 'request_reader'].includes(n.type) ? { ...n, config: modelConfig(n.config, model) } : n);
const added = (id: string, type: string, label: string, x: number, y: number): WorkflowNode => {
  const def = catalog.find((d) => d.type === type)!;
  const config = Object.fromEntries(Object.entries(def.config_schema.properties)
    .filter(([, p]) => p.default !== undefined && p.default !== null).map(([k, p]) => [k, p.default]));
  return { id, type, version: 1, label, position: { x, y },
    inputs: Object.fromEntries(Object.keys(def.inputs).map((k) => [k, ''])),
    config: type === 'agent' ? modelConfig(config, model) : config };
};
const branchA = added('branchA', 'agent', 'Branch A', 120, 550);
const branchB = added('branchB', 'agent', 'Branch B', 480, 550);
branchA.inputs = { input: 'agent.text' };
branchB.inputs = { input: 'agent.text' };
const join = added('join', 'join', 'Join', 300, 720);
const response = nodes.find((n) => n.id === 'response')!;
response.position = { x: 300, y: 890 };
response.inputs = { text: 'branchB.text' };
const valid: Workflow = {
  ...draft,
  nodes: [...nodes, branchA, branchB, join],
  edges: [
    ...draft.edges.filter((e) => e.id !== 'agent-response'),
    { id: 'agent-branchA', source: 'agent', target: 'branchA' },
    { id: 'agent-branchB', source: 'agent', target: 'branchB' },
    { id: 'branchA-join', source: 'branchA', target: 'join' },
    { id: 'branchB-join', source: 'branchB', target: 'join' },
    { id: 'join-response', source: 'join', target: 'response' },
  ],
};
const path = 'tests/browser/parallel-issues.json';
let current: Record<string, unknown> = {};
try { current = JSON.parse(readFileSync(path, 'utf8')); } catch { /* first authoring */ }
writeFileSync(path, JSON.stringify({ model, valid, broken: current.broken ?? [] }, null, 1) + '\n');
