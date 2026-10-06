// Runs the real audit-gate.mjs against a fake npm executable, so command handling - exit
// codes, signals, unparseable output, a missing npm - is tested end to end, not only the
// pure evaluator.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { chmodSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = fileURLToPath(new URL('..', import.meta.url));
const { exceptions } = JSON.parse(readFileSync(join(FRONTEND, 'audit-exceptions.json'), 'utf8'));
const EXPIRED = new Date().toISOString().slice(0, 10) > exceptions[0].expires;

const ADVISORY = { source: 1, name: 'braces', url: 'https://github.com/advisories/GHSA-vfj7-8cjw-p6xm', severity: 'high', range: '<=3.0.3' };
const CHAIN = ['vinext@1.0.0-beta.9', 'vite-plugin-commonjs@0.10.4', 'vite-plugin-dynamic-import@1.6.0', 'fast-glob@3.3.3', 'micromatch@4.0.8', 'braces@3.0.3'];
const via = ['braces', 'micromatch', 'fast-glob', 'vite-plugin-dynamic-import', 'vite-plugin-commonjs'];
const vulnerabilities: Record<string, unknown> = { braces: { name: 'braces', severity: 'high', via: [ADVISORY] } };
for (const [i, name] of ['micromatch', 'fast-glob', 'vite-plugin-dynamic-import', 'vite-plugin-commonjs', 'vinext'].entries()) {
  vulnerabilities[name] = { name, severity: 'high', via: [via[i]] };
}
const AUDIT = { auditReportVersion: 2, vulnerabilities, metadata: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 6, critical: 0, total: 6 } } };
let node: Record<string, unknown> = {};
for (const step of [...CHAIN].reverse()) {
  const at = step.lastIndexOf('@');
  node = { [step.slice(0, at)]: { version: step.slice(at + 1), dependencies: node } };
}
const TREE = { name: 'relay-workflow-studio', dependencies: node };
const VERSIONS = ['3.0.0', '3.0.1', '3.0.2', '3.0.3'];

type Reply = { json?: unknown; text?: string; status?: number };

function gate(replies: { audit?: Reply; ls?: Reply; view?: Reply }, withNpm = true) {
  const dir = mkdtempSync(join(tmpdir(), 'relay-audit-gate-'));
  const defaults: Record<string, Reply> = { audit: { json: AUDIT, status: 1 }, ls: { json: TREE, status: 0 }, view: { json: VERSIONS, status: 0 } };
  for (const command of ['audit', 'ls', 'view'] as const) {
    const reply = { ...defaults[command], ...replies[command] };
    writeFileSync(join(dir, `${command}.out`), reply.text ?? JSON.stringify(reply.json));
    writeFileSync(join(dir, `${command}.status`), String(reply.status ?? 0));
  }
  const npm = join(dir, 'npm');
  writeFileSync(npm, '#!/bin/sh\ncat "$FAKE_NPM/$1.out"\nexit "$(cat "$FAKE_NPM/$1.status")"\n');
  chmodSync(npm, 0o755);
  // Without npm, PATH holds only an empty directory, so no npm installed on the machine is found.
  const empty = mkdtempSync(join(tmpdir(), 'relay-audit-gate-empty-'));
  const path = withNpm ? `${dir}:/usr/bin:/bin` : empty;
  const result = spawnSync(process.execPath, ['scripts/audit-gate.mjs'], { cwd: FRONTEND, encoding: 'utf8', env: { NODE_ENV: 'test', PATH: path, FAKE_NPM: dir, HOME: dir } });
  return { status: result.status, output: `${result.stdout}${result.stderr}` };
}

void test('the reviewed chain passes through the real CLI as an accepted residual vulnerability', () => {
  const { status, output } = gate({});
  if (EXPIRED) {
    assert.equal(status, 1);
    assert.match(output, /expired on/);
  } else {
    assert.equal(status, 0, output);
    assert.match(output, /ACCEPTED RESIDUAL VULNERABILITY - not a clean audit/);
  }
});

void test('npm audit exiting with an error status fails even with an acceptable report', () => {
  const { status, output } = gate({ audit: { status: 2 } });
  assert.equal(status, 1);
  assert.match(output, /npm audit exited 2/);
});

void test('npm ls failing with tree problems fails', () => {
  const { status, output } = gate({ ls: { json: { ...TREE, problems: ['invalid: braces@3.0.3'] }, status: 1 } });
  assert.equal(status, 1);
  assert.match(output, /npm ls braces exited 1/);
  assert.match(output, /invalid: braces@3\.0\.3/);
});

void test('npm view failing with an empty list fails', () => {
  const { status, output } = gate({ view: { json: [], status: 1 } });
  assert.equal(status, 1);
  assert.match(output, /npm view braces versions exited 1/);
  assert.match(output, /published versions of braces could not be checked/);
});

void test('unparseable output or a missing npm fails', () => {
  const garbled = gate({ audit: { text: 'npm ERR! registry unreachable', status: 1 } });
  assert.equal(garbled.status, 1);
  assert.match(garbled.output, /returned no JSON/);
  const absent = gate({}, false);
  assert.equal(absent.status, 1);
  assert.match(absent.output, /npm audit --json could not run/);
});

void test('a high finding whose only advisory says moderate fails through the real CLI', () => {
  const advisory = { source: 2, name: 'other', url: 'https://github.com/advisories/GHSA-aaaa-bbbb-cccc', severity: 'moderate', range: '<2.0.0' };
  const contradicted = { auditReportVersion: 2, vulnerabilities: { other: { name: 'other', severity: 'high', via: [advisory] } }, metadata: { vulnerabilities: { info: 0, low: 0, moderate: 0, high: 1, critical: 0, total: 1 } } };
  const { status, output } = gate({ audit: { json: contradicted, status: 1 } });
  assert.equal(status, 1, output);
  assert.match(output, /finding other is high but nothing in its via is above moderate/);
  assert.doesNotMatch(output, /no high or critical advisories/);
});
