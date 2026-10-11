// Run after probe_phases.py, with Node's TypeScript stripping enabled if needed.
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { trustSummary } from '../../../frontend/lib/workflow.ts';

const data = JSON.parse(readFileSync(new URL('./probe-results.json', import.meta.url), 'utf8'));
const row = data.cases.find((entry) => entry.case === 'R09_cached_decision_lost');
assert.ok(row?.defect_reproduced);
const cached = row.observed.cached_event;
const original = { ...cached, cached: false, decision: row.observed.original_decision };
const before = trustSummary(original);
const after = trustSummary(cached);
assert.equal(before.decision.would_review, true);
assert.match(before.decision.explanation, /A person would need to decide/);
assert.equal(after.decision, undefined);
assert.deepEqual(before.values, after.values);
const result = { case: 'R09_frontend_cached_explanation_lost', defect_reproduced: true, before, after };
writeFileSync(new URL('./frontend-probe-results.json', import.meta.url), JSON.stringify(result, null, 2) + '\n');
console.log('R09: the real trustSummary helper drops the decision explanation on cached replay.');
