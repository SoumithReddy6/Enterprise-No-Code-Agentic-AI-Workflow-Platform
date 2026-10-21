import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { nodeRoutes, isControl, type Definition, type WorkflowNode } from '../lib/workflow.ts';

// Both files come from the backend registry; backend/tests/test_route_contract.py checks
// the route fixture against node_routes, and test_request_examples checks the catalog.
const catalog: Definition[] = JSON.parse(readFileSync(new URL('./browser/request-nodes.json', import.meta.url), 'utf8'));
const cases: { node: WorkflowNode; routes: string[] }[] = JSON.parse(
  readFileSync(new URL('./fixtures/node-routes.json', import.meta.url), 'utf8'),
);

void test('the editor draws the same routes as the backend for every fixture node', () => {
  assert.ok(cases.length > 0);
  for (const { node, routes } of cases)
    assert.deepEqual(nodeRoutes(catalog.find((d) => d.type === node.type), node), routes, node.type);
});

void test('a configured-route type with unknown settings draws no invented handles', () => {
  const definition = { type: 'test_routed', configurable_routes: true } as Definition;
  assert.deepEqual(nodeRoutes(definition, { type: 'test_routed', config: {} }), []);
  assert.equal(isControl(definition), true);
  assert.equal(isControl(catalog.find((d) => d.type === 'agent')), false);
});
