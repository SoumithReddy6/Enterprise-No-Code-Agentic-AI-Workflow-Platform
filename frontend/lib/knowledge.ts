import type { Workflow, RunEvent } from './workflow';
export type KnowledgeSource = {
  id: string;
  document_id: string;
  knowledge_base_id: string;
  version: number;
  url?: string;
  filename: string;
  page: number;
  text: string;
  score: number;
  citation: string;
  cited: boolean;
};
const safeId = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9_-]{1,64}$/.test(value);
/** Accept only canonical knowledge-base sources; presentation fields default so Retrieve output renders too. */
export function parseSources(value: unknown): KnowledgeSource[] {
  try {
    const parsed: unknown =
      typeof value === 'string' ? JSON.parse(value) : value;
    if (!Array.isArray(parsed)) return [];
    const candidates: Record<string, unknown>[] = parsed.flatMap(
      (source: unknown, index: number) => {
        if (!source || typeof source !== 'object') return [];
        const entry = source as Record<string, unknown>;
        return [
          {
            ...entry,
            citation: entry.citation ?? `S${index + 1}`,
            cited: entry.cited ?? false,
          },
        ];
      },
    );
    return candidates.filter((s): s is KnowledgeSource =>
        Boolean(
          s &&
          typeof s === 'object' &&
          safeId(s.id) &&
          safeId(s.document_id) &&
          safeId(s.knowledge_base_id) &&
          Number.isInteger(s.version) &&
          Number(s.version) >= 1 &&
          typeof s.filename === 'string' &&
          typeof s.text === 'string' &&
          typeof s.page === 'number' &&
          Number.isInteger(s.page) &&
          s.page >= 1 &&
          s.page <= 200 &&
          typeof s.score === 'number' &&
          Number.isFinite(s.score) &&
          typeof s.citation === 'string' &&
          /^S[1-9][0-9]*$/.test(s.citation) &&
          typeof s.cited === 'boolean',
        ),
      );
  } catch {
    return [];
  }
}
/** Links are built from validated identifiers, never from a URL carried in the source. */
export function documentLink(source: KnowledgeSource): string {
  return safeId(source.knowledge_base_id) &&
    safeId(source.document_id) &&
    Number.isInteger(source.page) &&
    source.page >= 1 &&
    source.page <= 200
    ? `/api/knowledge-bases/${source.knowledge_base_id}/documents/${source.document_id}/file#page=${source.page}`
    : '';
}
export function sourcesForRun(
  run: { workflow: Workflow } | undefined,
  events: RunEvent[],
): KnowledgeSource[] {
  if (!run) return [];
  const answerIds = new Set(
    run.workflow.nodes
      .filter((n) =>
        ['query', 'retrieve', 'agent', 'response'].includes(n.type),
      )
      .map((n) => n.id),
  );
  const latest = new Map<string, RunEvent>();
  for (const event of events)
    if (event.node_id && answerIds.has(event.node_id))
      latest.set(event.node_id, event);
  // Retrieve reports what was found; the Agent reports what it cited. Same passage, one card.
  const merged = new Map<string, KnowledgeSource>();
  for (const source of [...latest.values()]
    .filter((e) => e.status === 'success')
    .flatMap((e) => parseSources(e.outputs?.sources))) {
    const previous = merged.get(source.id);
    if (!previous || (source.cited && !previous.cited))
      merged.set(source.id, source);
  }
  return [...merged.values()];
}
