import test from 'node:test';
import assert from 'node:assert/strict';
import { incompleteRun, eventNotes } from '../lib/run-status.ts';

void test('a complete run shows nothing', () => {
  assert.equal(incompleteRun({ truncated: false }), null);
  assert.equal(incompleteRun(undefined), null);
});

void test('each kind of cause is named with its node and reason', () => {
  const view = incompleteRun({
    truncated: true,
    truncation_source: 'confirmed',
    truncation_causes: [
      { node_id: 'each', kind: 'loop', source: 'confirmed', reason: 'each accepted 1 of 3 items (max_items 1); 2 did not run.' },
      { node_id: 'agent', kind: 'agent', source: 'confirmed', reason: 'Agent step budget exhausted.' },
      { node_id: 'tickets', kind: 'tool', source: 'confirmed', reason: 'tickets: Jira returned 100 of 200 matching issues.' },
      { node_id: 'second', kind: 'budget', source: 'confirmed', reason: 'second did not run: Run-wide agent action budget exhausted.' },
    ],
  });
  assert.ok(view);
  assert.equal(view.title, 'Incomplete run');
  assert.equal(view.unverified, false);
  assert.match(view.certainty, /^Confirmed/);
  assert.deepEqual(
    view.causes.map((c) => [c.node, c.label]),
    [
      ['each', 'Loop did not deliver every item'],
      ['agent', 'Agent answer is incomplete'],
      ['tickets', 'Tool returned capped results'],
      ['second', 'Skipped: the action budget was exhausted'],
    ],
  );
  assert.equal(view.causes[2].reason, 'tickets: Jira returned 100 of 200 matching issues.');
});

void test('legacy uncertainty is distinguished and listed after confirmed causes', () => {
  const mixed = incompleteRun({
    truncated: true,
    truncation_source: 'confirmed',
    truncation_causes: [
      { node_id: 'old', kind: 'loop', source: 'legacy_checkpoint_unverified', reason: 'Loop completeness could not be fully verified.' },
      { node_id: 'tickets', kind: 'tool', source: 'confirmed', reason: 'capped' },
    ],
  });
  assert.ok(mixed);
  assert.deepEqual(mixed.causes.map((c) => [c.node, c.unverified]), [['tickets', false], ['old', true]]);
  const legacyOnly = incompleteRun({
    truncated: true,
    truncation_source: 'legacy_checkpoint_unverified',
    truncation_causes: [{ node_id: 'old', kind: 'loop', source: 'legacy_checkpoint_unverified', reason: 'unverified' }],
  });
  assert.ok(legacyOnly);
  assert.equal(legacyOnly.unverified, true);
  assert.match(legacyOnly.certainty, /^Unverified: this run was checkpointed by an older Relay version/);
});

void test('runs recorded before causes existed still explain themselves', () => {
  const view = incompleteRun({ truncated: true, truncation_reason: 'Agent work was incomplete.' });
  assert.ok(view);
  assert.deepEqual(view.causes, [{ node: '', label: 'Work was incomplete', unverified: false, reason: 'Agent work was incomplete.' }]);
});

void test('event notes explain skipped, capped, compacted and previewed entries', () => {
  assert.deepEqual(eventNotes({ status: 'not_run', reason: 'Run-wide agent action budget exhausted (3 actions).' }), [
    'Skipped: Run-wide agent action budget exhausted (3 actions).',
  ]);
  assert.deepEqual(eventNotes({ status: 'failed', budget_exhausted: true, recovered: true }), [
    'Recovered: refused by the action budget, so its work is missing from this run.',
  ]);
  assert.deepEqual(
    eventNotes({ status: 'success', items_truncated: true, outputs: { _truncation: { truncation_reason: 'tickets: Jira returned 100 of 200.' } } }),
    ['Incomplete result: tickets: Jira returned 100 of 200.'],
  );
  assert.deepEqual(eventNotes({ status: 'success', items_truncated: true }), ['Jira items are incomplete (result limit or additional pages).']);
  assert.deepEqual(eventNotes({ status: 'success', outputs: { results: { stored_in: 'run_loop_items', count: 100, sha256: 'x' } } }), [
    "100 results, stored per item; open the run's loop results for each one.",
  ]);
  assert.deepEqual(
    eventNotes({ status: 'success', outputs: { results: { stored_in: 'run_loop_items', count: 3, unavailable: 'Loop each cannot be restored.' } } }),
    ['Loop each cannot be restored.'],
  );
  assert.deepEqual(eventNotes({ status: 'success', preview_of: ['outputs.text'], preview_omitted: 2, omitted_fields: ['inputs'] }), [
    '3 field(s) in this log entry are previews; the run keeps the full values.',
    'Omitted from this log entry to keep it small: inputs.',
  ]);
  assert.deepEqual(eventNotes({ status: 'success', outputs: { results: [] } }), []);
});
