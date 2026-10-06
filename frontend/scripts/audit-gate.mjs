// npm audit with reviewed, expiring exceptions. Run from frontend/: node scripts/audit-gate.mjs
//
// Gathers the live inputs - the audit report, the installed dependency tree for each
// excepted package, and its published versions - with each command's exit status, and
// applies audit-gate-core.mjs. Nothing here decides a pass: the evaluator checks every
// status and document, and any input it cannot trust fails the gate.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { evaluate, summary } from './audit-gate-core.mjs';

function npmJson(args) {
  const result = spawnSync('npm', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (result.error) throw new Error(`npm ${args.join(' ')} could not run: ${result.error.message}`);
  let json;
  try {
    json = JSON.parse(result.stdout);
  } catch {
    throw new Error(`npm ${args.join(' ')} returned no JSON (exit ${result.status}${result.signal ? `, ${result.signal}` : ''}).`);
  }
  return { json, status: result.status, signal: result.signal };
}

try {
  const { exceptions } = JSON.parse(readFileSync(new URL('../audit-exceptions.json', import.meta.url), 'utf8'));
  const audit = npmJson(['audit', '--json']);
  const commands = [{ command: 'npm audit', status: audit.status, signal: audit.signal, total: audit.json?.metadata?.vulnerabilities?.total }];
  const trees = {};
  const published = {};
  for (const name of new Set(exceptions.map((e) => e.package))) {
    const ls = npmJson(['ls', name, '--all', '--json']);
    const view = npmJson(['view', name, 'versions', '--json']);
    trees[name] = ls.json;
    published[name] = view.json;
    commands.push({ command: `npm ls ${name}`, status: ls.status, signal: ls.signal }, { command: `npm view ${name} versions`, status: view.status, signal: view.signal });
  }
  const result = evaluate({ report: audit.json, trees, published, exceptions, commands, today: new Date().toISOString().slice(0, 10) });
  for (const line of summary(result, audit.json?.metadata?.vulnerabilities)) console.log(line);
  process.exit(result.ok ? 0 : 1);
} catch (error) {
  console.error(`npm audit gate: FAILED - ${error.message}`);
  process.exit(1);
}
