import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { evaluate, summary, satisfies, dependencyPaths } from '../scripts/audit-gate-core.mjs';

const { exceptions } = JSON.parse(readFileSync(new URL('../audit-exceptions.json', import.meta.url), 'utf8'));
const ADVISORY = { source: 1, name: 'braces', url: 'https://github.com/advisories/GHSA-vfj7-8cjw-p6xm', severity: 'high', range: '<=3.0.3' };
const CHAIN = [['vinext', '1.0.0-beta.9'], ['vite-plugin-commonjs', '0.10.4'], ['vite-plugin-dynamic-import', '1.6.0'], ['fast-glob', '3.3.3'], ['micromatch', '4.0.8'], ['braces', '3.0.3']];

function tree(chain = CHAIN, extra: Record<string, unknown> = {}) {
  let node: Record<string, unknown> = {};
  for (const [name, version] of [...chain].reverse()) node = { [name]: { version, dependencies: node } };
  return { name: 'relay-workflow-studio', dependencies: { ...node, ...extra } };
}

function report(extra: Record<string, unknown> = {}) {
  return {
    auditReportVersion: 2,
    vulnerabilities: {
      braces: { name: 'braces', severity: 'high', via: [ADVISORY] },
      micromatch: { name: 'micromatch', severity: 'high', via: ['braces'] },
      'fast-glob': { name: 'fast-glob', severity: 'high', via: ['micromatch'] },
      'vite-plugin-dynamic-import': { name: 'vite-plugin-dynamic-import', severity: 'high', via: ['fast-glob'] },
      'vite-plugin-commonjs': { name: 'vite-plugin-commonjs', severity: 'high', via: ['vite-plugin-dynamic-import'] },
      vinext: { name: 'vinext', severity: 'high', via: ['vite-plugin-commonjs'] },
      ...extra,
    },
    metadata: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 6, critical: 0, total: 6 } },
  };
}

const PUBLISHED = { braces: ['2.3.2', '3.0.0', '3.0.1', '3.0.2', '3.0.3'] };
const run = (overrides: Record<string, unknown> = {}) =>
  evaluate({ report: report(), trees: { braces: tree() }, published: PUBLISHED, exceptions, today: '2026-10-05', ...overrides });

void test('the reviewed chain passes as an accepted residual vulnerability, never as clean', () => {
  const result = run();
  assert.equal(result.ok, true, result.failures.join('\n'));
  assert.deepEqual(result.accepted.map((a: { id: string }) => a.id), ['GHSA-vfj7-8cjw-p6xm']);
  const lines = summary(result, report().metadata.vulnerabilities);
  assert.match(lines[0], /ACCEPTED RESIDUAL VULNERABILITY - not a clean audit/);
  assert.match(lines[1], /accepted until 2026-12-04/);
});

void test('the exception expires on its date and is never extended automatically', () => {
  assert.equal(run({ today: '2026-12-04' }).ok, true, 'still valid on the expiry date');
  const expired = run({ today: '2026-12-05' });
  assert.equal(expired.ok, false);
  assert.match(expired.failures.join(), /expired on 2026-12-04; it is not extended automatically/);
});

void test('an unrelated high or critical advisory fails', () => {
  for (const severity of ['high', 'critical']) {
    const other = { other: { name: 'other', severity, via: [{ name: 'other', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc', severity, range: '<2.0.0' }] } };
    const result = run({ report: report(other) });
    assert.equal(result.ok, false);
    assert.match(result.failures.join(), new RegExp(`Unaccepted ${severity} advisory GHSA-aaaa-bbbb-cccc in other`));
  }
});

void test('a moderate advisory is reported but does not fail the gate', () => {
  const moderate = { other: { name: 'other', severity: 'moderate', via: [{ name: 'other', url: 'https://github.com/advisories/GHSA-mmmm-nnnn-oooo', severity: 'moderate', range: '<2.0.0' }] } };
  assert.equal(run({ report: report(moderate) }).ok, true);
});

void test('the same advisory reached through a new dependency path fails', () => {
  const extra = { 'new-tool': { version: '1.0.0', dependencies: { braces: { version: '3.0.3' } } } };
  const result = run({ trees: { braces: tree(CHAIN, extra) } });
  assert.equal(result.ok, false);
  assert.match(result.failures.join(), /unexpected dependency path new-tool@1\.0\.0 > braces@3\.0\.3/);
});

void test('version drift anywhere in the reviewed chain fails', () => {
  // A new vinext, a new micromatch, or another braces still inside the vulnerable range.
  const drifts: Array<[number, string]> = [[0, '1.0.0-beta.10'], [4, '4.0.9'], [5, '3.0.2']];
  for (const [index, version] of drifts) {
    const drifted = CHAIN.map(([name, reviewed], i): [string, string] => [name, i === index ? version : reviewed]);
    const result = run({ trees: { braces: tree(drifted) } });
    assert.equal(result.ok, false, `drift at ${CHAIN[index][0]}`);
    assert.match(result.failures.join(), /unexpected dependency path/);
  }
});

void test('a compatible patched release fails the gate so it is adopted', () => {
  const result = run({ published: { braces: [...PUBLISHED.braces, '3.0.4'] } });
  assert.equal(result.ok, false);
  assert.match(result.failures.join(), /compatible patched release 3\.0\.4 is published/);
  // A new major is not a compatible patch for micromatch's braces ^3 dependency.
  assert.equal(run({ published: { braces: [...PUBLISHED.braces, '4.0.0'] } }).ok, true);
});

void test('a changed vulnerable range needs a new review', () => {
  const widened = report({ braces: { name: 'braces', severity: 'high', via: [{ ...ADVISORY, range: '<=3.0.5' }] } });
  const result = run({ report: widened });
  assert.equal(result.ok, false);
  assert.match(result.failures.join(), /now covers <=3\.0\.5, not the reviewed <=3\.0\.3/);
});

void test('an affected package outside the reviewed chain fails', () => {
  const result = run({ report: report({ 'other-plugin': { name: 'other-plugin', severity: 'high', via: ['braces'] } }) });
  assert.equal(result.ok, false);
  assert.match(result.failures.join(), /Unexpected high finding in other-plugin/);
});

void test('a malformed report, an audit-service error or missing registry data fails', () => {
  assert.equal(run({ report: null }).ok, false);
  assert.equal(run({ report: { auditReportVersion: 2 } }).ok, false);
  const failed = run({ report: { error: { code: 'ENOTFOUND', summary: 'request to registry failed' } } });
  assert.equal(failed.ok, false);
  assert.match(failed.failures.join(), /audit service failed: request to registry failed/);
  assert.equal(run({ report: report({ broken: { name: 'broken', severity: 'high' } }) }).ok, false);
  const unpublished = run({ published: {} });
  assert.equal(unpublished.ok, false);
  assert.match(unpublished.failures.join(), /published versions of braces could not be checked/);
  assert.equal(run({ trees: { braces: { dependencies: {} } } }).ok, false, 'an unverifiable installation is not a pass');
});

void test('an audit with no blocking advisories passes without claiming more', () => {
  const clean = { vulnerabilities: {}, metadata: { vulnerabilities: { high: 0, critical: 0, total: 0 } } };
  const result = run({ report: clean });
  assert.equal(result.ok, true);
  assert.match(summary(result, clean.metadata.vulnerabilities)[0], /passed, no high or critical advisories/);
});

void test('range and path helpers', () => {
  assert.equal(satisfies('<=3.0.3', '3.0.3'), true);
  assert.equal(satisfies('<=3.0.3', '3.0.4'), false);
  assert.equal(satisfies('>=7.0.0 <7.29.1', '7.29.1'), false);
  assert.throws(() => satisfies('~3.0', '3.0.0'), /unparseable range/);
  assert.deepEqual(dependencyPaths(tree(), 'braces'), [CHAIN.map(([n, v]) => `${n}@${v}`)]);
});
