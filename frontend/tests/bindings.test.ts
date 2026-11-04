import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bindableOutputs, type WorkflowEdge, type WorkflowNode } from '../lib/workflow.ts';

// Generated from the backend rule by backend/tests/test_binding_contract.py, which also checks
// that every listed binding validates and every other one is rejected.
const fixture = JSON.parse(readFileSync(new URL('./fixtures/bindable-outputs.json', import.meta.url), 'utf8')) as {
  outputs: Record<string, string[]>;
  cases: Record<string, { workflow: { nodes: WorkflowNode[]; edges: WorkflowEdge[] }; bindable: Record<string, string[]> }>;
};
const outputs = (type: string) => fixture.outputs[type] ?? [];

for (const [name, { workflow, bindable }] of Object.entries(fixture.cases)) {
  void test(`the editor offers the bindings the backend accepts: ${name}`, () => {
    assert.deepEqual(bindableOutputs(workflow, outputs), bindable);
  });
}

void test('a node after a condition can bind the agent before it, not the other branch', () => {
  const { workflow } = fixture.cases.condition_branches;
  const result = bindableOutputs(workflow, outputs);
  assert.ok(result.auto_note.includes('extract.text'));
  assert.ok(!result.auto.includes('review_note.text'));
});
