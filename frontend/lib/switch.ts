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

const CASE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const entryId = (entry: unknown): string | undefined =>
  isRecord(entry) && typeof entry.id === 'string' ? entry.id : undefined;

// Routes the Switch itself owns. Default is automatic and error is reserved for recovery:
// no case, valid or malformed, can own their connections.
const RESERVED_ROUTES = new Set(['default', 'error']);

/** The route id that belongs to this entry alone: a malformed entry can still have one, and
 * then connections can start at it. An id that another entry also uses belongs to neither,
 * and a reserved route never belongs to an entry. */
function ownedId(entries: unknown[], index: number): string | undefined {
  const id = entryId(entries[index]);
  if (id === undefined || RESERVED_ROUTES.has(id)) return undefined;
  return entries.every((e, i) => i === index || entryId(e) !== id) ? id : undefined;
}

/** Why an entry cannot be edited as an ordinary case, or null when it can. Besides its shape,
 * a case needs a route id of its own: valid, not reserved for the Switch, and unique. Only
 * such a case owns the connections at its id. */
export function caseProblem(entries: unknown[], index: number): string | null {
  const entry = entries[index];
  if (!isSwitchCase(entry)) return 'it needs an id, a label and a rule';
  if (RESERVED_ROUTES.has(entry.id)) return `its route id "${entry.id}" is reserved for the Switch`;
  if (!CASE_ID.test(entry.id)) return `its route id "${entry.id}" is not a valid route id`;
  if (entries.some((e, i) => i !== index && entryId(e) === entry.id)) return `its route id "${entry.id}" is also used by another case`;
  return null;
}

const editable = (entries: unknown[], index: number): boolean => caseProblem(entries, index) === null;

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

/** The editable cases only, in order. */
export function switchCases(config: Record<string, unknown>): SwitchCase[] {
  const entries = caseEntries(config);
  return entries.filter((_, i) => editable(entries, i)) as SwitchCase[];
}

/** The position of the editable case with this route id, or -1. */
const caseIndex = (entries: unknown[], id: string): number =>
  entries.findIndex((c, i) => entryId(c) === id && editable(entries, i));

function newCase(config: Record<string, unknown>, position: number): SwitchCase {
  return {
    // Every id in the draft is taken, even a malformed entry's.
    id: newCaseId(caseEntries(config).flatMap((c) => entryId(c) ?? [])),
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
  const entries = caseEntries(config);
  const index = caseIndex(entries, id);
  return { ...config, cases: entries.map((c, i) => (i === index ? { ...(c as SwitchCase), ...change } : c)) };
}

export function moveCase(config: Record<string, unknown>, id: string, delta: -1 | 1): Record<string, unknown> {
  const entries = [...caseEntries(config)];
  const from = caseIndex(entries, id);
  const to = from + delta;
  if (from < 0 || to < 0 || to >= entries.length) return config;
  [entries[from], entries[to]] = [entries[to], entries[from]];
  return { ...config, cases: entries };
}

/** The connections that start at an entry's own route id (none for a reserved or shared id). */
export function entryConnections<E extends RouteEdge>(
  config: Record<string, unknown>, edges: E[], nodeId: string, index: number,
): E[] {
  const id = ownedId(caseEntries(config), index);
  return id === undefined ? [] : caseEdges(edges, nodeId, id);
}

type Repair<E> = { config: Record<string, unknown>; edges: E[] };

/** Repair one entry that is not an editable case, at its position. A valid id that only this
 * entry uses is kept, so its connections stay correct; any other id is replaced, and the
 * connections at an owned id move to the new one. The label and a well-shaped rule are kept,
 * so a case whose only problem is its id loses nothing; otherwise the rule starts empty. */
export function replaceEntry<E extends RouteEdge>(
  config: Record<string, unknown>, edges: E[], nodeId: string, index: number,
): Repair<E> {
  const entries = [...caseEntries(config)];
  if (index < 0 || index >= entries.length || editable(entries, index)) return { config, edges };
  const entry = entries[index];
  const id = ownedId(entries, index);
  const keep = id !== undefined && CASE_ID.test(id);
  const label = isRecord(entry) && typeof entry.label === 'string' && entry.label.trim() ? entry.label : `Case ${index + 1}`;
  const when = isRecord(entry) && isRecord(entry.when) ? entry.when : { operator: 'eq', field: '' };
  const replacement = keep ? { id, label, when } : { ...newCase(config, index + 1), label, when };
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
  if (index < 0 || index >= entries.length || editable(entries, index) || entries.length <= 1) return { config, edges };
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

/** Remove an editable case together with its connections: one explicit action, never a silent
 * drop. Only the connections at its own route id go, so Default is never touched. */
export function removeCase<E extends RouteEdge>(
  config: Record<string, unknown>,
  edges: E[],
  nodeId: string,
  caseId: string,
): { config: Record<string, unknown>; edges: E[] } {
  const entries = caseEntries(config);
  const index = caseIndex(entries, caseId);
  if (entries.length <= 1 || index < 0) return { config, edges };
  const removed = new Set(entryConnections(config, edges, nodeId, index).map((e) => e.id));
  return {
    config: { ...config, cases: entries.filter((_, i) => i !== index) },
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
  const entries = caseEntries(config);
  entries.forEach((c, index) => {
    if (!editable(entries, index)) return;
    const key = canonical((c as SwitchCase).when ?? {});
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
