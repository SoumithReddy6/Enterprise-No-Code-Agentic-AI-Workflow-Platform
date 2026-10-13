import test from 'node:test';
import assert from 'node:assert/strict';
import { seed, paletteCategory, parseJsonSetting } from '../lib/workflow.ts';
import { approvalDecision, type RunApproval } from '../lib/approvals.ts';
import {
  createRequestWorkflow,
  requestTemplates,
  admissionDetails,
  reviewState,
} from '../lib/request-routing.ts';

void test('new narrative workflow has admission control without pretending narrative text is a verified business fact', () => {
  assert.equal(seed.contract, 2);
  assert.deepEqual(
    seed.nodes.slice(0, 3).map((n) => n.type),
    ['chat_input', 'greeting_cache', 'request_router'],
  );
  assert.ok(seed.nodes.some((n) => n.type === 'agent'));
  assert.ok(!seed.nodes.some((n) => n.type === 'verify_extraction'));
  const router = seed.nodes.find((n) => n.type === 'request_router')!;
  assert.match(String(router.config.purpose), /questions/i);
  assert.ok(
    seed.edges.some(
      (e) => e.source === router.id && e.sourceHandle === 'reply',
    ),
  );
});

void test('all admission nodes are available in the control palette', () => {
  for (const type of ['greeting_cache', 'request_router', 'verify_extraction'])
    assert.equal(paletteCategory(type), 'Control');
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

void test('business templates have an explicit check, original-input binding and terminating reply branches', () => {
  for (const template of requestTemplates.filter((t) => t.id !== 'qa')) {
    const flow = createRequestWorkflow(template.id);
    assert.equal(flow.contract, 2);
    const check = flow.nodes.find((n) => n.type === 'verify_extraction')!;
    assert.ok(check, template.id);
    assert.equal(check.inputs.original, 'input.message');
    assert.ok(
      Array.isArray(check.config.required_fields) &&
        check.config.required_fields.length > 0,
    );
    for (const control of flow.nodes.filter((n) =>
      ['greeting_cache', 'request_router', 'verify_extraction'].includes(
        n.type,
      ),
    )) {
      const replies = flow.edges.filter(
        (e) => e.source === control.id && e.sourceHandle === 'reply',
      );
      assert.equal(replies.length, 1);
      assert.equal(
        flow.nodes.find((n) => n.id === replies[0].target)!.type,
        'response',
      );
    }
    assert.ok(
      !flow.nodes.some((n) => n.config.enable_writes === true),
      'draft examples configure no external writes',
    );
    if (
      (check.config.field_rules as { kind: string }[]).some(
        (r) => r.kind === 'reference_member',
      )
    ) {
      assert.equal(check.inputs.reference, 'records.text');
      assert.equal(
        flow.nodes.find((n) => n.id === 'records')!.config.operation,
        'search',
      );
    }
  }
});

void test('each new template is independent and no preset treats yes/no as a greeting', () => {
  const first = createRequestWorkflow('purchase');
  first.nodes[1].config.entries = [];
  const firstCheck = first.nodes.find((n) => n.type === 'verify_extraction')!;
  (firstCheck.config.field_rules as { field: string }[])[0].field = '/other';
  (
    firstCheck.config.expected_schema as {
      properties: { quantity: { exclusiveMinimum: number } };
    }
  ).properties.quantity.exclusiveMinimum = -100;
  first.nodes.find((n) => n.type === 'condition')!.config.compare_to = '-1';
  const second = createRequestWorkflow('purchase');
  assert.ok((second.nodes[1].config.entries as unknown[]).length > 0);
  const secondCheck = second.nodes.find((n) => n.type === 'verify_extraction')!;
  assert.equal(
    (secondCheck.config.field_rules as { field: string }[])[0].field,
    '/item',
  );
  assert.equal(
    (
      secondCheck.config.expected_schema as {
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
    !(second.nodes[1].config.entries as { message: string }[]).some((e) =>
      /^(yes|no)$/i.test(e.message),
    ),
  );
  assert.throws(
    () => createRequestWorkflow('unrecognised'),
    /unknown.*template/i,
  );
});

const event = (outputs: Record<string, unknown>, cached = false) => ({
  seq: 1,
  node_id: 'node',
  status: 'success',
  timestamp: '',
  outputs,
  cached,
});

void test('prepared replies distinguish Redis hit, fill, local fallback and checkpoint replay', () => {
  for (const outcome of ['hit', 'filled', 'local_fallback']) {
    const details = admissionDetails(
      event({ branch: 'reply', receipt: JSON.stringify({ v: 1, outcome }) }),
      'greeting_cache',
    )!;
    assert.match(details.title, /prepared reply/i);
    assert.match(
      details.explanation,
      outcome === 'hit'
        ? /Redis hit/i
        : outcome === 'filled'
          ? /Redis populated/i
          : /local configuration/i,
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
  assert.match(replay.explanation, /checkpoint.*no new Redis/i);
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
