import test from 'node:test';
import assert from 'node:assert/strict';
import {
  addCase, caseEdges, defaultSwitchConfig, MAX_CASES, moveCase, newCaseId, removeCase,
  routeLabel, switchCases, unreachableCases, updateCase,
} from '../lib/switch.ts';
import { nodeRoutes, paletteCategory, type Definition, type WorkflowEdge } from '../lib/workflow.ts';
import { readFileSync } from 'node:fs';

const catalog: Definition[] = JSON.parse(readFileSync(new URL('./browser/request-nodes.json', import.meta.url), 'utf8'));
const definition = catalog.find((d) => d.type === 'switch')!;
const when = (compare_to: string, extra: Record<string, unknown> = {}) => ({ field: 'category', operator: 'eq', compare_to, ...extra });
const config = {
  cases: [
    { id: 'case_billing', label: 'Billing', when: when('billing') },
    { id: 'case_tech', label: 'Technical', when: when('technical') },
    { id: 'case_acct', label: 'Account', when: when('account') },
  ],
};
const edge = (id: string, sourceHandle: string): WorkflowEdge => ({ id, source: 'route', target: 'out_' + id, sourceHandle });

void test('the Switch is offered under Control', () => {
  assert.equal(paletteCategory('switch'), 'Control');
});

void test('generated ids are valid route handles, unique and never a label', () => {
  const ids = new Set<string>();
  for (let i = 0; i < 200; i += 1) {
    const id = newCaseId(ids);
    assert.match(id, /^case_[a-z0-9]{6}$/);
    assert.ok(!ids.has(id));
    ids.add(id);
  }
  // A collision is retried, never reused.
  let calls = 0;
  const repeat = () => (calls++ < 6 ? 0 : 0.5);
  assert.notEqual(newCaseId(['case_aaaaaa'], repeat), 'case_aaaaaa');
});

void test('a new Switch has one case that already draws its handle and Default', () => {
  const fresh = defaultSwitchConfig();
  assert.equal(fresh.cases.length, 1);
  assert.deepEqual(nodeRoutes(definition, { type: 'switch', config: fresh }), [fresh.cases[0].id, 'default']);
});

void test('renaming, editing and moving a case keep every id and route', () => {
  let next = updateCase(config, 'case_tech', { label: 'Tech support', when: when('tech') });
  next = moveCase(next, 'case_acct', -1);
  next = moveCase(next, 'case_billing', 1);
  // billing, tech, acct → acct up → billing, acct, tech → billing down → acct, billing, tech
  assert.deepEqual(switchCases(next).map((c) => c.id), ['case_acct', 'case_billing', 'case_tech']);
  assert.deepEqual(new Set(nodeRoutes(definition, { type: 'switch', config: next })), new Set(['case_billing', 'case_tech', 'case_acct', 'default']));
  assert.equal(switchCases(next).find((c) => c.id === 'case_tech')!.label, 'Tech support');
  // Moving past either end changes nothing.
  assert.equal(moveCase(config, 'case_billing', -1), config);
  assert.equal(moveCase(config, 'case_acct', 1), config);
});

void test('cases can be added up to the limit and never past it', () => {
  let next: Record<string, unknown> = { cases: [] };
  for (let i = 0; i < MAX_CASES + 3; i += 1) next = addCase(next);
  assert.equal(switchCases(next).length, MAX_CASES);
  assert.equal(new Set(switchCases(next).map((c) => c.id)).size, MAX_CASES);
});

void test('removing a connected case removes exactly its own connection', () => {
  const edges = [edge('b', 'case_billing'), edge('t', 'case_tech'), edge('d', 'default'),
    { id: 'other', source: 'elsewhere', target: 'x', sourceHandle: 'case_tech' }];
  assert.deepEqual(caseEdges(edges, 'route', 'case_tech').map((e) => e.id), ['t']);
  const result = removeCase(config, edges, 'route', 'case_tech');
  assert.deepEqual(switchCases(result.config).map((c) => c.id), ['case_billing', 'case_acct']);
  assert.deepEqual(result.edges.map((e) => e.id), ['b', 'd', 'other']);
  // The last case cannot be removed: a Switch needs at least one.
  const single = { cases: [config.cases[0]] };
  assert.deepEqual(removeCase(single, edges, 'route', 'case_billing'), { config: single, edges });
});

void test('only provable duplicate rules are reported as unreachable', () => {
  const cases = (...rules: Record<string, unknown>[]) => ({ cases: rules.map((w, i) => ({ id: `c${i}`, label: `C${i}`, when: w })) });
  // Same rule, with and without the default settings written out.
  assert.deepEqual([...unreachableCases(cases(when('a'), when('a', { compare_as: 'text', case_sensitive: false })))], [[1, 0]]);
  assert.deepEqual([...unreachableCases(cases(when('a'), when('b'), when('a')))], [[2, 0]]);
  // Different operator, field, case sensitivity or spacing: not provably the same.
  assert.deepEqual([...unreachableCases(cases(when('a'), when('a', { case_sensitive: true })))], []);
  assert.deepEqual([...unreachableCases(cases(when('a'), when('a ')))], []);
  assert.deepEqual([...unreachableCases(cases(when('a'), { ...when('a'), field: 'other' }))], []);
  assert.deepEqual([...unreachableCases(cases({ operator: 'empty' }, { operator: 'empty', field: '' }))], [[1, 0]]);
});

void test('handles show case labels and Default, not internal ids', () => {
  assert.equal(routeLabel(config, 'case_tech'), 'Technical');
  assert.equal(routeLabel(config, 'default'), 'Default');
  assert.equal(routeLabel(config, 'case_unknown'), 'case_unknown');
});
