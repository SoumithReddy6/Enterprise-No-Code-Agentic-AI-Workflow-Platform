// npm audit with reviewed, expiring exceptions. Run from frontend/: node scripts/audit-gate.mjs
//
// Gathers the live inputs - the audit report, the installed dependency tree for each
// excepted package, and its published versions - and applies audit-gate-core.mjs. Any
// failure to obtain them fails the gate: an audit that could not run is not a pass.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { evaluate, summary } from './audit-gate-core.mjs';

function npmJson(args) {
  const result = spawnSync('npm', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  if (result.error) throw new Error(`npm ${args.join(' ')} could not run: ${result.error.message}`);
  try {
    return JSON.parse(result.stdout);
  } catch {
    throw new Error(`npm ${args.join(' ')} returned no JSON (exit ${result.status}).`);
  }
}

try {
  const { exceptions } = JSON.parse(readFileSync(new URL('../audit-exceptions.json', import.meta.url), 'utf8'));
  // npm audit exits 1 when it finds advisories; the report decides, not the exit code.
  const report = npmJson(['audit', '--json']);
  const trees = {};
  const published = {};
  for (const name of new Set(exceptions.map((e) => e.package))) {
    trees[name] = npmJson(['ls', name, '--all', '--json']);
    published[name] = npmJson(['view', name, 'versions', '--json']);
  }
  const result = evaluate({ report, trees, published, exceptions, today: new Date().toISOString().slice(0, 10) });
  for (const line of summary(result, report?.metadata?.vulnerabilities)) console.log(line);
  process.exit(result.ok ? 0 : 1);
} catch (error) {
  console.error(`npm audit gate: FAILED - ${error.message}`);
  process.exit(1);
}
