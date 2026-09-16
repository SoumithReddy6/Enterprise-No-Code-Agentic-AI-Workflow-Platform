import test from 'node:test';
import assert from 'node:assert/strict';
import { sourcesForRun } from '../lib/knowledge.ts';
import type { Workflow } from '../lib/workflow.ts';

const node = (id: string, type: string) => ({
  id,
  type,
  version: 1 as const,
  label: '',
  position: { x: 0, y: 0 },
  inputs: {},
  config: {},
});
const passage = (cited: boolean) => ({
  id: 'chunk1',
  knowledge_base_id: 'kb1',
  version: 1,
  document_id: 'doc1',
  filename: 'facts.txt',
  page: 1,
  text: 'Fact',
  score: 0.5,
  citation: 'S1',
  cited,
});
const workflow: Workflow = {
  version: 1,
  name: 'Grounded',
  description: '',
  nodes: [node('retrieve', 'retrieve'), node('agent', 'agent')],
  edges: [],
};

void test('agent citations mark retrieved passages as cited without duplicating cards', () => {
  const events = [
    {
      status: 'success',
      node_id: 'retrieve',
      outputs: { sources: JSON.stringify([passage(false)]) },
      seq: 1,
      timestamp: '',
    },
    {
      status: 'success',
      node_id: 'agent',
      outputs: { sources: JSON.stringify([passage(true)]) },
      seq: 2,
      timestamp: '',
    },
  ];
  const sources = sourcesForRun({ workflow }, events);
  assert.equal(sources.length, 1);
  assert.equal(sources[0].cited, true);
  // An agent without evidence reports no sources; the retrieved card stays "Retrieved only".
  const retrievedOnly = sourcesForRun({ workflow }, [
    events[0],
    { ...events[1], outputs: { sources: '[]' } },
  ]);
  assert.equal(retrievedOnly.length, 1);
  assert.equal(retrievedOnly[0].cited, false);
});
