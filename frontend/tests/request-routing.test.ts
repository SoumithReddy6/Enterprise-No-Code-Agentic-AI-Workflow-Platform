import test from 'node:test';
import assert from 'node:assert/strict';
import { seed, paletteCategory, parseJsonSetting } from '../lib/workflow.ts';
import { approvalDecision, type RunApproval } from '../lib/approvals.ts';
import {
  createRequestWorkflow,
  requestTemplates,
  admissionDetails,
  reviewState,
  fieldsReadDownstream,
  withSuggestedFields,
  ACCEPT_QUOTED_RISK,
} from '../lib/request-routing.ts';

void test('new narrative workflow starts with a request reader without pretending narrative text is a verified business fact', () => {
  assert.equal(seed.contract, 2);
  assert.deepEqual(
    seed.nodes.slice(0, 2).map((n) => n.type),
    ['chat_input', 'request_reader'],
  );
  assert.ok(seed.nodes.some((n) => n.type === 'agent'));
  for (const old of ['greeting_cache', 'request_router', 'verify_extraction'])
    assert.ok(!seed.nodes.some((n) => n.type === old));
  const reader = seed.nodes.find((n) => n.type === 'request_reader')!;
  assert.match(String(reader.config.purpose), /questions/i);
  assert.ok(
    seed.edges.some(
      (e) => e.source === reader.id && e.sourceHandle === 'reply',
    ),
  );
});

void test('the palette offers the request reader and hides the replaced admission nodes', () => {
  assert.equal(paletteCategory('request_reader'), 'Control');
  for (const type of ['greeting_cache', 'request_router', 'verify_extraction'])
    assert.equal(paletteCategory(type), null);
});

void test('JSON settings refuse overflowing numbers instead of silently saving null', () => {
  for (const text of ['{"maximum":1e309}', '{"nested":[-1e309]}', '[1e309]'])
    assert.throws(
      () => parseJsonSetting(text, text.startsWith('[') ? 'array' : 'object'),
      /finite/i,
    );
  assert.deepEqual(
    parseJsonSetting('{"maximum":1000,"note":"1e309"}', 'object'),
    { maximum: 1000, note: '1e309' },
  );
  assert.throws(() => parseJsonSetting('[]', 'object'), /object/i);
  assert.throws(() => parseJsonSetting('{}', 'array'), /array/i);
});

const approval = (kind: string): RunApproval => ({
  id: 'review',
  node_id: 'check',
  kind,
  status: 'pending',
  expires_at: 4_000_000_000,
  payload_json: JSON.stringify({
    kind,
    original: 'Request',
    value: '{"amount":9007199254740993}',
  }),
});

void test('decision review requires a deliberate boolean and hashes the same payload for either branch', async () => {
  await assert.rejects(
    approvalDecision(approval('decision')),
    /explicit.*true.*false/i,
  );
  const yes = await approvalDecision(approval('decision'), true);
  const no = await approvalDecision(approval('decision'), false);
  assert.deepEqual(no, { ...yes, choice: false });
  assert.equal(yes.choice, true);
});

void test('verification and write confirmations cannot accidentally include a branch choice', async () => {
  for (const kind of ['verification', 'write']) {
    assert.deepEqual(
      Object.keys(await approvalDecision(approval(kind))).sort(),
      ['approval_id', 'digest'],
    );
    await assert.rejects(
      approvalDecision(approval(kind), false),
      /only.*decision/i,
    );
  }
});

void test('rejecting a decision ends the run without selecting either branch', async () => {
  assert.deepEqual(
    Object.keys(
      await approvalDecision(approval('decision'), undefined, 'reject'),
    ).sort(),
    ['approval_id', 'digest'],
  );
});

void test('business templates read the original input once and terminate the reply branch', () => {
  for (const template of requestTemplates) {
    const flow = createRequestWorkflow(template.id);
    assert.equal(flow.contract, 2);
    const reader = flow.nodes.find((n) => n.type === 'request_reader')!;
    assert.ok(reader, template.id);
    assert.equal(reader.inputs.message, 'input.message');
    assert.ok(
      (reader.config.required_fields as unknown[]).length > 0 ||
        (reader.config.required_any as unknown[]).length > 0,
    );
    const replies = flow.edges.filter(
      (e) => e.source === reader.id && e.sourceHandle === 'reply',
    );
    assert.equal(replies.length, 1);
    assert.equal(
      flow.nodes.find((n) => n.id === replies[0].target)!.type,
      'response',
    );
    assert.ok(
      !flow.nodes.some((n) => n.config.enable_writes === true),
      'draft examples configure no external writes',
    );
    // Every extracted field has a rule, so no field is left unchecked.
    const fields = Object.keys(
      (reader.config.expected_schema as { properties: object }).properties,
    );
    const ruled = (reader.config.field_rules as { field: string }[]).map(
      (r) => r.field,
    );
    assert.deepEqual(
      fields.filter((f) => !ruled.includes('/' + f)),
      [],
      template.id,
    );
    if (
      (reader.config.field_rules as { kind: string }[]).some(
        (r) => r.kind === 'reference_member',
      )
    ) {
      assert.equal(reader.inputs.reference, 'records.text');
      assert.equal(
        flow.nodes.find((n) => n.id === 'records')!.config.operation,
        'search',
      );
      assert.ok(
        flow.edges.some((e) => e.source === 'input' && e.target === 'records'),
      );
    }
  }
});

void test('the purchase template accepts a total or a quantity with a unit price', () => {
  const reader = createRequestWorkflow('purchase').nodes.find(
    (n) => n.type === 'request_reader',
  )!;
  assert.deepEqual(reader.config.required_fields, ['/item']);
  assert.deepEqual(reader.config.required_any, [
    ['/total'],
    ['/quantity', '/unit_price'],
  ]);
  assert.deepEqual(reader.config.cross_checks, [
    'total == quantity * unit_price',
  ]);
});

void test('each new template is independent and no preset treats yes/no as a greeting', () => {
  const first = createRequestWorkflow('purchase');
  const firstReader = first.nodes.find((n) => n.type === 'request_reader')!;
  firstReader.config.prepared_replies = [];
  (firstReader.config.field_rules as { field: string }[])[0].field = '/other';
  (
    firstReader.config.expected_schema as {
      properties: { quantity: { exclusiveMinimum: number } };
    }
  ).properties.quantity.exclusiveMinimum = -100;
  first.nodes.find((n) => n.type === 'condition')!.config.compare_to = '-1';
  const second = createRequestWorkflow('purchase');
  const secondReader = second.nodes.find((n) => n.type === 'request_reader')!;
  assert.ok((secondReader.config.prepared_replies as unknown[]).length > 0);
  assert.equal(
    (secondReader.config.field_rules as { field: string }[])[0].field,
    '/item',
  );
  assert.equal(
    (
      secondReader.config.expected_schema as {
        properties: { quantity: { exclusiveMinimum: number } };
      }
    ).properties.quantity.exclusiveMinimum,
    0,
  );
  assert.equal(
    second.nodes.find((n) => n.type === 'condition')!.config.compare_to,
    '1000',
  );
  assert.ok(
    !(secondReader.config.prepared_replies as { message: string }[]).some(
      (e) => /^(yes|no)$/i.test(e.message),
    ),
  );
  assert.throws(
    () => createRequestWorkflow('unrecognised'),
    /unknown.*template/i,
  );
});

void test('field suggestions come from later Conditions and Calculations, never from unrelated nodes', () => {
  const flow = createRequestWorkflow('purchase');
  const suggestions = fieldsReadDownstream(flow, 'reader');
  assert.deepEqual(
    suggestions.map((s) => [s.field, s.number, s.readers]),
    [
      ['quantity', true, ['Calculate total in code']],
      ['total', true, ['Calculate total in code']],
      ['unit_price', true, ['Calculate total in code']],
    ],
  );
  // The condition reads the calculation, not the reader: result is not a reader field.
  assert.ok(!suggestions.some((s) => s.field === 'result'));
  const support = fieldsReadDownstream(createRequestWorkflow('support'), 'reader');
  assert.deepEqual(
    support.map((s) => [s.field, s.number]),
    [['category', false]],
  );
  const calc = createRequestWorkflow('purchase');
  calc.nodes.find((n) => n.id === 'calculate')!.config.expression =
    'round(max(order.total, 1e5), 2) + abs(fee)';
  assert.deepEqual(
    fieldsReadDownstream(calc, 'reader').map((s) => s.field),
    ['fee', 'order'],
  );
});

void test('accepting suggestions adds a nullable field and an accept_quoted rule, and never sets Required', () => {
  const config = {
    purpose: 'Orders',
    expected_schema: {
      type: 'object',
      properties: { item: { type: ['string', 'null'] } },
    },
    required_fields: ['/item'],
    field_rules: [{ field: '/item', kind: 'accept_quoted' }],
  };
  const next = withSuggestedFields(config, [
    { field: 'item', number: false, readers: ['x'] },
    { field: 'total', number: true, readers: ['x'] },
  ]);
  assert.deepEqual(
    (next.expected_schema as { properties: object }).properties,
    {
      item: { type: ['string', 'null'] },
      total: { type: ['number', 'null'] },
    },
  );
  assert.deepEqual(next.field_rules, [
    { field: '/item', kind: 'accept_quoted' },
    { field: '/total', kind: 'accept_quoted' },
  ]);
  assert.deepEqual(next.required_fields, ['/item']);
  assert.equal(
    (config.expected_schema.properties as Record<string, unknown>).total,
    undefined,
  );
});

void test('the accept_quoted risk note says it proves location, not role', () => {
  assert.match(ACCEPT_QUOTED_RISK, /not its role/);
  assert.match(ACCEPT_QUOTED_RISK, /cross-check/);
});

const event = (outputs: Record<string, unknown>, cached = false) => ({
  seq: 1,
  node_id: 'node',
  status: 'success',
  timestamp: '',
  outputs,
  cached,
});

void test('prepared replies from configuration, older Redis records and checkpoint replay stay distinct', () => {
  for (const outcome of ['hit', 'filled', 'local_fallback']) {
    const details = admissionDetails(
      event({ branch: 'reply', receipt: JSON.stringify({ v: 1, outcome }) }),
      'greeting_cache',
    )!;
    assert.match(details.title, /prepared reply/i);
    assert.match(
      details.explanation,
      outcome === 'hit'
        ? /older run: Redis copy\)/i
        : outcome === 'filled'
          ? /Redis copy stored/i
          : /^Prepared reply from the saved configuration; no model call\.$/,
    );
  }
  assert.match(
    admissionDetails(
      event({ branch: 'continue', receipt: '{"v":1,"outcome":"not_common"}' }),
      'greeting_cache',
    )!.explanation,
    /original.*unchanged/i,
  );
  const replay = admissionDetails(
    event({ branch: 'reply', receipt: '{"v":1,"outcome":"hit"}' }, true),
    'greeting_cache',
  )!;
  assert.match(replay.explanation, /checkpoint.*no new lookup or model call/i);
});

void test('router proceed is admission only; reply and missing-field explanations remain model-labelled', () => {
  for (const route of ['proceed', 'clarify', 'out_of_scope']) {
    const details = admissionDetails(
      event({
        decision: JSON.stringify({
          route,
          reason: 'Saved reason',
          missing_fields: route === 'clarify' ? ['quantity'] : [],
          reply: '',
        }),
      }),
      'request_router',
    )!;
    assert.match(details.explanation, /model/i);
    if (route === 'proceed') {
      assert.match(details.explanation, /not.*verification.*approval/i);
      assert.equal(details.title, 'Router: continue to the workflow');
    }
    assert.deepEqual(
      details.missingFields,
      route === 'clarify' ? ['quantity'] : [],
    );
  }
});

void test('verification shows actual field outcomes and manual confirmation without rewriting failed checks', () => {
  const fields = [
    { field: '/amount', status: 'unverified', reason: 'origin_unconfirmed' },
  ];
  const details = admissionDetails(
    event({
      report: JSON.stringify({
        verdict: 'needs_review',
        complete: true,
        fields,
      }),
      _verification: { review_id: 'human-review' },
    }),
    'verify_extraction',
  )!;
  assert.equal(details.manual, true);
  assert.deepEqual(details.fields, fields);
  assert.match(details.explanation, /human.*automatic checks.*unverified/i);
  assert.equal(
    admissionDetails(
      event({
        report:
          '{"verdict":"clarify","fields":[{"field":"/amount","status":"absent","reason":"required_absent"}]}',
      }),
      'verify_extraction',
    )!.fields[0].status,
    'absent',
  );
});

void test('reader details separate a prepared reply, the model reading and the code decision', () => {
  const prepared = admissionDetails(
    event({ branch: 'reply', reading: '', report: '' }),
    'request_reader',
  )!;
  assert.equal(prepared.title, 'Prepared reply');
  assert.match(prepared.explanation, /no model call/i);
  const fields = [
    { field: '/item', status: 'passed', reason: 'source_checked' },
    { field: '/total', status: 'absent', reason: 'value_absent' },
  ];
  const checked = admissionDetails(
    event({
      branch: 'continue',
      reading: JSON.stringify({ type: 'unrelated', values: {}, reply: '' }),
      report: JSON.stringify({ verdict: 'passed', complete: true, fields }),
      _verification: { review_id: 'human-review' },
    }),
    'request_reader',
  )!;
  assert.equal(checked.manual, true);
  assert.match(checked.explanation, /read this message as "unrelated"/);
  assert.match(checked.explanation, /code checked the values and chose the branch/);
  assert.deepEqual(checked.missingFields, ['/total']);
});

void test('malformed or previewed reports are visible as unavailable, never as a verified pass', () => {
  for (const report of ['not json', 'null', '{"verdict":"passed","fields":[]}'])
    assert.match(
      admissionDetails(event({ report }), 'verify_extraction')!.explanation,
      /unavailable/i,
    );
  assert.equal(
    admissionDetails(event({ text: 'ordinary answer' }), 'agent'),
    null,
  );
});

void test('a completed human branch remains visibly manual, independently of the automatic proposal', () => {
  for (const choice of [true, false]) {
    const details = admissionDetails(
      event({
        branch: String(choice),
        _decision_review: {
          id: 'review',
          choice,
          payload_digest: 'a'.repeat(64),
        },
      }),
      'condition',
    )!;
    assert.equal(details.manual, true);
    assert.match(details.title, /decision.*person/i);
    assert.match(details.explanation, new RegExp(`chose ${choice}`));
    assert.match(details.explanation, /does not.*automatic check/i);
  }
});

void test('review validity uses the exact displayed payload kind and expiry and fails closed on unknown kinds', () => {
  assert.equal(reviewState(approval('verification'), 1).canDecide, true);
  assert.equal(
    reviewState({ ...approval('verification'), expires_at: 1 }, 1).canDecide,
    false,
  );
  assert.match(
    reviewState({ ...approval('verification'), expires_at: 1 }, 1).message,
    /expired/i,
  );
  assert.equal(reviewState(approval('unknown'), 1).canDecide, false);
  assert.equal(
    reviewState(
      { ...approval('decision'), payload_json: '{"kind":"verification"}' },
      1,
    ).canDecide,
    false,
  );
  assert.equal(
    reviewState({ ...approval('decision'), status: 'completed' }, 1).canDecide,
    false,
  );
  assert.equal(
    reviewState({ ...approval('verification'), expires_at: NaN }, 1).canDecide,
    false,
  );
});
