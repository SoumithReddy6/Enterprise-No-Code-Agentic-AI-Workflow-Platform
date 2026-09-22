export type WorkflowNode = {
  id: string;
  type: string;
  version: 1;
  label: string;
  position: { x: number; y: number };
  inputs: Record<string, string>;
  config: Record<string, unknown>;
};
export type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  kind?: 'flow' | 'tool' | 'agent';
};
export type Workflow = {
  version: 1;
  name: string;
  description: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};
export type ConfigProperty = {
  type?: string;
  title?: string;
  default?: unknown;
  enum?: string[];
  anyOf?: ConfigProperty[];
  minimum?: number;
  maximum?: number;
  description?: string;
};
export type Definition = {
  type: string;
  name: string;
  category: string;
  description: string;
  inputs: Record<string, string>;
  outputs: Record<string, string>;
  config_schema: { properties: Record<string, ConfigProperty> };
};
export type RunEvent = {
  answerability?: { decision: 'allow' | 'abstain' | 'skip'; reason?: string };
  abstention_source?: string | null;
  seq: number;
  node_id?: string;
  status: string;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  duration_ms?: number;
  cached?: boolean;
  usage?: { calls: number; prompt_tokens: number; completion_tokens: number };
  error?: string;
  timestamp: string;
  reason?: string;
  truncated?: boolean;
};
export type Run = {
  approvals?: import('./approvals').RunApproval[];
  approval_terminal?: boolean;
  id: string;
  name?: string;
  status: string;
  output: string;
  error: string;
  created_at: string;
  finished_at?: string;
  truncated?: boolean;
  truncation_reason?: string;
  events: RunEvent[];
  workflow: Workflow;
  message: string;
};
export type Credential = { id: string; name: string; provider: string };
export type SavedWorkflow = { id: string; name: string; updated_at: string };

export const seed: Workflow = {
  version: 1,
  name: 'My first AI workflow',
  description: 'An agent answers an incoming message.',
  nodes: [
    {
      id: 'input',
      type: 'chat_input',
      version: 1,
      label: 'Chat input',
      position: { x: 300, y: 40 },
      inputs: {},
      config: {},
    },
    {
      id: 'agent',
      type: 'agent',
      version: 1,
      label: 'Agent',
      position: { x: 300, y: 250 },
      inputs: { input: 'input.message' },
      config: {
        provider: 'ollama',
        model: '',
        credential_id: '',
        role: 'reasoner',
        system: 'You are a helpful assistant.',
        user_prompt: '{input}',
        max_tokens: 2048,
        max_steps: 6,
        memory_key: 'default',
      },
    },
    {
      id: 'response',
      type: 'response',
      version: 1,
      label: 'Response',
      position: { x: 300, y: 460 },
      inputs: { text: 'agent.text' },
      config: {},
    },
  ],
  edges: [
    { id: 'input-agent', source: 'input', target: 'agent' },
    { id: 'agent-response', source: 'agent', target: 'response' },
  ],
};

export class ApiError extends Error {
  status: number;
  data: unknown;
  constructor(message: string, status: number, data: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export async function api<T>(
  path: string,
  method = 'GET',
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    const options: RequestInit = { method };
    if (body !== undefined && method !== 'GET') {
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(body);
    }
    response = await fetch(`/api${path}`, options);
  } catch {
    throw new Error(
      'Cannot reach the local backend. Start the API and try again.',
    );
  }
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail =
      data && typeof data === 'object' && 'detail' in data
        ? data.detail
        : undefined;
    throw new ApiError(
      Array.isArray(detail)
        ? detail
            .map((d: unknown) =>
              typeof d === 'string' ? d : JSON.stringify(d),
            )
            .join('\n')
        : typeof detail === 'string'
          ? detail
          : detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string'
            ? detail.message
          : 'The request failed. Check that the local API is running.',
      response.status,
      data,
    );
  }
  return data as T;
}

export const accent: Record<string, string> = {
  chat_input: 'teal',
  manual_input: 'teal',
  prompt: 'amber',
  llm: 'purple',
  condition: 'blue',
  response: 'green',
};
export const friendlyStatus: Record<string, string> = {
  awaiting_approval: 'Awaiting approval',
  queued: 'Queued',
  running: 'Running',
  success: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

/** Compare executable graph semantics, independent of server defaults and canvas layout. */
export function sameExecutionGraph(a: Workflow, b: Workflow): boolean {
  const stable = (value: unknown): string => {
    if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
    if (value && typeof value === 'object')
      return (
        '{' +
        Object.entries(value)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([key, v]) => JSON.stringify(key) + ':' + stable(v))
          .join(',') +
        '}'
      );
    return JSON.stringify(value) ?? 'null';
  };
  const executable = (w: Workflow) => ({
    nodes: w.nodes
      .map(({ id, type, version, inputs, config }) => ({
        id,
        type,
        version,
        inputs,
        config,
      }))
      .sort((a, b) => a.id.localeCompare(b.id)),
    edges: w.edges
      .map(({ source, target, sourceHandle, targetHandle, kind }) => ({
        source,
        target,
        sourceHandle: sourceHandle ?? null,
        targetHandle: targetHandle ?? null,
        kind: kind ?? 'flow',
      }))
      .sort((a, b) => stable(a).localeCompare(stable(b))),
  });
  return stable(executable(a)) === stable(executable(b));
}

export function saveCompletion(
  startDocument: number,
  currentDocument: number,
  saved: Workflow,
  current: Workflow,
) {
  return {
    sameDocument: startDocument === currentDocument,
    dirty: JSON.stringify(saved) !== JSON.stringify(current),
  };
}

export function importableWorkflow(
  workflow: Workflow,
  supportedTypes: string[],
): Workflow {
  if (workflow.nodes.some((node) => node.type.startsWith('vector_')) ||
      workflow.edges.some((edge) => String(edge.kind) === 'store'))
    throw new Error('This workflow used the retired VectorDB nodes. Create a named knowledge base and reconnect a Retrieve node before importing or replaying it.');
  const unsupported = workflow.nodes.find(
    (node) => !supportedTypes.includes(node.type),
  );
  if (unsupported)
    throw new Error(`Unsupported node type: ${unsupported.type}`);
  return workflow;
}

export function connectionKind(
  sourceType: string,
  targetType: string,
  sourceHandle?: string | null,
  targetHandle?: string | null,
): WorkflowEdge['kind'] | null {
  if (targetHandle === 'tools')
    return targetType === 'agent' &&
      (sourceType.startsWith('tool_') ||
        ['retrieve', 'query'].includes(sourceType))
      ? 'tool'
      : null;
  if (sourceHandle === 'agents')
    return sourceType === 'agent' && targetType === 'agent' ? 'agent' : null;
  return 'flow';
}
export const paletteCategories = [
  'Input',
  'Agent',
  'Tools',
  'Retrieval',
  'Control',
  'Output',
];
export function paletteCategory(type: string): string | null {
  // Prompt template and Language model stay loadable for saved workflows but are not offered.
  if (['prompt', 'llm'].includes(type)) return null;
  if (type.startsWith('tool_')) return 'Tools';
  if (['retrieve', 'query'].includes(type)) return 'Retrieval';
  return (
    (
      {
        chat_input: 'Input',
        manual_input: 'Input',
        agent: 'Agent',
        condition: 'Control',
        response: 'Output',
      } as Record<string, string>
    )[type] || null
  );
}

export function flowBinding(
  from: WorkflowNode,
  target: WorkflowNode,
  fromDef: Definition,
  toDef: Definition,
): { port: string; output: string } | null {
  const evidence = from.type === 'retrieve' && target.type === 'agent';
  const port = evidence
    ? 'input'
    : Object.keys(toDef.inputs).find((k) => !target.inputs[k]);
  const output = evidence ? 'context' : Object.keys(fromDef.outputs)[0];
  return port && output ? { port, output } : null;
}
