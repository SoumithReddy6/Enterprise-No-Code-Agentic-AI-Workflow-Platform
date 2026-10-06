import test from 'node:test';
import assert from 'node:assert/strict';
import { conditionSettingVisible, withConditionOperator } from '../lib/workflow.ts';

const SETTINGS = ['operator', 'field', 'contains', 'compare_to', 'options', 'compare_as', 'case_sensitive'];
const shown = (config: Record<string, unknown>) => SETTINGS.filter((key) => conditionSettingVisible(config, key));

void test('a saved legacy condition shows exactly its original settings plus operator and field', () => {
  assert.deepEqual(shown({ contains: 'refund' }), ['operator', 'field', 'contains', 'case_sensitive']);
});

void test('each operator shows only the settings it uses', () => {
  assert.deepEqual(shown({ operator: 'gt' }), ['operator', 'field', 'compare_to']);
  assert.deepEqual(shown({ operator: 'eq' }), ['operator', 'field', 'compare_to', 'compare_as', 'case_sensitive']);
  assert.deepEqual(shown({ operator: 'in' }), ['operator', 'field', 'options', 'compare_as', 'case_sensitive']);
  assert.deepEqual(shown({ operator: 'empty' }), ['operator', 'field']);
});

void test('switching operator drops the previous operand and an inapplicable compare_as', () => {
  const legacy = { contains: 'refund', case_sensitive: true, field: 'note' };
  assert.deepEqual(withConditionOperator(legacy, 'gt'), { operator: 'gt', case_sensitive: true, field: 'note' });
  const numericIn = { operator: 'in', options: ['1', '2'], compare_as: 'number', field: '' };
  assert.deepEqual(withConditionOperator(numericIn, 'eq'), { operator: 'eq', compare_as: 'number', field: '' });
  assert.deepEqual(withConditionOperator(numericIn, 'empty'), { operator: 'empty', field: '' });
  assert.deepEqual(withConditionOperator({ operator: 'gt', compare_to: '5' }, 'lte'), { operator: 'lte', compare_to: '5' });
  assert.deepEqual(numericIn.options, ['1', '2'], 'the original config is not mutated');
});
