// Decides whether an npm audit report may pass, given reviewed exceptions.
//
// Pure: everything it needs is passed in, so the policy can be tested without npm or the
// network. The CLI (audit-gate.mjs) gathers the live inputs.
//
// A high or critical advisory passes only if an exception names exactly that advisory and
// package, the advisory's range is the one reviewed, every installed dependency path to
// the package is a reviewed path (names and versions), no compatible patched release has
// been published, and the exception has not expired. Anything else fails, including a
// malformed report or an audit-service error. A pass with an exception is reported as an
// accepted residual vulnerability, never as a clean audit.

const BLOCKING = new Set(['high', 'critical']);

function parseVersion(text) {
  const match = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(text).trim());
  return match ? match.slice(1).map(Number) : null;
}

function compare(a, b) {
  for (let i = 0; i < 3; i += 1) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1;
  return 0;
}

// Supports the comparator ranges npm advisories use: space-separated >=, >, <=, <, =.
export function satisfies(range, version) {
  const v = parseVersion(version);
  if (!v) return false;
  const comparators = String(range).trim().split(/\s+/);
  if (!comparators.length || comparators[0] === '') throw new Error(`unparseable range: ${range}`);
  return comparators.every((comparator) => {
    const match = /^(>=|<=|>|<|=)?(\d+\.\d+\.\d+)$/.exec(comparator);
    if (!match) throw new Error(`unparseable range: ${range}`);
    const order = compare(v, parseVersion(match[2]));
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

function advisoryId(url) {
  const match = /(GHSA-[a-z0-9-]+)/i.exec(String(url || ''));
  return match ? match[1] : String(url || '');
}

export function evaluate({ report, trees, published, exceptions, today }) {
  const failures = [];
  const accepted = [];
  const fail = (message) => failures.push(message);
  if (!report || typeof report !== 'object') return { ok: false, failures: ['The audit report is not a JSON object.'], accepted };
  if (report.error) return { ok: false, failures: [`The audit service failed: ${report.error.summary || report.error.code || 'unknown error'}.`], accepted };
  if (typeof report.vulnerabilities !== 'object' || report.vulnerabilities === null || typeof report.metadata?.vulnerabilities !== 'object') {
    return { ok: false, failures: ['The audit report is malformed: vulnerabilities or metadata is missing.'], accepted };
  }
  const roots = new Map();
  for (const [name, entry] of Object.entries(report.vulnerabilities)) {
    if (!entry || !Array.isArray(entry.via)) return { ok: false, failures: [`The audit report is malformed at ${name}.`], accepted };
    for (const via of entry.via) {
      if (via && typeof via === 'object') roots.set(`${via.name}|${advisoryId(via.url)}`, { ...via, id: advisoryId(via.url) });
    }
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
    const installed = dependencyPaths(trees[root.name], root.name).map((path) => path.join(' > '));
    const reviewed = (exception.paths || []).map((path) => path.join(' > '));
    if (!installed.length) fail(`${where}: no installed path to ${root.name} was found to verify.`);
    for (const path of installed) if (!reviewed.includes(path)) fail(`${where}: unexpected dependency path ${path}.`);
    let patched = [];
    try {
      const installedVersions = new Set(installed.map((path) => path.split(' > ').at(-1).split('@').at(-1)));
      const majors = new Set([...installedVersions].map((v) => parseVersion(v)?.[0]));
      patched = (published[root.name] || []).filter((v) => parseVersion(v) && majors.has(parseVersion(v)[0]) && !satisfies(root.range, v));
    } catch (error) {
      fail(`${where}: ${error.message}.`);
    }
    if (!Array.isArray(published[root.name])) fail(`${where}: the published versions of ${root.name} could not be checked.`);
    else if (patched.length) fail(`${where}: compatible patched release ${patched.join(', ')} is published. Adopt it and remove the exception.`);
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
  const tally = Object.entries(counts || {}).filter(([, n]) => n).map(([k, n]) => `${n} ${k}`).join(', ') || 'none';
  if (!result.ok) return [`npm audit gate: FAILED (reported: ${tally})`, ...result.failures.map((f) => `  - ${f}`)];
  if (!result.accepted.length) return [`npm audit gate: passed, no high or critical advisories (reported: ${tally})`];
  return [
    `npm audit gate: passed with ACCEPTED RESIDUAL VULNERABILITY - not a clean audit (reported: ${tally})`,
    ...result.accepted.map((a) => `  - ${a.id} (${a.severity}) in ${a.package}, accepted until ${a.expires}: ${a.reason}`),
  ];
}
