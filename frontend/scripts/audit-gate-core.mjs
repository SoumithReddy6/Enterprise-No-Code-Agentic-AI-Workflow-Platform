// Decides whether an npm audit may pass, given reviewed exceptions.
//
// Pure: everything it needs is passed in, so the policy can be tested without npm or the
// network. The CLI (audit-gate.mjs) gathers the live inputs.
//
// Fail closed. Every input is validated before it is trusted: a report that is not the
// documented shape, a severity or reference that is missing or unknown, counts that do
// not match the findings, a command that did not succeed, a dependency tree with
// problems, or registry data that cannot show whether a patch exists - each fails. Absent
// data is never read as "nothing blocking".
//
// A high or critical advisory passes only if an exception names exactly that advisory and
// package, the advisory's range is the one reviewed, every installed dependency path to
// the package is a reviewed path (names and versions), no compatible patched release has
// been published, and the exception has not expired. A pass with an exception is reported
// as an accepted residual vulnerability, never as a clean audit.

export const SEVERITIES = ['info', 'low', 'moderate', 'high', 'critical'];
const BLOCKING = new Set(['high', 'critical']);
const SEMVER = /^(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$/;
const isMap = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const isText = (value) => typeof value === 'string' && value.trim() !== '';

// Stable releases only: a prerelease is valid registry data but never counts as a patch.
function parseStable(text) {
  const match = SEMVER.exec(String(text));
  return match && !match[4] ? match.slice(1, 4).map(Number) : null;
}

function compare(a, b) {
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

// Supports the comparator ranges npm advisories use: space-separated >=, >, <=, <, =.
export function satisfies(range, version) {
  const v = parseStable(version);
  if (!v) return false;
  const comparators = String(range).trim().split(/\s+/);
  if (!comparators.length || comparators[0] === '') throw new Error(`unparseable range: ${range}`);
  return comparators.every((comparator) => {
    const match = /^(>=|<=|>|<|=)?(\d+\.\d+\.\d+)$/.exec(comparator);
    if (!match) throw new Error(`unparseable range: ${range}`);
    const order = compare(v, parseStable(match[2]));
    return { '>=': order >= 0, '<=': order <= 0, '>': order > 0, '<': order < 0, '=': order === 0, undefined: order === 0 }[match[1]];
  });
}

// Paths from the root to every installed occurrence of `name`, as name@version lists.
export function dependencyPaths(tree, name) {
  const paths = [];
  const walk = (node, trail) => {
    for (const [child, info] of Object.entries(node?.dependencies || {})) {
      const step = `${child}@${info?.version}`;
      if (child === name) paths.push([...trail, step]);
      walk(info, [...trail, step]);
    }
  };
  walk(tree, []);
  return paths;
}

// Problems npm ls reports that make the installed tree unfit to verify.
export function treeProblems(tree) {
  const problems = [];
  if (!isMap(tree)) return ['the dependency tree is not a JSON object'];
  if (tree.error) problems.push(`npm ls reported an error: ${tree.error.summary || tree.error.code || 'unknown'}`);
  if (Array.isArray(tree.problems) && tree.problems.length) problems.push(...tree.problems.map((p) => `npm ls: ${p}`));
  const walk = (node, trail) => {
    for (const [child, info] of Object.entries(node?.dependencies || {})) {
      const where = [...trail, child].join(' > ');
      if (!isMap(info)) { problems.push(`${where} has no record`); continue; }
      for (const flag of ['missing', 'invalid', 'extraneous']) if (info[flag]) problems.push(`${where} is ${flag}`);
      if (!info.missing && !SEMVER.test(String(info.version))) problems.push(`${where} has no valid version`);
      walk(info, [...trail, child]);
    }
  };
  walk(tree, []);
  return problems;
}

// Exit codes the CLI accepts: npm audit exits 1 exactly when it found advisories.
/** @param {{ command: string, status: number | null, signal?: string | null, total?: number | null }} result */
export function commandProblems({ command, status, signal = null, total = null }) {
  if (signal) return [`${command} was terminated by ${signal}`];
  if (command === 'npm audit') {
    if (status === 0 && total === 0) return [];
    if (status === 1 && total > 0) return [];
    return [`npm audit exited ${String(status)} with ${String(total)} reported findings; only 0 with none or 1 with some is a completed audit`];
  }
  return status === 0 ? [] : [`${command} exited ${String(status)}`];
}

function advisoryId(url) {
  const match = /(GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4})/i.exec(String(url || ''));
  return match ? match[1] : null;
}

// Every structural requirement on the report; any violation makes it unusable.
export function reportProblems(report) {
  if (!isMap(report)) return ['the audit report is not a JSON object'];
  if (report.error) return [`the audit service failed: ${report.error.summary || report.error.code || 'unknown error'}`];
  const problems = [];
  if (!isMap(report.vulnerabilities)) problems.push('vulnerabilities is missing or not an object');
  const counts = report.metadata?.vulnerabilities;
  if (!isMap(counts)) problems.push('metadata.vulnerabilities is missing or not an object');
  else for (const key of [...SEVERITIES, 'total']) {
    if (!Number.isInteger(counts[key]) || counts[key] < 0) problems.push(`metadata count ${key} is missing or invalid`);
  }
  if (problems.length) return problems;
  const tally = Object.fromEntries(SEVERITIES.map((s) => [s, 0]));
  const advisories = new Map();
  for (const [name, entry] of Object.entries(report.vulnerabilities)) {
    if (!isMap(entry)) { problems.push(`finding ${name} is not an object`); continue; }
    if (entry.name !== name) problems.push(`finding ${name} names itself ${JSON.stringify(entry.name)}`);
    if (!SEVERITIES.includes(entry.severity)) problems.push(`finding ${name} has unknown severity ${JSON.stringify(entry.severity)}`);
    else tally[entry.severity] += 1;
    if (!Array.isArray(entry.via) || !entry.via.length) { problems.push(`finding ${name} has no via`); continue; }
    for (const via of entry.via) {
      if (typeof via === 'string') {
        if (!Object.hasOwn(report.vulnerabilities, via)) problems.push(`finding ${name} refers to unknown finding ${via}`);
      } else if (isMap(via)) {
        const id = advisoryId(via.url);
        if (!isText(via.name) || !id || !SEVERITIES.includes(via.severity) || !isText(via.range)) {
          problems.push(`finding ${name} has an incomplete advisory record`);
          continue;
        }
        const key = `${via.name}|${id}`;
        const seen = advisories.get(key);
        if (seen && (seen.severity !== via.severity || seen.range !== via.range)) problems.push(`advisory ${id} in ${via.name} is reported inconsistently`);
        if (!seen) advisories.set(key, { ...via, id });
      } else {
        problems.push(`finding ${name} has an invalid via entry`);
      }
    }
  }
  for (const severity of SEVERITIES) {
    if (tally[severity] !== counts[severity]) problems.push(`metadata reports ${counts[severity]} ${severity} but the report lists ${tally[severity]}`);
  }
  const listed = Object.values(tally).reduce((a, b) => a + b, 0);
  if (counts.total !== listed) problems.push(`metadata reports ${counts.total} total but the report lists ${listed}`);
  return problems;
}

function publishedProblems(name, versions, installedVersions) {
  if (!Array.isArray(versions) || !versions.length) return [`the published versions of ${name} could not be checked`];
  const invalid = versions.filter((v) => typeof v !== 'string' || !SEMVER.test(v));
  if (invalid.length) return [`the published versions of ${name} include invalid entries: ${invalid.slice(0, 3).map(String).join(', ')}`];
  const missing = installedVersions.filter((v) => !versions.includes(v));
  if (missing.length) return [`the published versions of ${name} do not include the installed ${missing.join(', ')}`];
  return [];
}

export function evaluate({ report, trees, published, exceptions, today, commands = [] }) {
  const failures = [];
  const accepted = [];
  const fail = (message) => failures.push(message);
  for (const command of commands) failures.push(...commandProblems(command));
  const structural = reportProblems(report);
  if (structural.length) return { ok: false, failures: [...failures, ...structural.map((p) => `Malformed audit report: ${p}.`)], accepted };
  const roots = new Map();
  for (const entry of Object.values(report.vulnerabilities)) {
    for (const via of entry.via) if (isMap(via)) roots.set(`${via.name}|${advisoryId(via.url)}`, { ...via, id: advisoryId(via.url) });
  }
  const allowedAffected = new Set();
  for (const root of roots.values()) {
    if (!BLOCKING.has(root.severity)) continue;
    const exception = exceptions.find((e) => e.advisory === root.id && e.package === root.name);
    if (!exception) { fail(`Unaccepted ${root.severity} advisory ${root.id} in ${root.name} (${root.range}).`); continue; }
    const where = `${root.id} in ${root.name}`;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(exception.expires || '')) { fail(`The exception for ${where} has no valid expiry date.`); continue; }
    if (today > exception.expires) fail(`The exception for ${where} expired on ${exception.expires}; it is not extended automatically. Re-review it.`);
    if (root.range !== exception.vulnerable_range) fail(`${where} now covers ${root.range}, not the reviewed ${exception.vulnerable_range}. Re-review it.`);
    const tree = trees[root.name];
    for (const problem of treeProblems(tree)) fail(`${where}: the installed tree cannot be verified: ${problem}.`);
    const installed = dependencyPaths(tree, root.name).map((path) => path.join(' > '));
    const reviewed = (exception.paths || []).map((path) => path.join(' > '));
    if (!installed.length) fail(`${where}: no installed path to ${root.name} was found to verify.`);
    for (const path of installed) if (!reviewed.includes(path)) fail(`${where}: unexpected dependency path ${path}.`);
    const installedVersions = [...new Set(installed.map((path) => path.split(' > ').at(-1).split('@').at(-1)))];
    const registry = publishedProblems(root.name, published[root.name], installedVersions);
    for (const problem of registry) fail(`${where}: ${problem}.`);
    if (!registry.length) {
      try {
        const majors = new Set(installedVersions.map((v) => parseStable(v)?.[0]));
        const patched = published[root.name].filter((v) => parseStable(v) && majors.has(parseStable(v)[0]) && !satisfies(root.range, v));
        if (patched.length) fail(`${where}: compatible patched release ${patched.join(', ')} is published. Adopt it and remove the exception.`);
      } catch (error) {
        fail(`${where}: ${error.message}.`);
      }
    }
    for (const path of exception.paths || []) for (const step of path) allowedAffected.add(step.slice(0, step.lastIndexOf('@')));
    accepted.push({ id: root.id, package: root.name, severity: root.severity, expires: exception.expires, reason: exception.reason });
  }
  for (const [name, entry] of Object.entries(report.vulnerabilities)) {
    if (BLOCKING.has(entry.severity) && !allowedAffected.has(name) && entry.via.every((via) => typeof via === 'string')) {
      fail(`Unexpected ${entry.severity} finding in ${name}, outside every reviewed dependency path.`);
    }
  }
  return { ok: failures.length === 0, failures, accepted };
}

export function summary(result, counts) {
  const tally = isMap(counts) ? Object.entries(counts).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(', ') || 'none' : 'unavailable';
  if (!result.ok) return [`npm audit gate: FAILED (reported: ${tally})`, ...result.failures.map((f) => `  - ${f}`)];
  if (!result.accepted.length) return [`npm audit gate: passed, no high or critical advisories (reported: ${tally})`];
  return [
    `npm audit gate: passed with ACCEPTED RESIDUAL VULNERABILITY - not a clean audit (reported: ${tally})`,
    ...result.accepted.map((a) => `  - ${a.id} (${a.severity}) in ${a.package}, accepted until ${a.expires}: ${a.reason}`),
  ];
}
