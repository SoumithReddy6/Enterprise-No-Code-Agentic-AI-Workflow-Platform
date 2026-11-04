// Read-only adversarial probes for dded39d's security gate. Exit 1 means the
// stated fail-closed policy is not met. Run from any directory with Node.
import { readFileSync } from 'node:fs';
import { evaluate, summary } from '../../../frontend/scripts/audit-gate-core.mjs';

const { exceptions } = JSON.parse(readFileSync(new URL('../../../frontend/audit-exceptions.json', import.meta.url), 'utf8'));
let dependencies = {};
for (const step of [...exceptions[0].paths[0]].reverse()) {
  const at = step.lastIndexOf('@');
  dependencies = { [step.slice(0, at)]: { version: step.slice(at + 1), dependencies } };
}
const advisory = { name: 'braces', url: 'https://github.com/advisories/GHSA-vfj7-8cjw-p6xm', severity: 'high', range: '<=3.0.3' };
const report = {
  auditReportVersion: 2,
  vulnerabilities: { braces: { name: 'braces', severity: 'high', via: [advisory] } },
  metadata: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 1, critical: 0, total: 1 } },
};
const base = { report, trees: { braces: { dependencies } }, published: { braces: ['3.0.3'] }, exceptions, today: '2026-10-05' };
const cases = [
  ['reviewed control', true, {}],
  ['high count with missing findings', false, { report: { ...report, vulnerabilities: {} } }],
  ['unaccepted high advisory without severity', false, { report: { ...report, vulnerabilities: { other: { name: 'other', severity: 'high', via: [{ name: 'other', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc', range: '<2.0.0' }] } } } }],
  ['critical finding with numeric via', false, { report: { ...report, vulnerabilities: { other: { name: 'other', severity: 'critical', via: [42] } }, metadata: { vulnerabilities: { ...report.metadata.vulnerabilities, high: 0, critical: 1 } } } }],
  ['arrays replacing report maps', false, { report: { vulnerabilities: [], metadata: { vulnerabilities: [] } } }],
  ['empty published versions', false, { published: { braces: [] } }],
  ['invalid published versions', false, { published: { braces: ['not-a-version'] } }],
  ['dependency tree reports problems', false, { trees: { braces: { dependencies, problems: ['invalid: braces@3.0.3'] } } }],
];
const probes = cases.map(([name, expected, changes]) => {
  const input = { ...base, ...changes };
  const result = evaluate(input);
  const intended = {
    'high count with missing findings': /metadata reports 1 high but the report lists 0/,
    'unaccepted high advisory without severity': /incomplete advisory record/,
    'critical finding with numeric via': /invalid via entry/,
    'arrays replacing report maps': /vulnerabilities is missing or not an object/,
    'empty published versions': /published versions of braces could not be checked/,
    'invalid published versions': /published versions of braces include invalid entries/,
    'dependency tree reports problems': /installed tree cannot be verified: npm ls: invalid/,
  }[name];
  const intended_reason = intended ? intended.test(result.failures.join('\n')) : result.failures.length === 0;
  return { name, expected_ok: expected, actual_ok: result.ok, policy_met: result.ok === expected && intended_reason, intended_reason, failures: result.failures, output: summary(result, input.report.metadata.vulnerabilities) };
});
const policy_met = probes.every((p) => p.policy_met);
console.log(JSON.stringify({ policy_met, probes }, null, 2));
process.exit(policy_met ? 0 : 1);
