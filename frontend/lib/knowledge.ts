import type { Workflow, RunEvent } from './workflow';
export type KnowledgeBase = { id: string; name: string };
export type KnowledgeDocument = {
  id: string;
  filename: string;
  status: string;
  error?: string;
  pages?: number;
};
export type KnowledgeSource = {
  id: string;
  document_id: string;
  resource_id?: string;
  knowledge_base_id?: string;
  version?: number;
  url?: string;
  filename: string;
  page: number;
  text: string;
  score: number;
  citation: string;
  cited: boolean;
};
const safeId = (value: unknown): value is string =>
  typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value);
export function parseSources(value: unknown): KnowledgeSource[] {
  try {
    const parsed: unknown =
      typeof value === 'string' ? JSON.parse(value) : value;
    if (!Array.isArray(parsed) || parsed.length > 100) return [];
    const normalized = parsed.map((source: unknown, index: number) => {
      if (
        source &&
        typeof source === 'object' &&
        (('resource_id' in source && safeId(source.resource_id)) ||
          ('knowledge_base_id' in source && safeId(source.knowledge_base_id)))
      ) {
        const entry = source as Record<string, unknown>;
        return {
          ...entry,
          citation: entry.citation ?? `S${index + 1}`,
          cited: entry.cited ?? false,
        };
      }
      return source as Record<string, unknown>;
    });
    return normalized.filter((s): s is KnowledgeSource =>
      Boolean(
        s &&
        typeof s === 'object' &&
        safeId(s.id) &&
        safeId(s.document_id) &&
        (s.resource_id === undefined || safeId(s.resource_id)) &&
        (s.knowledge_base_id === undefined ||
          (safeId(s.knowledge_base_id) &&
            s.knowledge_base_id.length <= 64 &&
            s.document_id.length <= 64 &&
            s.id.length <= 64 &&
            Number.isInteger(s.version) &&
            Number(s.version) >= 1)) &&
        typeof s.filename === 'string' &&
        typeof s.text === 'string' &&
        typeof s.page === 'number' &&
        Number.isInteger(s.page) &&
        s.page >= 1 &&
        s.page <= 200 &&
        typeof s.score === 'number' &&
        Number.isFinite(s.score) &&
        typeof s.citation === 'string' &&
        /^S([1-9][0-9]?|100)$/.test(s.citation) &&
        typeof s.cited === 'boolean',
      ),
    );
  } catch {
    return [];
  }
}
export function documentLink(source: KnowledgeSource): string {
  return safeId(source.document_id) &&
    Number.isInteger(source.page) &&
    source.page >= 1 &&
    source.page <= 200
    ? source.knowledge_base_id
      ? safeId(source.knowledge_base_id) &&
        source.knowledge_base_id.length <= 64 &&
        source.document_id.length <= 64
        ? `/api/knowledge-bases/${source.knowledge_base_id}/documents/${source.document_id}/file#page=${source.page}`
        : ''
      : `/api/${source.resource_id && safeId(source.resource_id) ? 'vector-files' : 'documents'}/${source.document_id}/file#page=${source.page}`
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
        ['grounded_answer', 'query', 'retrieve', 'agent'].includes(n.type),
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
export function pdfWorkflow(
  baseId: string,
  config: Record<string, unknown>,
): Workflow {
  return {
    version: 1,
    name: 'Ask your PDFs',
    description: 'Answer a question using local PDF evidence.',
    nodes: [
      {
        id: 'input',
        type: 'chat_input',
        version: 1,
        label: 'Question',
        position: { x: 65, y: 130 },
        inputs: {},
        config: {},
      },
      {
        id: 'retrieve',
        type: 'retrieval',
        version: 1,
        label: 'Retrieve passages',
        position: { x: 370, y: 130 },
        inputs: { query: 'input.message' },
        config: { knowledge_base_id: baseId, limit: 4 },
      },
      {
        id: 'answer',
        type: 'grounded_answer',
        version: 1,
        label: 'Grounded answer',
        position: { x: 675, y: 130 },
        inputs: {
          query: 'retrieve.query',
          context: 'retrieve.context',
          sources: 'retrieve.sources',
        },
        config: {
          system: 'Answer clearly using the supplied evidence.',
          ...config,
        },
      },
      {
        id: 'out',
        type: 'response',
        version: 1,
        label: 'Send response',
        position: { x: 980, y: 130 },
        inputs: { text: 'answer.text' },
        config: {},
      },
    ],
    edges: [
      { id: 'input-retrieve', source: 'input', target: 'retrieve' },
      { id: 'retrieve-answer', source: 'retrieve', target: 'answer' },
      { id: 'answer-out', source: 'answer', target: 'out' },
    ],
  };
}
