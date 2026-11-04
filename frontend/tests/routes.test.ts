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

void test('the editor keeps a case handle while its rule is being edited', () => {
  // Deliberate: the backend refuses the invalid rule at validation, but the connection survives.
  const definition = catalog.find((d) => d.type === 'switch');
  const node = { type: 'switch', config: { cases: [{ id: 'case_1', label: 'Billing', when: { operator: 'gt' } }] } };
  assert.deepEqual(nodeRoutes(definition, node), ['case_1', 'default']);
});

void test('the shared fixture covers configured Switch routes, not only static ones', () => {
  const sizes = cases.filter((c) => c.node.type === 'switch').map((c) => c.routes.length);
  for (const size of [2, 4, 16, 0]) assert.ok(sizes.includes(size), String(size));
});
