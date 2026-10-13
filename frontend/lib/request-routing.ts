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
const member = (field: string, key: string) => ({
  field: '/' + field,
  kind: 'reference_member',
  reference_list: '/issues',
  reference_key: key,
});
const plans: Record<
  string,
  {
    purpose: string;
    properties: Record<string, unknown>;
    rules: unknown[];
    expression?: string;
    condition: Record<string, unknown>;
    yes: string;
    no: string;
  }
> = {
  purchase: {
    purpose:
      'Draft purchase decisions for requests with a named product, quantity and unit price. Product identity must be checked against a catalogue or confirmed by a person.',
    properties: {
      item: nullable('string'),
      quantity: { ...nullable('number'), exclusiveMinimum: 0 },
      unit_price: { ...nullable('number'), minimum: 0 },
    },
    rules: [
      quoted('item'),
      member('item', '/fields/product'),
      source('quantity'),
      source('unit_price'),
    ],
    expression: 'quantity * unit_price',
    condition: { field: 'result', operator: 'gt', compare_to: '1000' },
    yes: 'Checked purchase exceeds $1,000. Follow the purchase review policy.',
    no: 'Checked purchase is $1,000 or less. No external action has been sent.',
  },
  support: {
    purpose:
      'Draft support tickets with a summary and a category of billing, technical or account. General unrelated questions are outside this workflow.',
    properties: { summary: nullable('string'), category: nullable('string') },
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
    properties: {
      order_id: nullable('string'),
      amount: { ...nullable('number'), minimum: 0 },
    },
    rules: [quoted('order_id'), member('order_id', '/key'), source('amount')],
    condition: { field: 'amount', operator: 'lte', compare_to: '100' },
    yes: 'Checked refund request is within the example $100 review threshold. Eligibility is not established and no refund has been issued.',
    no: 'Checked refund request exceeds the example $100 threshold. Follow the refund review policy; no refund has been issued.',
  },
  yesno: {
    purpose:
      'Draft a yes/no eligibility decision for a named record. A model inference from prose is not proof of eligibility; confirm unknown interpretations with a person.',
    properties: {
      record_id: nullable('string'),
      eligible: nullable('boolean'),
    },
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

/** Draft templates carry policy, never authoritative fixture data or executable approval. */
export function createRequestWorkflow(template: string = 'qa'): Workflow {
  const meta = requestTemplates.find((t) => t.id === template);
  if (!meta) throw new Error('Unknown workflow template.');
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
  const model = {
    provider: 'ollama',
    model: '',
    credential_id: '',
    max_tokens: 2048,
  };
  node('input', 'chat_input', 'Chat input', {});
  node(
    'cache',
    'greeting_cache',
    'Prepared greetings',
    { message: 'input.message' },
    {
      entries: [
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
    },
  );
  node(
    'router',
    'request_router',
    'Request router',
    { message: 'input.message' },
    {
      ...model,
      purpose:
        plans[template]?.purpose ??
        'Answer general questions and help explain topics. Accept broad questions; request clarification when there is no question or request.',
      accepted_requests:
        template === 'qa'
          ? ['General questions, explanations and requests for help.']
          : [meta.description],
      exclusions:
        template === 'qa'
          ? [
              'Requests to perform external actions or approve business transactions.',
            ]
          : [
              'Unrelated questions, greetings without a request, and invented facts.',
            ],
      clarification_rules: [
        'Ask for missing required facts; never fill them with guesses or zero defaults.',
      ],
      examples: [meta.sample],
    },
  );
  edge('input', 'cache');
  edge('cache', 'router', 'continue');
  if (template === 'qa') {
    node(
      'agent',
      'agent',
      'Agent',
      { input: 'input.message' },
      {
        ...model,
        role: 'reasoner',
        system:
          'Answer the user question clearly. Ask for clarification when needed. Do not claim to have taken external actions.',
        user_prompt: '{input}',
        max_steps: 6,
        memory_key: 'default',
      },
    );
    node('response', 'response', 'Response', { text: 'agent.text' });
    edge('router', 'agent', 'continue');
    edge('agent', 'response');
  } else {
    const plan = plans[template];
    const reference = plan.rules.some(
      (rule) => (rule as { kind: string }).kind === 'reference_member',
    );
    if (reference) {
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
      edge('router', 'records', 'continue');
    }
    node(
      'extract',
      'agent',
      'Extract request facts',
      { input: 'input.message' },
      {
        ...model,
        role: template === 'yesno' ? 'classification' : 'extraction',
        system: `Return only a JSON object with these fields: ${Object.keys(plan.properties).join(', ')}. Copy only what the original request states; use null for absent facts. Never invent defaults. Do not approve anything or calculate totals.`,
        user_prompt: '{input}',
        max_steps: 6,
        memory_key: 'default',
        output_schema: object(plan.properties),
      },
    );
    node(
      'verify',
      'verify_extraction',
      'Check extracted facts',
      {
        original: 'input.message',
        value: 'extract.text',
        ...(reference ? { reference: 'records.text' } : {}),
      },
      {
        expected_schema: object(plan.properties),
        required_fields: Object.keys(plan.properties).map((f) => '/' + f),
        field_rules: plan.rules,
        cross_checks: [],
      },
    );
    edge(
      reference ? 'records' : 'router',
      'extract',
      reference ? undefined : 'continue',
    );
    edge('extract', 'verify');
    let value = 'verify.text',
      previous = 'verify';
    if (plan.expression) {
      node(
        'calculate',
        'calculate',
        'Calculate total in code',
        { value },
        { mode: 'compute', expression: plan.expression },
      );
      edge('verify', 'calculate', 'continue');
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
    edge(previous, 'decision', previous === 'verify' ? 'continue' : undefined);
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
  for (const source of [
    'cache',
    'router',
    ...(template === 'qa' ? [] : ['verify']),
  ]) {
    node(
      source + 'Reply',
      'response',
      source === 'cache'
        ? 'Prepared reply'
        : source === 'router'
          ? 'Router reply'
          : 'Missing facts reply',
      { text: source + '.reply' },
      {},
      -180,
      nodes.find((n) => n.id === source)!.position.y + 90,
    );
    edge(source, source + 'Reply', 'reply');
  }
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
  if (!['greeting_cache', 'request_router', 'verify_extraction'].includes(type))
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
  if (type === 'greeting_cache') {
    const receipt = parsed(outputs.receipt);
    detail.title =
      outputs.branch === 'reply'
        ? 'Prepared reply'
        : 'Prepared messages: continue';
    const outcomes: Record<string, string> = {
      hit: 'Prepared reply from the saved configuration (Redis hit); no model call.',
      filled:
        'Prepared reply from the saved configuration; Redis populated, no model call.',
      local_fallback:
        'Prepared reply from local configuration; Redis was not used, no model call.',
      not_common:
        'No configured whole-message match. The original message continues unchanged.',
    };
    if (event.cached)
      detail.explanation =
        'Restored from this run checkpoint; no new Redis lookup or model call.';
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
    if (event.cached)
      detail.explanation += ' Restored from this run checkpoint.';
  }
  return detail;
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
