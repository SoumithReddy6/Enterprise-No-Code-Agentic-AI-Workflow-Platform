import { createRequestWorkflow } from './request-routing.ts';
import type { EventUsage } from './run-status.ts';

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
  contract?: 1 | 2;
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
  optional_inputs?: Record<string, string>;
  outputs: Record<string, string>;
  branch_routes?: string[];
  configurable_routes?: boolean;
  config_schema: {
    properties: Record<string, ConfigProperty>;
    $defs?: Record<string, { properties?: Record<string, ConfigProperty> }>;
  };
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
  usage?: EventUsage;
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
  checkpoints?: Record<string, Record<string, unknown>>;
};
export type Credential = { id: string; name: string; provider: string };
export type SavedWorkflow = { id: string; name: string; updated_at: string };

/** Avoid JSON.stringify turning an overflowing editor number into a hidden null. */
export function parseJsonSetting(text: string, type: string | undefined): unknown {
  const value: unknown = JSON.parse(text, (_key, value: unknown) => {
    if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('JSON settings require finite numbers.');
    return value;
  });
  if (type === 'object' && (value === null || Array.isArray(value) || typeof value !== 'object')) throw new Error('Enter a JSON object.');
  if (type === 'array' && !Array.isArray(value)) throw new Error('Enter a JSON array.');
  return value;
}

// Types whose routes come from each node's settings. The backend's routes_from_config is
// the authority; tests/routes.test.ts pins both to tests/fixtures/node-routes.json.
const CASE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const CONFIGURED_ROUTES: Record<string, (config: Record<string, unknown>) => string[]> = {
  // Case structure only (ids, count, uniqueness). A predicate being edited keeps its handle,
  // so its connection survives the edit; the backend still refuses the invalid predicate.
  switch: (config) => {
    const cases = config.cases;
    if (!Array.isArray(cases) || cases.length < 1 || cases.length > 15) return [];
    const ids = cases.map((c) => (c && typeof c === 'object' ? (c as { id?: unknown }).id : undefined));
    if (ids.some((id) => typeof id !== 'string' || !CASE_ID.test(id) || id === 'default' || id === 'error')) return [];
    if (new Set(ids).size !== ids.length) return [];
    return [...(ids as string[]), 'default'];
  },
};
/** The routes of one node, the only source of handles in the editor. */
export function nodeRoutes(
  definition: Definition | undefined,
  node: Pick<WorkflowNode, 'type' | 'config'>,
): string[] {
  if (!definition) return [];
  if (definition.configurable_routes)
    return CONFIGURED_ROUTES[definition.type]?.(node.config ?? {}) ?? [];
  return definition.branch_routes ?? [];
}
export const isControl = (definition?: Definition): boolean =>
  !!definition && (!!definition.configurable_routes || !!definition.branch_routes?.length);
export const inputPorts = (definition: Definition): Record<string, string> => ({ ...definition.inputs, ...definition.optional_inputs });
export function setInputBinding(definition: Definition, bindings: Record<string, string>, port: string, value: string): Record<string, string> {
  const next = { ...bindings, [port]: value };
  if (!value && Object.hasOwn(definition.optional_inputs ?? {}, port)) delete next[port];
  return next;
}

export const seed: Workflow = createRequestWorkflow('qa');

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
    contract: w.contract ?? 1,
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
  // The loop ports of a Repeat (body, check) and a For each (body). The target type is left
  // to Validate, which names the node and the rule. On other cards these names are ordinary
  // route ids (a Switch case may be named body, check or flow), so they connect as flow.
  if (isLoopType(sourceType) && (sourceHandle === 'body' || sourceHandle === 'check'))
    return isAttachmentPort(sourceType, sourceHandle) ? 'loop' : null;
  return 'flow';
}

const LOOP_TYPES = ['repeat', 'for_each'];
export const isLoopType = (type: string) => LOOP_TYPES.includes(type);
// The settings a loop edge sets: each names the node attached at that port. Nothing else in a
// loop node's settings is an attachment, whatever handle a malformed edge carries.
const ATTACHMENT_PORTS: Record<string, string[]> = { repeat: ['body', 'check'], for_each: ['body'] };
export const loopPorts = (type: string) => ATTACHMENT_PORTS[type] ?? [];
const isAttachmentPort = (type: string, port: string | null) => port !== null && loopPorts(type).includes(port);

// A new Repeat. `until` is required and has no schema default; this rule reads the `pass`
// field of a checker's JSON answer (the writer-and-judge case).
export function newRepeatConfig(): Record<string, unknown> {
  return {
    body: '', check: '', max_iterations: 3, on_limit: 'fail',
    until: { operator: 'eq', field: 'pass', compare_to: 'true' },
  };
}

// The port a saved edge occupies on its source card, from every form the backend accepts
// (backend/app/platform_graph.py and compiler.py): a Repeat's loop edges name body or check;
// an older For each loop edge may name no port (body); a flow edge may name no port or
// `output`, which on a loop card is the bottom port `flow` and elsewhere the unnamed bottom
// port. A Switch is the exception: its ports are its case ids, any of which may be `output`
// (or body, check, flow), so its handles are kept as saved. Drawing (canvasEdge) and editing
// (portInUse, removeElements) both read it, so what is drawn as connected is what the
// editor treats as connected.
export function edgePort(edge: WorkflowEdge, sourceType: string): string | null {
  const handle = edge.sourceHandle ?? null;
  if (sourceType === 'switch') return handle;
  if (edge.kind === 'loop') return sourceType === 'for_each' && handle === null ? 'body' : handle;
  if (handle !== null && handle !== 'output') return handle;
  return isLoopType(sourceType) && (!edge.kind || edge.kind === 'flow') ? 'flow' : null;
}

// The edge as drawn: its ports mapped to the card's handles. For display only; the saved
// graph keeps the handle values it was given. A target `input` is the unnamed top port.
export function canvasEdge(edge: WorkflowEdge, sourceType: string): WorkflowEdge {
  return {
    ...edge,
    sourceHandle: edgePort(edge, sourceType),
    targetHandle: edge.targetHandle === 'input' ? null : (edge.targetHandle ?? null),
  };
}

// A new connection as saved: the drawn `flow` port of a loop card is saved with no source
// handle, as every other normal edge is. On any other card `flow` is a route id (a Switch
// case) and is saved as it is.
export function savedConnection<T extends { sourceHandle?: string | null; targetHandle?: string | null }>(connection: T, sourceType: string): T {
  return isLoopType(sourceType) && connection.sourceHandle === 'flow' ? { ...connection, sourceHandle: null } : connection;
}

type Graph = { nodes: WorkflowNode[]; edges: WorkflowEdge[] };
const typeOf = (graph: Graph, id: string) => graph.nodes.find((n) => n.id === id)?.type ?? '';

// Whether a loop port (body or check) of a node already has its attachment.
export function portInUse(graph: Graph, source: string, port: string): boolean {
  return graph.edges.some((e) => e.kind === 'loop' && e.source === source && edgePort(e, typeOf(graph, source)) === port);
}

// Attach a node to a loop port: add the edge, set the loop node's body or check to it, and
// clear the attached node's input bindings (an attached callable takes no bindings).
export function withLoopAttachment(graph: Graph, edge: WorkflowEdge): Graph {
  const type = typeOf(graph, edge.source);
  const port = edgePort(edge, type);
  return {
    edges: [...graph.edges, edge],
    nodes: graph.nodes.map((n) =>
      n.id === edge.source && isAttachmentPort(type, port) ? { ...n, config: { ...n.config, [port!]: edge.target } }
        : n.id === edge.target ? { ...n, inputs: {} } : n),
  };
}

// The loop settings that named the target of a removed loop edge are cleared; a setting
// naming another node stays as it is (Validate reports the mismatch). Only attachment
// settings are cleared: a malformed loop edge whose handle names another setting (on_limit,
// say) removes the edge and leaves that setting as it is.
function withoutLoopAttachments(graph: Graph, removed: WorkflowEdge[]): Graph {
  const cleared = new Map<string, Set<string>>();
  for (const e of removed) {
    if (e.kind !== 'loop') continue;
    const type = typeOf(graph, e.source);
    const port = edgePort(e, type);
    const owner = graph.nodes.find((n) => n.id === e.source);
    if (!isAttachmentPort(type, port) || !owner || owner.config[port!] !== e.target) continue;
    cleared.set(e.source, (cleared.get(e.source) ?? new Set()).add(port!));
  }
  if (!cleared.size) return graph;
  return {
    ...graph,
    nodes: graph.nodes.map((n) => cleared.has(n.id)
      ? { ...n, config: { ...n.config, ...Object.fromEntries([...cleared.get(n.id)!].map((p) => [p, ''])) } }
      : n),
  };
}

// Remove nodes and edges as one change: the listed edges, every edge of a removed node, and
// each loop setting that named a removed attachment.
export function removeElements(graph: Graph, nodeIds: string[], edgeIds: string[]): Graph {
  const nodes = new Set(nodeIds), edges = new Set(edgeIds);
  const removed = graph.edges.filter((e) => edges.has(e.id) || nodes.has(e.source) || nodes.has(e.target));
  const kept = withoutLoopAttachments(graph, removed);
  return {
    nodes: kept.nodes.filter((n) => !nodes.has(n.id)),
    edges: graph.edges.filter((e) => !removed.includes(e)),
  };
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
  // These stay loadable for saved workflows but are not offered. The Request reader
  // replaces the greeting cache, router and separate extraction check.
  if (
    [
      'prompt',
      'llm',
      'greeting_cache',
      'request_router',
      'verify_extraction',
    ].includes(type)
  )
    return null;
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
        join: 'Control',
        request_reader: 'Control',
        switch: 'Control',
        repeat: 'Control',
        response: 'Output',
      } as Record<string, string>
    )[type] || null
  );
}

// How many branches arrive at a Join: the distinct sources of its normal flow edges. Error
// routes, attached tools and specialists do not count, and a repeated edge counts once.
// Validate decides whether the branches are correct.
export function joinArrivals(edges: WorkflowEdge[], join: string): number {
  return new Set(
    edges
      .filter((e) => e.target === join && (!e.kind || e.kind === 'flow') && e.sourceHandle !== 'error')
      .map((e) => e.source),
  ).size;
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
  const isJson = (property: ConfigProperty) => {
    const type = (property.anyOf?.find((p) => p.type !== 'null') ?? property).type;
    return type === 'object' || type === 'array';
  };
  for (const node of workflow.nodes) {
    const definition = definitions.get(node.type);
    const properties = definition?.config_schema.properties ?? {};
    for (const [key, property] of Object.entries(properties)) {
      if (isJson(property) && typeof node.config[key] === 'string')
        problems.push({
          node_id: node.id,
          message: `${property.title || key} is not valid JSON yet. Fix it before validating or running.`,
        });
    }
    // A Switch case's rule and a Repeat's stop rule are nested Conditions: their JSON
    // settings (Options) need the same check.
    const predicate = definition?.config_schema.$defs?.ConditionConfig?.properties ?? {};
    const rule = (value: unknown, name: string) => {
      if (!value || typeof value !== 'object') return;
      for (const [key, property] of Object.entries(predicate))
        if (isJson(property) && typeof (value as Record<string, unknown>)[key] === 'string')
          problems.push({
            node_id: node.id,
            message: `${name} ${property.title || key} is not valid JSON yet. Fix it before validating or running.`,
          });
    };
    if (node.type === 'switch' && Array.isArray(node.config.cases))
      node.config.cases.forEach((entry: unknown, index: number) =>
        rule(entry && typeof entry === 'object' ? (entry as { when?: unknown }).when : undefined, `Case ${index + 1}`));
    if (node.type === 'repeat') rule(node.config.until, 'Stop rule');
  }
  return problems;
}

// The outputs each node may bind: those of upstream nodes whose outputs exist on every flow
// path to it. Mirrors bindable_outputs in backend/app/compiler.py, and both are held to
// tests/fixtures/bindable-outputs.json. A node reached by an error edge, or through a node
// using on_error 'continue', ran without outputs on that path, so it does not count. A Join
// runs after every branch of its fork, so it takes the union of its arrivals.
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
    const join = byId.get(id)?.type === 'join';
    available.set(
      id,
      sets.length
        ? sets.reduce((a, b) => new Set(join ? [...a, ...b] : [...a].filter((x) => b.has(x))))
        : new Set(),
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
