import test from 'node:test';
import assert from 'node:assert/strict';
import { trustSummary, type RunEvent } from '../lib/workflow.ts';

const event = (extra: Partial<RunEvent>): RunEvent => ({ seq: 1, status: 'success', timestamp: '', node_id: 'n', ...extra });

void test('a node without labels has no trust summary', () => {
  assert.equal(trustSummary(undefined), null);
  assert.equal(trustSummary(event({ outputs: { text: 'x' } })), null);
});

void test("a structured answer is summarised by its fields, other nodes by their ports", () => {
  const extract = event({ outputs: { text: '{}', provider: 'ollama', _provenance: { v: 1,
    ports: { text: 'guessed', provider: 'source' }, fields: { text: { item: 'quoted', total: 'absent' } } } } });
  assert.deepEqual(trustSummary(extract), { values: [
    { name: 'item', label: 'quoted' }, { name: 'total', label: 'absent' }, { name: 'provider', label: 'source' }] });
  const unknown = event({ outputs: { _provenance: { v: 1, ports: { text: 'trusted-by-me' } } } });
  assert.deepEqual(trustSummary(unknown), { values: [] }, 'labels the backend never sends are ignored');
});

void test('a decision is explained in plain words', () => {
  const review = trustSummary(event({ outputs: { branch: 'false', _provenance: { v: 1, ports: { branch: 'guessed' } } },
    decision: { label: 'guessed', would_review: true, reason: 'guessed' } }));
  assert.equal(review?.decision?.explanation, 'A person would need to decide: the value was not found in the input.');
  const automatic = trustSummary(event({ decision: { label: 'calculated', would_review: false } }));
  assert.equal(automatic?.decision?.explanation, 'Decided automatically on a value that is calculated in code.');
  const failed = trustSummary(event({ decision: { label: 'calculated', would_review: true, reason: 'check_failed', passed: false } }));
  assert.equal(failed?.decision?.explanation, 'A person would need to decide: the values disagree.');
});
