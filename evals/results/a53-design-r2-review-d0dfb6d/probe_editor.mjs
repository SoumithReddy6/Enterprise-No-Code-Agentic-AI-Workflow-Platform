import assert from 'node:assert/strict';
import { writeFileSync } from 'node:fs';
import { bindableOutputs, paletteCategory } from '../../../frontend/lib/workflow.ts';

const types = { input: 'chat_input', fork: 'prompt', a: 'prompt', b: 'prompt', join: 'join', out: 'response' };
const nodes = Object.entries(types).map(([id, type]) => ({ id, type, inputs: {}, config: {} }));
const pairs = [['input', 'fork'], ['fork', 'a'], ['fork', 'b'], ['a', 'join'], ['b', 'join'], ['join', 'out']];
const edges = pairs.map(([source, target], i) => ({ id: String(i), source, target }));
const outputs = (type) => ({ chat_input: ['message'], prompt: ['text'], response: ['text'], join: [] }[type] ?? []);
const actual = bindableOutputs({ nodes, edges }, outputs).out;
assert.equal(paletteCategory('join'), null);
assert.equal(actual.includes('a.text'), false);
assert.equal(actual.includes('b.text'), false);
const result = {
  joinHiddenFromPalette: true,
  currentEditorBindableAfterJoin: actual,
  requiredByBackendDesign: ['input.message', 'fork.text', 'a.text', 'b.text'],
  missingBranchBindings: ['a.text', 'b.text'],
  scope: 'Current helper on a proposed Join graph; backend parallel implementation does not yet exist.',
};
writeFileSync(new URL('./editor-results.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result));
