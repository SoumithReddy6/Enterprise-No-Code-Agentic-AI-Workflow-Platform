// Remaining R02 boundary: a high/critical package cannot silently pass because
// its sole embedded advisory is marked moderate. Metadata is complete and
// counts the package severity correctly. Run from any directory with Node.
import { evaluate, reportProblems, summary } from '../../../frontend/scripts/audit-gate-core.mjs';

const probes = ['moderate', 'high', 'critical'].map((severity) => {
  const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 1 };
  counts[severity] = 1;
  const report = {
    auditReportVersion: 2,
    vulnerabilities: {
      other: {
        name: 'other', severity,
        via: [{ name: 'other', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc', severity: 'moderate', range: '<2.0.0' }],
      },
    },
    metadata: { vulnerabilities: counts },
  };
  const result = evaluate({ report, trees: {}, published: {}, exceptions: [], today: '2026-10-05', commands: [{ command: 'npm audit', status: 1, total: 1 }] });
  const expected_ok = severity === 'moderate';
  return { severity, expected_ok, actual_ok: result.ok, policy_met: result.ok === expected_ok, structural_errors: reportProblems(report), failures: result.failures, output: summary(result, counts) };
});
const policy_met = probes.every((p) => p.policy_met);
console.log(JSON.stringify({ policy_met, probes }, null, 2));
process.exit(policy_met ? 0 : 1);
