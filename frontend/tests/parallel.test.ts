import test from 'node:test';
import assert from 'node:assert/strict';
import { paletteCategory, type WorkflowEdge } from '../lib/workflow.ts';

// A5.3 Part 2 (docs/superpowers/specs/2026-11-07-parallel-branches-editor-design.md).

void test('U01 the Join is offered in the Control group of the palette', () => {
  assert.equal(paletteCategory('join'), 'Control');
});

void test('U02 a Join card counts distinct sources of its normal flow arrivals', async () => {
  // Loaded here, so the file still runs where the helper does not exist yet.
  const { joinArrivals } = (await import('../lib/workflow.ts')) as unknown as {
    joinArrivals: (edges: WorkflowEdge[], join: string) => number;
  };
  const branches: WorkflowEdge[] = [
    { id: 'a', source: 'branchA', target: 'join' },
    { id: 'b', source: 'branchB', target: 'join', kind: 'flow' },
  ];
  assert.equal(joinArrivals(branches, 'join'), 2);
  const noise: WorkflowEdge[] = [
    { id: 'error', source: 'branchC', target: 'join', sourceHandle: 'error' },
    { id: 'tool', source: 'lookup', target: 'join', kind: 'tool', targetHandle: 'tools' },
    { id: 'other', source: 'branchD', target: 'after' },
    { id: 'again', source: 'branchA', target: 'join' },
  ];
  assert.equal(joinArrivals([...branches, ...noise], 'join'), 2);
  assert.equal(joinArrivals([branches[0]], 'join'), 1);
});
