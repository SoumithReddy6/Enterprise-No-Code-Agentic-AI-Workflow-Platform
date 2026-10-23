// Switch case editing. Case ids are route handles: generated once, never derived from a
// label, and kept when a case is renamed or moved, so its connection stays attached.
type RouteEdge = { id: string; source: string; sourceHandle?: string | null };

export type SwitchCase = { id: string; label: string; when: Record<string, unknown> };
export type SwitchConfig = { cases: SwitchCase[] };

export const MAX_CASES = 15;
const ID_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';

export function newCaseId(existing: Iterable<string>, random: () => number = Math.random): string {
  const taken = new Set(existing);
  for (;;) {
    let suffix = '';
    for (let i = 0; i < 6; i += 1) suffix += ID_CHARS[Math.floor(random() * ID_CHARS.length)];
    const id = `case_${suffix}`;
    if (!taken.has(id)) return id;
  }
}

export function switchCases(config: Record<string, unknown>): SwitchCase[] {
  return Array.isArray(config.cases) ? (config.cases as SwitchCase[]) : [];
}

function newCase(cases: SwitchCase[]): SwitchCase {
  return {
    id: newCaseId(cases.map((c) => c.id)),
    label: `Case ${cases.length + 1}`,
    when: { operator: 'eq', field: '' },
  };
}

export const defaultSwitchConfig = (): SwitchConfig => ({ cases: [newCase([])] });

export function addCase(config: Record<string, unknown>): Record<string, unknown> {
  const cases = switchCases(config);
  if (cases.length >= MAX_CASES) return config;
  return { ...config, cases: [...cases, newCase(cases)] };
}

export function updateCase(
  config: Record<string, unknown>,
  id: string,
  change: Partial<Omit<SwitchCase, 'id'>>,
): Record<string, unknown> {
  return { ...config, cases: switchCases(config).map((c) => (c.id === id ? { ...c, ...change } : c)) };
}

export function moveCase(config: Record<string, unknown>, id: string, delta: -1 | 1): Record<string, unknown> {
  const cases = [...switchCases(config)];
  const from = cases.findIndex((c) => c.id === id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= cases.length) return config;
  [cases[from], cases[to]] = [cases[to], cases[from]];
  return { ...config, cases };
}

/** The edges that start at this case's handle. */
export function caseEdges<E extends RouteEdge>(edges: E[], nodeId: string, caseId: string): E[] {
  return edges.filter((e) => e.source === nodeId && e.sourceHandle === caseId);
}

/** Remove a case together with its connections: one explicit action, never a silent drop. */
export function removeCase<E extends RouteEdge>(
  config: Record<string, unknown>,
  edges: E[],
  nodeId: string,
  caseId: string,
): { config: Record<string, unknown>; edges: E[] } {
  const cases = switchCases(config);
  if (cases.length <= 1) return { config, edges };
  const removed = new Set(caseEdges(edges, nodeId, caseId).map((e) => e.id));
  return {
    config: { ...config, cases: cases.filter((c) => c.id !== caseId) },
    edges: edges.filter((e) => !removed.has(e.id)),
  };
}

// The backend's defaults (ConditionConfig): an omitted setting means this value.
const PREDICATE_DEFAULTS: Record<string, unknown> = {
  operator: 'contains', field: '', compare_as: 'text', case_sensitive: false,
};

function canonical(when: Record<string, unknown>): string {
  const filled = { ...PREDICATE_DEFAULTS, ...when };
  return JSON.stringify(Object.keys(filled).sort().map((k) => [k, filled[k] ?? null]));
}

/** For each case that can never match because an earlier case has exactly the same rule,
 * the index of that earlier case. Only provable duplicates: exactly equal settings once
 * the backend's defaults are filled in. Rules that differ only in spacing are not flagged. */
export function unreachableCases(config: Record<string, unknown>): Map<number, number> {
  const first = new Map<string, number>();
  const result = new Map<number, number>();
  switchCases(config).forEach((c, index) => {
    const key = canonical(c.when ?? {});
    if (first.has(key)) result.set(index, first.get(key)!);
    else first.set(key, index);
  });
  return result;
}

/** The text shown on a route handle: the case label, or Default. */
export function routeLabel(config: Record<string, unknown>, route: string): string {
  if (route === 'default') return 'Default';
  return switchCases(config).find((c) => c.id === route)?.label || route;
}
