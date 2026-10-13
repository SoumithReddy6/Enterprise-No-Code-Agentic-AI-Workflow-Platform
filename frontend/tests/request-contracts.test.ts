import test from 'node:test';
import assert from 'node:assert/strict';
import { controlRoutes, inputPorts, setInputBinding, sameExecutionGraph, seed, type Definition } from '../lib/workflow.ts';

const definition: Definition = {
  type: 'test_control', name: 'Test control', category: 'Control', description: '',
  inputs: { message: 'string' }, optional_inputs: { reference: 'object' },
  outputs: { branch: 'string', text: 'string' }, branch_routes: ['continue', 'reply'],
  config_schema: { properties: {} },
};

void test('handles come from the registry for any control type', () => {
  assert.deepEqual(controlRoutes(definition), ['continue', 'reply']);
  assert.deepEqual(controlRoutes({ ...definition, branch_routes: [] }), []);
  assert.deepEqual(controlRoutes({ ...definition, branch_routes: undefined }), []);
});

void test('optional inputs appear but clearing them removes the binding', () => {
  assert.deepEqual(inputPorts(definition), { message: 'string', reference: 'object' });
  const bindings = { message: 'input.message', reference: 'input.message' };
  assert.deepEqual(setInputBinding(definition, bindings, 'reference', ''), { message: 'input.message' });
  assert.deepEqual(setInputBinding(definition, bindings, 'message', ''), { ...bindings, message: '' });
  assert.deepEqual(bindings, { message: 'input.message', reference: 'input.message' });
});

void test('contract changes invalidate execution highlighting; omitted contract is one', () => {
  const { contract: _contract, ...legacy } = seed;
  assert.equal(sameExecutionGraph(legacy, { ...legacy, contract: 1 }), true);
  assert.equal(sameExecutionGraph(legacy, { ...legacy, contract: 2 }), false);
});
