export type WorkflowNode = {
  id: string;
  type: string;
  version: 1;
  label: string;
  position: { x: number; y: number };
  inputs: Record<string, string>;
  config: Record<string, unknown>;
  on_error?: 'fail' | 'continue' | 'route';
  retry?: { attempts: number; base_delay: number };
};
export type WorkflowEdge = {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
  targetHandle?: string | null;
  kind?: 'flow' | 'tool' | 'agent' | 'loop';
};
// A validation message and the node it concerns, when it concerns one.
export type Issue = { node_id: string | null; message: string };
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
  items_truncated?: boolean;
  items_warnings?: string[];
  answerability?: { decision: 'allow' | 'abstain' | 'skip'; reason?: string };
  abstention_source?: string | null;
  seq: number;
  node_id?: string;
  status: string;
  inputs?: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  decision?: TrustDecision;
  duration_ms?: number;
  cached?: boolean;
  usage?: { calls: number; prompt_tokens: number; completion_tokens: number };
  error?: string;
  timestamp: string;
  reason?: string;
  truncated?: boolean;
  recovered?: boolean;
  budget_exhausted?: boolean;
  preview_of?: string[];
  preview_omitted?: number;
  omitted_fields?: string[];
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
  truncation_source?: string;
  truncation_causes?: import('./run-status').TruncationCause[];
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
        calculate: 'Control',
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

// The condition settings each operator uses. Operator and field always apply; the
// inspector hides the rest, and the backend rejects an operand the operator does not use.
const CONDITION_SETTINGS: Record<string, string[]> = {
  contains: ['contains', 'case_sensitive'],
  eq: ['compare_to', 'compare_as', 'case_sensitive'],
  ne: ['compare_to', 'compare_as', 'case_sensitive'],
  gt: ['compare_to'],
  gte: ['compare_to'],
  lt: ['compare_to'],
  lte: ['compare_to'],
  in: ['options', 'compare_as', 'case_sensitive'],
  empty: [],
};

// A saved condition without an operator is a legacy contains condition.
function conditionOperator(config: Record<string, unknown>): string {
  return typeof config.operator === 'string' ? config.operator : 'contains';
}

export function conditionSettingVisible(
  config: Record<string, unknown>,
  key: string,
): boolean {
  if (key === 'operator' || key === 'field') return true;
  return (CONDITION_SETTINGS[conditionOperator(config)] ?? []).includes(key);
}

// Switching operator drops the settings the new operator does not use, so a stale operand
// or compare_as cannot make the condition invalid.
export function withConditionOperator(
  config: Record<string, unknown>,
  operator: string,
): Record<string, unknown> {
  const used = CONDITION_SETTINGS[operator] ?? [];
  const next: Record<string, unknown> = { ...config, operator };
  for (const key of ['contains', 'compare_to', 'options', 'compare_as'])
    if (!used.includes(key)) delete next[key];
  return next;
}

// JSON settings whose text does not parse yet. The inspector keeps such text as the setting
// itself rather than the last valid value, so a workflow holding any must not be validated
// or run: what would execute is not what the screen shows.
export function unparsedJsonSettings(
  workflow: Workflow,
  catalog: Definition[],
): Issue[] {
  const definitions = new Map(catalog.map((d) => [d.type, d]));
  const problems: Issue[] = [];
  for (const node of workflow.nodes) {
    const properties = definitions.get(node.type)?.config_schema.properties ?? {};
    for (const [key, property] of Object.entries(properties)) {
      const type = (property.anyOf?.find((p) => p.type !== 'null') ?? property).type;
      if ((type === 'object' || type === 'array') && typeof node.config[key] === 'string')
        problems.push({
          node_id: node.id,
          message: `${property.title || key} is not valid JSON yet. Fix it before validating or running.`,
        });
    }
  }
  return problems;
}

// The outputs each node may bind: those of upstream nodes whose outputs exist on every flow
// path to it. Mirrors bindable_outputs in backend/app/compiler.py, and both are held to
// tests/fixtures/bindable-outputs.json. A node reached by an error edge, or through a node
// using on_error 'continue', ran without outputs on that path, so it does not count.
export function bindableOutputs(
  workflow: Pick<Workflow, 'nodes' | 'edges'>,
  outputs: (type: string) => string[],
): Record<string, string[]> {
  const byId = new Map(workflow.nodes.map((n) => [n.id, n]));
  const flow = workflow.edges.filter(
    (e) => (!e.kind || e.kind === 'flow') && byId.has(e.source) && byId.has(e.target),
  );
  // As in split_graph: the flow is the triggers plus every node a flow edge touches. Attached
  // tools and loop bodies are callables, not steps, and bind nothing.
  const ids = new Set([
    ...workflow.nodes.filter((n) => ['chat_input', 'manual_input'].includes(n.type)).map((n) => n.id),
    ...flow.flatMap((e) => [e.source, e.target]),
  ]);
  const arrivals = new Map([...ids].map((id) => [id, flow.filter((e) => e.target === id)]));
  const degree = new Map([...ids].map((id) => [id, arrivals.get(id)!.length]));
  const queue = [...ids].filter((id) => degree.get(id) === 0).sort();
  const order: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    order.push(id);
    for (const edge of flow.filter((e) => e.source === id)) {
      degree.set(edge.target, degree.get(edge.target)! - 1);
      if (degree.get(edge.target) === 0) {
        queue.push(edge.target);
        queue.sort();
      }
    }
  }
  const yields = (edge: WorkflowEdge) =>
    edge.sourceHandle !== 'error' && byId.get(edge.source)?.on_error !== 'continue';
  const available = new Map<string, Set<string>>();
  for (const id of order) {
    const edges = arrivals.get(id)!;
    const sets = edges.map(
      (e) => new Set([...(available.get(e.source) ?? []), ...(yields(e) ? [e.source] : [])]),
    );
    available.set(
      id,
      sets.length ? sets.reduce((a, b) => new Set([...a].filter((x) => b.has(x)))) : new Set(),
    );
  }
  const result: Record<string, string[]> = {};
  for (const id of order) {
    result[id] = [...available.get(id)!]
      .sort((a, b) => order.indexOf(a) - order.indexOf(b))
      .flatMap((source) =>
        outputs(byId.get(source)!.type)
          .filter((port) => !port.startsWith('_'))
          .map((port) => `${source}.${port}`),
      );
  }
  return result;
}

// Where a value came from, as labelled by the backend (backend/app/provenance.py).
export type TrustLabel = 'source' | 'quoted' | 'calculated' | 'guessed' | 'absent';
export type TrustDecision = {
  label: TrustLabel;
  would_review: boolean;
  reason?: string;
  passed?: boolean;
};
export const trustText: Record<TrustLabel, string> = {
  source: 'From outside any model',
  quoted: 'Found in the input',
  calculated: 'Calculated in code',
  guessed: 'Not confirmed: a model guess',
  absent: 'Not stated',
};
const reasonText: Record<string, string> = {
  guessed: 'the value was not found in the input',
  unlabelled: 'the value comes from an earlier run without labels',
  check_failed: 'the values disagree',
  missing_field: 'the field has no label',
  unrecorded_field: 'the field has no label',
};
const isLabel = (value: unknown): value is TrustLabel =>
  typeof value === 'string' && Object.hasOwn(trustText, value);

// The labels of one node's latest successful outputs, for the inspector: a structured
// answer's fields when it has them, otherwise its ports; and its decision, if it made one.
export function trustSummary(event: RunEvent | undefined): {
  values: { name: string; label: TrustLabel }[];
  decision?: TrustDecision & { explanation: string };
} | null {
  const record = event?.outputs?._provenance as
    | { ports?: Record<string, unknown>; fields?: Record<string, Record<string, unknown>> }
    | undefined;
  if (!record && !event?.decision) return null;
  const values: { name: string; label: TrustLabel }[] = [];
  for (const [port, label] of Object.entries(record?.ports ?? {})) {
    const fields = record?.fields?.[port];
    if (fields)
      for (const [path, fieldLabel] of Object.entries(fields)) {
        if (isLabel(fieldLabel)) values.push({ name: path, label: fieldLabel });
      }
    else if (isLabel(label)) values.push({ name: port, label });
  }
  const decision = event?.decision;
  if (!decision) return { values };
  const explanation = decision.would_review
    ? `A person would need to decide: ${reasonText[decision.reason ?? ''] ?? 'the value could not be confirmed'}.`
    : `Decided automatically on a value that is ${trustText[decision.label].toLowerCase()}.`;
  return { values, decision: { ...decision, explanation } };
}
