import type {
  Workflow,
  WorkflowNode,
  WorkflowEdge,
  RunEvent,
} from './workflow.ts';
import type { RunApproval } from './approvals.ts';

export const requestTemplates = [
  {
    id: 'qa',
    label: 'General questions',
    description:
      'Answer broad questions. Narrative replies do not authorize business actions.',
    sample: 'How do AI workflows work?',
  },
  {
    id: 'purchase',
    label: 'Purchase request',
    description:
      'Read an item, quantity and unit price, check them, then calculate the total. Product records must be connected or reviewed.',
    sample: '3 laptops at $450 each.',
  },
  {
    id: 'support',
    label: 'Support / ticket',
    description:
      'Check a summary and category before routing a ticket draft. This example creates no ticket.',
    sample: '{"summary":"Invoice download is broken","category":"billing"}',
  },
  {
    id: 'refund',
    label: 'Refund request',
    description:
      'Check the order identifier and requested amount before applying the example amount policy. Order records must be connected or reviewed.',
    sample: '{"order_id":"ORDER-1","amount":24}',
  },
  {
    id: 'yesno',
    label: 'Yes / no eligibility',
    description:
      'Keep a model classification separate from a human decision. Eligibility requires suitable record evidence or review.',
    sample: 'Is record MEMBER-1 eligible?',
  },
] as const;

const object = (properties: Record<string, unknown>) => ({
  type: 'object',
  additionalProperties: false,
  properties,
  required: Object.keys(properties),
});
const nullable = (type: string) => ({ type: [type, 'null'] });
const source = (field: string) => ({
  field: '/' + field,
  kind: 'source_field',
  source_pointer: '/' + field,
});
const quoted = (field: string) => ({ field: '/' + field, kind: 'quoted' });
// Passes when the exact value is written in the request. It proves where the value is
// written, not its role, so numbers also get a cross-check where one exists.
const accept = (field: string) => ({
  field: '/' + field,
  kind: 'accept_quoted',
});
const member = (field: string, key: string) => ({
  field: '/' + field,
  kind: 'reference_member',
  reference_list: '/issues',
  reference_key: key,
});
type Plan = {
  purpose: string;
  accepted: string[];
  excluded: string[];
  properties: Record<string, unknown>;
  required: string[];
  requiredAny?: string[][];
  rules: unknown[];
  crossChecks?: string[];
  expression?: string;
  condition?: Record<string, unknown>;
  yes?: string;
  no?: string;
};
const plans: Record<string, Plan> = {
  qa: {
    purpose:
      'Answer general questions and help explain topics. A greeting or a message with no question gets a reply.',
    accepted: ['General questions, explanations and requests for help.'],
    excluded: [
      'Requests to perform external actions or approve business transactions.',
    ],
    properties: { question: nullable('string') },
    required: ['question'],
    rules: [accept('question')],
  },
  purchase: {
    purpose:
      'Draft purchase decisions for requests with a named product and either a total or a quantity and unit price. Product identity must be checked against a catalogue or confirmed by a person.',
    accepted: ['Orders with an item and a price.'],
    excluded: ['Questions about past orders, and general questions.'],
    properties: {
      item: nullable('string'),
      quantity: { ...nullable('number'), exclusiveMinimum: 0 },
      unit_price: { ...nullable('number'), minimum: 0 },
      total: { ...nullable('number'), minimum: 0 },
    },
    required: ['item'],
    requiredAny: [['total'], ['quantity', 'unit_price']],
    rules: [
      accept('item'),
      member('item', '/fields/product'),
      accept('quantity'),
      accept('unit_price'),
      accept('total'),
    ],
    crossChecks: ['total == quantity * unit_price'],
    expression: 'coalesce(total, quantity * unit_price)',
    condition: { field: 'result', operator: 'gt', compare_to: '1000' },
    yes: 'Checked purchase exceeds $1,000. Follow the purchase review policy.',
    no: 'Checked purchase is $1,000 or less. No external action has been sent.',
  },
  support: {
    purpose:
      'Draft support tickets with a summary and a category of billing, technical or account. General unrelated questions are outside this workflow.',
    accepted: ['Support problems with a summary and a category.'],
    excluded: ['Unrelated questions and greetings without a request.'],
    properties: { summary: nullable('string'), category: nullable('string') },
    required: ['summary', 'category'],
    rules: [
      source('summary'),
      source('category'),
      {
        field: '/category',
        kind: 'enum',
        allowed_values: ['billing', 'technical', 'account'],
      },
    ],
    condition: { field: 'category', operator: 'eq', compare_to: 'billing' },
    yes: 'Checked ticket draft: billing queue. No ticket has been created.',
    no: 'Checked ticket draft: general support queue. No ticket has been created.',
  },
  refund: {
    purpose:
      'Draft refund request routing for a stated order identifier and amount. Request facts are separate from refund eligibility and payment authorization.',
    accepted: ['Refund requests with an order identifier and an amount.'],
    excluded: ['Unrelated questions and greetings without a request.'],
    properties: {
      order_id: nullable('string'),
      amount: { ...nullable('number'), minimum: 0 },
    },
    required: ['order_id', 'amount'],
    rules: [quoted('order_id'), member('order_id', '/key'), source('amount')],
    condition: { field: 'amount', operator: 'lte', compare_to: '100' },
    yes: 'Checked refund request is within the example $100 review threshold. Eligibility is not established and no refund has been issued.',
    no: 'Checked refund request exceeds the example $100 threshold. Follow the refund review policy; no refund has been issued.',
  },
  yesno: {
    purpose:
      'Draft a yes/no eligibility decision for a named record. A model inference from prose is not proof of eligibility; confirm unknown interpretations with a person.',
    accepted: ['Eligibility questions about a named record.'],
    excluded: ['Unrelated questions and greetings without a request.'],
    properties: {
      record_id: nullable('string'),
      eligible: nullable('boolean'),
    },
    required: ['record_id', 'eligible'],
    // Membership can identify a record, but cannot prove a boolean belongs to that
    // particular record. This generic example requires a person for eligibility.
    rules: [
      quoted('record_id'),
      member('record_id', '/key'),
      quoted('eligible'),
    ],
    condition: {
      field: 'eligible',
      operator: 'eq',
      compare_to: 'true',
      compare_as: 'text',
    },
    yes: 'Checked or manually confirmed interpretation: yes. No entitlement has been granted.',
    no: 'Checked or manually confirmed interpretation: no. No external action has been sent.',
  },
};

/** The Request reader settings of a template: the workflow's request contract. */
export function readerConfig(template: string): Record<string, unknown> {
  const plan = plans[template];
  const meta = requestTemplates.find((t) => t.id === template);
  if (!plan || !meta) throw new Error('Unknown workflow template.');
  return {
    purpose: plan.purpose,
    accepted_requests: plan.accepted,
    excluded_requests: plan.excluded,
    examples: [meta.sample],
    prepared_replies: [
      {
        message: 'hello',
        reply:
          'Hello! Send your request and I will check whether this workflow can help.',
      },
      {
        message: 'hello!',
        reply:
          'Hello! Send your request and I will check whether this workflow can help.',
      },
    ],
    expected_schema: object(plan.properties),
    required_fields: plan.required.map((f) => '/' + f),
    required_any: (plan.requiredAny ?? []).map((g) => g.map((f) => '/' + f)),
    field_rules: plan.rules,
    cross_checks: plan.crossChecks ?? [],
    provider: 'ollama',
    model: '',
    credential_id: '',
    max_tokens: 2048,
  };
}

/** Draft templates carry policy, never authoritative fixture data or executable approval. */
export function createRequestWorkflow(template: string = 'qa'): Workflow {
  const meta = requestTemplates.find((t) => t.id === template);
  if (!meta) throw new Error('Unknown workflow template.');
  const plan = plans[template];
  const nodes: WorkflowNode[] = [],
    edges: WorkflowEdge[] = [];
  const node = (
    id: string,
    type: string,
    label: string,
    inputs: Record<string, string>,
    config: Record<string, unknown> = {},
    x = 300,
    y?: number,
  ) => {
    nodes.push({
      id,
      type,
      version: 1,
      label,
      position: {
        x,
        y: y ?? 40 + nodes.filter((n) => n.position.x === x).length * 170,
      },
      inputs,
      config,
    });
  };
  const edge = (source: string, target: string, sourceHandle?: string) =>
    edges.push({
      id: `${source}-${target}`,
      source,
      target,
      ...(sourceHandle ? { sourceHandle } : {}),
    });
  node('input', 'chat_input', 'Chat input', {});
  const reference = plan.rules.some(
    (rule) => (rule as { kind: string }).kind === 'reference_member',
  );
  if (reference) {
    // Independent records the reader checks values against. They are read before the
    // reader, because the graph allows one flow edge out of the input.
    node(
      'records',
      'tool_jira',
      'Read independent reference records',
      { input: 'input.message' },
      {
        connection_id: '',
        operation: 'search',
        jql:
          template === 'purchase'
            ? 'project = CATALOG'
            : template === 'refund'
              ? 'project = ORDERS'
              : 'project = MEMBERS',
        enable_writes: false,
      },
    );
    edge('input', 'records');
  }
  node(
    'reader',
    'request_reader',
    'Request reader',
    {
      message: 'input.message',
      ...(reference ? { reference: 'records.text' } : {}),
    },
    readerConfig(template),
  );
  edge(reference ? 'records' : 'input', 'reader');
  if (template === 'qa') {
    node(
      'agent',
      'agent',
      'Agent',
      { input: 'input.message' },
      {
        provider: 'ollama',
        model: '',
        credential_id: '',
        max_tokens: 2048,
        role: 'reasoner',
        system:
          'Answer the user question clearly. Ask for clarification when needed. Do not claim to have taken external actions.',
        user_prompt: '{input}',
        max_steps: 6,
        memory_key: 'default',
      },
    );
    node('response', 'response', 'Response', { text: 'agent.text' });
    edge('reader', 'agent', 'continue');
    edge('agent', 'response');
  } else {
    let value = 'reader.text',
      previous = 'reader';
    if (plan.expression) {
      node(
        'calculate',
        'calculate',
        'Calculate total in code',
        { value },
        { mode: 'compute', expression: plan.expression },
      );
      edge('reader', 'calculate', 'continue');
      value = 'calculate.text';
      previous = 'calculate';
    }
    node(
      'decision',
      'condition',
      'Business condition',
      { value },
      plan.condition,
    );
    edge(previous, 'decision', previous === 'reader' ? 'continue' : undefined);
    const branchY = nodes.find((n) => n.id === 'decision')!.position.y + 170;
    node(
      'yesText',
      'prompt',
      'True branch explanation',
      { message: value },
      { template: plan.yes + '\nChecked value: {message}' },
      70,
      branchY,
    );
    node(
      'yes',
      'response',
      'True branch response',
      { text: 'yesText.text' },
      {},
      70,
      branchY + 170,
    );
    node(
      'noText',
      'prompt',
      'False branch explanation',
      { message: value },
      { template: plan.no + '\nChecked value: {message}' },
      580,
      branchY,
    );
    node(
      'no',
      'response',
      'False branch response',
      { text: 'noText.text' },
      {},
      580,
      branchY + 170,
    );
    edge('decision', 'yesText', 'true');
    edge('yesText', 'yes');
    edge('decision', 'noText', 'false');
    edge('noText', 'no');
  }
  node(
    'readerReply',
    'response',
    'Reader reply',
    { text: 'reader.reply' },
    {},
    -180,
    nodes.find((n) => n.id === 'reader')!.position.y + 90,
  );
  edge('reader', 'readerReply', 'reply');
  const workflow: Workflow = {
    version: 1,
    contract: 2,
    name: template === 'qa' ? 'My first AI workflow' : meta.label + ' draft',
    description: meta.description,
    nodes,
    edges,
  };
  // Every draft owns its nested defaults. Editing one cannot alter the next
  // draft's schema, field rules or business policy through a shared object.
  return structuredClone(workflow);
}

export type AdmissionDetail = {
  title: string;
  explanation: string;
  missingFields: string[];
  fields: { field: string; status: string; reason: string }[];
  manual: boolean;
};
const parsed = (text: unknown): Record<string, unknown> | null => {
  if (typeof text !== 'string') return null;
  try {
    const result: unknown = JSON.parse(text);
    return result && typeof result === 'object' && !Array.isArray(result)
      ? (result as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
};
export function admissionDetails(
  event: RunEvent,
  type: string,
): AdmissionDetail | null {
  if (['condition', 'calculate'].includes(type)) {
    const manual = event.outputs?._decision_review;
    if (
      !manual ||
      typeof manual !== 'object' ||
      !('id' in manual) ||
      typeof manual.id !== 'string' ||
      !('choice' in manual) ||
      typeof manual.choice !== 'boolean'
    )
      return null;
    return {
      title: 'Business decision made by a person',
      explanation:
        `A person chose ${manual.choice} for the exact reviewed input and policy. This does not change the automatic check or the value's origin label.` +
        (event.cached ? ' Restored from this run checkpoint.' : ''),
      missingFields: [],
      fields: [],
      manual: true,
    };
  }
  if (
    ![
      'greeting_cache',
      'request_router',
      'verify_extraction',
      'request_reader',
    ].includes(type)
  )
    return null;
  const detail: AdmissionDetail = {
    title: 'Request check',
    explanation:
      'Detailed check data is unavailable. Inspect the exact run data before deciding.',
    missingFields: [],
    fields: [],
    manual: false,
  };
  const outputs = event.outputs ?? {};
  if (type === 'request_reader' && outputs.reading === '') {
    detail.title = 'Prepared reply';
    detail.explanation =
      'Prepared reply from the saved configuration; no model call.' +
      (event.cached ? ' Restored from this run checkpoint.' : '');
    return detail;
  }
  if (type === 'greeting_cache') {
    const receipt = parsed(outputs.receipt);
    detail.title =
      outputs.branch === 'reply'
        ? 'Prepared reply'
        : 'Prepared messages: continue';
    // hit and filled appear only in runs recorded while a Redis copy existed.
    const outcomes: Record<string, string> = {
      hit: 'Prepared reply from the saved configuration (older run: Redis copy); no model call.',
      filled:
        'Prepared reply from the saved configuration (older run: Redis copy stored); no model call.',
      local_fallback:
        'Prepared reply from the saved configuration; no model call.',
      not_common:
        'No configured whole-message match. The original message continues unchanged.',
    };
    if (event.cached)
      detail.explanation =
        'Restored from this run checkpoint; no new lookup or model call.';
    else if (
      receipt &&
      typeof receipt.outcome === 'string' &&
      Object.hasOwn(outcomes, receipt.outcome)
    )
      detail.explanation = outcomes[receipt.outcome];
  } else if (type === 'request_router') {
    const decision = parsed(outputs.decision);
    if (
      !decision ||
      !['proceed', 'clarify', 'out_of_scope'].includes(String(decision.route))
    )
      return detail;
    detail.title =
      decision.route === 'proceed'
        ? 'Router: continue to the workflow'
        : decision.route === 'clarify'
          ? 'Router: clarification requested'
          : 'Router: outside this workflow';
    detail.explanation =
      (event.cached
        ? 'Restored model routing from this run checkpoint; no new model call. '
        : 'Model routing. ') +
      (decision.route === 'proceed'
        ? 'Admission is not verification or business approval. '
        : 'The reply is model-generated; this path ends here. ') +
      (typeof decision.reason === 'string' ? decision.reason : '');
    detail.missingFields = Array.isArray(decision.missing_fields)
      ? decision.missing_fields.filter(
          (f): f is string => typeof f === 'string',
        )
      : [];
  } else {
    const report = parsed(outputs.report);
    if (
      !report ||
      !['passed', 'clarify', 'needs_review'].includes(String(report.verdict)) ||
      !Array.isArray(report.fields) ||
      !report.fields.length ||
      !report.fields.every(
        (f) =>
          f &&
          typeof f === 'object' &&
          typeof f.field === 'string' &&
          typeof f.reason === 'string' &&
          ['passed', 'failed', 'unverified', 'absent'].includes(f.status),
      )
    )
      return detail;
    detail.fields = report.fields;
    detail.missingFields = detail.fields
      .filter((f) => f.status === 'absent')
      .map((f) => f.field);
    const receipt = outputs._verification;
    detail.manual =
      !!receipt &&
      typeof receipt === 'object' &&
      'review_id' in receipt &&
      typeof receipt.review_id === 'string' &&
      !!receipt.review_id;
    detail.title = detail.manual
      ? 'Interpretation confirmed by a person'
      : report.verdict === 'passed'
        ? 'Extraction checks passed'
        : report.verdict === 'clarify'
          ? 'Missing or malformed facts'
          : 'Interpretation needs review';
    detail.explanation = detail.manual
      ? 'A human confirmed the displayed interpretation. Automatic checks remain failed or unverified where shown; origin labels are unchanged.'
      : report.verdict === 'passed'
        ? 'Configured shape, origin and suitability checks passed for this exact value.'
        : report.verdict === 'clarify'
          ? 'Submit a new request with the required facts. This path ends here.'
          : 'Unknown meaning requires a person; it is not an automatic pass.';
    if (type === 'request_reader') {
      const reading = parsed(outputs.reading);
      const kind = reading?.type;
      if (['request', 'unclear', 'unrelated'].includes(String(kind)))
        detail.explanation += ` The model read this message as "${String(kind)}"; code checked the values and chose the branch.`;
    }
    if (event.cached)
      detail.explanation += ' Restored from this run checkpoint.';
  }
  return detail;
}

/** Shown next to accept_quoted rules: what the rule proves and what it does not. */
export const ACCEPT_QUOTED_RISK =
  'An accept_quoted rule passes a value that is written in the request. It proves where the value is written, not its role: "3 at $450" holds two numbers. Add a cross-check for numbers, and prefer a reference_member rule against real records for text.';

export type FieldSuggestion = {
  field: string;
  number: boolean;
  readers: string[];
};
const CALCULATE_WORDS = new Set([
  'min',
  'max',
  'abs',
  'round',
  'coalesce',
  'and',
  'or',
  'not',
  'true',
  'false',
  'null',
]);
/** Fields of a node's text output that later Conditions and Calculations read. */
export function fieldsReadDownstream(
  workflow: Workflow,
  nodeId: string,
): FieldSuggestion[] {
  const found = new Map<string, FieldSuggestion>();
  const add = (path: string, reader: string, number: boolean) => {
    const field = path.split('.')[0];
    if (!field) return;
    const entry = found.get(field) ?? { field, number: false, readers: [] };
    entry.number ||= number;
    if (!entry.readers.includes(reader)) entry.readers.push(reader);
    found.set(field, entry);
  };
  for (const node of workflow.nodes) {
    if (!Object.values(node.inputs).includes(nodeId + '.text')) continue;
    const reader = node.label || node.id;
    if (node.type === 'condition' && typeof node.config.field === 'string')
      add(
        node.config.field,
        reader,
        ['gt', 'gte', 'lt', 'lte'].includes(String(node.config.operator)),
      );
    if (node.type === 'calculate' && typeof node.config.expression === 'string')
      for (const match of node.config.expression.matchAll(
        /(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\b(?!\s*\()/g,
      ))
        if (!CALCULATE_WORDS.has(match[1])) add(match[1], reader, true);
  }
  return [...found.values()].sort((a, b) => a.field.localeCompare(b.field));
}

/** Add suggested fields the author accepted: a nullable property and an accept_quoted rule
 * for each new field. Required fields stay the author's choice. */
export function withSuggestedFields(
  config: Record<string, unknown>,
  suggestions: FieldSuggestion[],
): Record<string, unknown> {
  const schema = (
    config.expected_schema && typeof config.expected_schema === 'object'
      ? structuredClone(config.expected_schema)
      : { type: 'object', additionalProperties: false, properties: {} }
  ) as { type?: string; properties?: Record<string, unknown> };
  schema.type = 'object';
  const properties = (schema.properties ??= {});
  const rules = Array.isArray(config.field_rules)
    ? [...(config.field_rules as { field: string }[])]
    : [];
  for (const s of suggestions) {
    if (!Object.hasOwn(properties, s.field))
      properties[s.field] = {
        type: s.number
          ? ['number', 'null']
          : ['string', 'number', 'boolean', 'null'],
      };
    if (!rules.some((r) => r.field === '/' + s.field))
      rules.push({ field: '/' + s.field, kind: 'accept_quoted' } as never);
  }
  return { ...config, expected_schema: schema, field_rules: rules };
}

export function reviewState(
  approval: RunApproval,
  now = Date.now() / 1000,
): { canDecide: boolean; message: string } {
  const kind = approval.kind ?? 'write';
  if (!['write', 'decision', 'verification'].includes(kind))
    return {
      canDecide: false,
      message:
        'Unknown review kind. Reload the run; this review cannot be confirmed here.',
    };
  const payload = parsed(approval.payload_json);
  if (!payload || (kind !== 'write' && payload.kind !== kind))
    return {
      canDecide: false,
      message:
        'Review payload is unavailable or does not match its kind. Reload the run before deciding.',
    };
  if (!Number.isFinite(approval.expires_at))
    return {
      canDecide: false,
      message: 'Review expiry is unavailable. Reload the run before deciding.',
    };
  if (approval.expires_at <= now)
    return {
      canDecide: false,
      message:
        'This review has expired. Refresh the run status; it cannot authorize continuation.',
    };
  if (approval.status !== 'pending')
    return {
      canDecide: false,
      message: 'This review is no longer pending: ' + approval.status + '.',
    };
  return {
    canDecide: true,
    message:
      'Confirmation applies only to the exact payload below; it does not authorize a changed value.',
  };
}
