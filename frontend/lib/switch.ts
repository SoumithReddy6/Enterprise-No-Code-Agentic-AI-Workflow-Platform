// Switch case editing. Case ids are route handles: generated once, never derived from a
// label, and kept when a case is renamed or moved, so its connection stays attached.
//
// A draft may hold malformed entries (an imported file, an older or hand-edited save). They
// stay in the draft exactly as they are until the author repairs or removes them; no helper
// here drops or rewrites one silently.
type RouteEdge = { id: string; source: string; sourceHandle?: string | null };

export type SwitchCase = { id: string; label: string; when: Record<string, unknown> };
export type SwitchConfig = { cases: SwitchCase[] };

export const MAX_CASES = 15;
const ID_CHARS = 'abcdefghijklmnopqrstuvwxyz0123456789';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value);

/** Whether an entry has the shape the case editor can show and edit. The backend still
 * validates ids, labels and rules; this only guards the editor against crashing. */
export function isSwitchCase(entry: unknown): entry is SwitchCase {
  return isRecord(entry) && typeof entry.id === 'string' && typeof entry.label === 'string'
    && (entry.when === undefined || isRecord(entry.when));
}

export function newCaseId(existing: Iterable<string>, random: () => number = Math.random): string {
  const taken = new Set(existing);
  for (;;) {
    let suffix = '';
    for (let i = 0; i < 6; i += 1) suffix += ID_CHARS[Math.floor(random() * ID_CHARS.length)];
    const id = `case_${suffix}`;
    if (!taken.has(id)) return id;
  }
}

/** Every entry of the case list, malformed ones included, in order; [] when it is not a list. */
export function caseEntries(config: Record<string, unknown>): unknown[] {
  return Array.isArray(config.cases) ? config.cases : [];
}

/** The well-formed cases only, in order. */
export function switchCases(config: Record<string, unknown>): SwitchCase[] {
  return caseEntries(config).filter(isSwitchCase);
}

function newCase(config: Record<string, unknown>, position: number): SwitchCase {
  return {
    id: newCaseId(switchCases(config).map((c) => c.id)),
    label: `Case ${position}`,
    when: { operator: 'eq', field: '' },
  };
}

export const defaultSwitchConfig = (): SwitchConfig => ({ cases: [newCase({}, 1)] });

export function addCase(config: Record<string, unknown>): Record<string, unknown> {
  const entries = caseEntries(config);
  if (entries.length >= MAX_CASES) return config;
  return { ...config, cases: [...entries, newCase(config, entries.length + 1)] };
}

export function updateCase(
  config: Record<string, unknown>,
  id: string,
  change: Partial<Omit<SwitchCase, 'id'>>,
): Record<string, unknown> {
  return { ...config, cases: caseEntries(config).map((c) => (isSwitchCase(c) && c.id === id ? { ...c, ...change } : c)) };
}

export function moveCase(config: Record<string, unknown>, id: string, delta: -1 | 1): Record<string, unknown> {
  const entries = [...caseEntries(config)];
  const from = entries.findIndex((c) => isSwitchCase(c) && c.id === id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= entries.length) return config;
  [entries[from], entries[to]] = [entries[to], entries[from]];
  return { ...config, cases: entries };
}

const CASE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const entryId = (entry: unknown): string | undefined =>
  isRecord(entry) && typeof entry.id === 'string' ? entry.id : undefined;

/** The route id that belongs to this entry alone: a malformed entry can still have one, and
 * then connections can start at it. An id that another entry also uses belongs to neither. */
function ownedId(entries: unknown[], index: number): string | undefined {
  const id = entryId(entries[index]);
  return id !== undefined && entries.every((e, i) => i === index || entryId(e) !== id) ? id : undefined;
}

/** The connections that start at a malformed entry's own route id. */
export function entryConnections<E extends RouteEdge>(
  config: Record<string, unknown>, edges: E[], nodeId: string, index: number,
): E[] {
  const id = ownedId(caseEntries(config), index);
  return id === undefined ? [] : caseEdges(edges, nodeId, id);
}

type Repair<E> = { config: Record<string, unknown>; edges: E[] };

/** Repair one malformed entry: replace it, at its position, with a new empty case. A valid id
 * that only this entry uses is kept (with its label), so its connections stay correct; any
 * other id is replaced, and its connections move to the new id. */
export function replaceEntry<E extends RouteEdge>(
  config: Record<string, unknown>, edges: E[], nodeId: string, index: number,
): Repair<E> {
  const entries = [...caseEntries(config)];
  if (index < 0 || index >= entries.length || isSwitchCase(entries[index])) return { config, edges };
  const entry = entries[index];
  const id = ownedId(entries, index);
  const keep = id !== undefined && CASE_ID.test(id) && id !== 'default' && id !== 'error';
  const label = isRecord(entry) && typeof entry.label === 'string' && entry.label.trim() ? entry.label : `Case ${index + 1}`;
  const replacement = keep ? { id, label, when: { operator: 'eq', field: '' } } : { ...newCase(config, index + 1), label };
  entries[index] = replacement;
  const moved = !keep && id !== undefined
    ? edges.map((e) => (e.source === nodeId && e.sourceHandle === id ? { ...e, sourceHandle: replacement.id } : e))
    : edges;
  return { config: { ...config, cases: entries }, edges: moved };
}

/** Remove one malformed entry together with the connections at its own route id. */
export function removeEntry<E extends RouteEdge>(
  config: Record<string, unknown>, edges: E[], nodeId: string, index: number,
): Repair<E> {
  const entries = caseEntries(config);
  if (index < 0 || index >= entries.length || isSwitchCase(entries[index]) || entries.length <= 1) return { config, edges };
  const removed = new Set(entryConnections(config, edges, nodeId, index).map((e) => e.id));
  return { config: { ...config, cases: entries.filter((_, i) => i !== index) }, edges: edges.filter((e) => !removed.has(e.id)) };
}

/** Start a new list when `cases` is not a list at all. An explicit author action only. The
 * old routes are unknown, so connections from any route but Default are removed with it. */
export function resetCases<E extends RouteEdge>(
  config: Record<string, unknown>, edges: E[], nodeId: string,
): Repair<E> {
  return {
    config: { ...config, cases: [newCase({}, 1)] },
    edges: edges.filter((e) => e.source !== nodeId || e.sourceHandle === 'default'),
  };
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
  const entries = caseEntries(config);
  if (entries.length <= 1 || !entries.some((c) => isSwitchCase(c) && c.id === caseId)) return { config, edges };
  const removed = new Set(caseEdges(edges, nodeId, caseId).map((e) => e.id));
  return {
    config: { ...config, cases: entries.filter((c) => !(isSwitchCase(c) && c.id === caseId)) },
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
 * the position of that earlier case (positions count every entry, as the editor shows them).
 * Only provable duplicates: exactly equal settings once the backend's defaults are filled
 * in. Rules that differ only in spacing are not flagged; malformed entries are skipped. */
export function unreachableCases(config: Record<string, unknown>): Map<number, number> {
  const first = new Map<string, number>();
  const result = new Map<number, number>();
  caseEntries(config).forEach((c, index) => {
    if (!isSwitchCase(c)) return;
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
