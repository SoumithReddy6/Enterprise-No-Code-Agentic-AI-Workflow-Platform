import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequestWorkflow } from '../../lib/request-routing';
import type { Run, Workflow } from '../../lib/workflow';

const catalog = JSON.parse(
  readFileSync(new URL('./request-nodes.json', import.meta.url), 'utf8'),
);
async function fixture(page: Page, runs: Run[] = [], failure = '') {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  await page.route('**/api/**', async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    const data: Record<string, unknown> = {
      '/api/auth/status': { enabled: false },
      '/api/nodes': catalog,
      '/api/workflows': [],
      '/api/credentials': [],
      '/api/models': [
        {
          id: 'local',
          provider: 'ollama',
          model: 'fixture',
          credential_id: '',
          enabled: true,
        },
      ],
      '/api/connections': [],
      '/api/knowledge-bases': [],
      '/api/runs/page': { items: runs, next_cursor: null },
      ...Object.fromEntries(runs.map((r) => ['/api/runs/' + r.id, r])),
    };
    if (request.method() === 'POST') {
      const body = request.postDataJSON();
      calls.push({ path, body });
      if (path === '/api/validate') {
        await route.fulfill({
          json: { valid: true, errors: [], workflow: body },
        });
        return;
      }
      if (path === '/api/workflows') {
        await route.fulfill({ json: { id: 'saved', updated_at: 'now' } });
        return;
      }
      if (/\/(approve|reject)$/.test(path)) {
        await route.fulfill({
          status: failure ? 409 : 200,
          json: failure ? { detail: failure } : {},
        });
        return;
      }
    }
    if (!Object.hasOwn(data, path))
      throw new Error('Unexpected request: ' + request.method() + ' ' + path);
    await route.fulfill({ json: data[path] });
  });
  page.on('dialog', (d) => void d.accept());
  await page.goto('/');
  await expect(
    page.getByRole('button', { name: 'Chat input', exact: true }),
  ).toBeVisible();
  return calls;
}

test('new business setup saves the displayed purpose and strict rules; invalid drafts save but cannot validate or run', async ({
  page,
}) => {
  const calls = await fixture(page);
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page.getByRole('button', { name: 'New workflow', exact: true }).click();
  await page
    .getByLabel('Workflow template', { exact: true })
    .selectOption('purchase');
  await page
    .getByLabel('Purpose', { exact: true })
    .fill('Draft purchase decisions using checked facts');
  await expect(
    page.getByRole('textbox', { name: 'Required fields', exact: true }),
  ).toBeVisible();
  await page
    .getByRole('button', { name: 'Create workflow', exact: true })
    .click();
  await expect(page.locator('[data-id="verify"]')).toBeVisible();
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByText('Workflow is valid and ready to run.', { exact: true })).toBeVisible();
  const saved = calls.at(-1)!.body as Workflow;
  expect(saved.contract).toBe(2);
  expect(
    saved.nodes.find((n) => n.type === 'request_router')!.config,
  ).toMatchObject({
    purpose: 'Draft purchase decisions using checked facts',
    model: 'fixture',
  });
  expect(saved.nodes.find((n) => n.type === 'agent')!.config.model).toBe(
    'fixture',
  );
  await page.locator('[data-id="verify"]').getByText('Check extracted facts', { exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Verify extraction', exact: true })).toBeVisible();
  await page.getByLabel(/^Field rules/i).fill('[broken');
  const before = calls.length;
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(
    page.getByRole('button', { name: /Field rules is not valid JSON/i }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Run workflow', exact: true }).click();
  expect(calls.length).toBe(before);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => calls.length).toBe(before + 1);
  expect(
    (calls.at(-1)!.body as Workflow).nodes.find((n) => n.id === 'verify')!
      .config.field_rules,
  ).toBe('[broken');
});

function reviewRun(
  kind: 'verification' | 'decision' | 'write',
  expires = 4_000_000_000,
): Run {
  const payload =
    kind === 'write'
      ? {
          method: 'POST',
          url: 'https://example.invalid/send',
          body: 'Exact body',
        }
      : {
          kind,
          original: 'Original input with <script>literal text</script>',
          value: '{"amount":9007199254740993}',
          report: {
            verdict: 'needs_review',
            complete: true,
            fields: [
              {
                field: '/amount',
                status: 'unverified',
                reason: 'origin_unconfirmed',
              },
            ],
          },
          inputs: { value: '{"amount":9007199254740993}' },
          check: { proposed_choice: false, error: '' },
          policy: { expression: 'amount > 1000' },
        };
  return {
    id: 'review',
    name: 'Review fixture',
    status: 'awaiting_approval',
    output: '',
    error: '',
    created_at: '',
    workflow: createRequestWorkflow('purchase'),
    message: 'Original input',
    events: [],
    approvals: [
      {
        id: 'approval',
        node_id: 'verify',
        kind,
        status: 'pending',
        expires_at: expires,
        payload_json: JSON.stringify(payload, null, 2),
      },
    ],
  };
}
async function openReview(page: Page) {
  await page
    .getByRole('button', { name: 'Close node settings', exact: true })
    .click();
  await page.getByRole('button', { name: 'History', exact: true }).click();
  await page.getByRole('button', { name: /^Review fixture / }).click();
}

for (const choice of [true, false])
  test(`human decision sends explicit ${choice} and the digest of the complete displayed payload`, async ({
    page,
  }) => {
    const run = reviewRun('decision'),
      calls = await fixture(page, [run]);
    await openReview(page);
    const panel = page.getByRole('region', {
      name: 'Review business decision',
      exact: true,
    });
    await expect(panel).toContainText('9007199254740993');
    await expect(panel).toContainText('Original input');
    await expect(
      panel.getByRole('region', { name: 'Original input for review' }),
    ).toContainText('Original input');
    await expect(panel).toContainText('proposed_choice');
    await panel
      .getByRole('button', {
        name: `Choose ${choice} and continue`,
        exact: true,
      })
      .click();
    await expect.poll(() => calls.length).toBe(1);
    expect(calls[0]).toEqual({
      path: '/api/runs/review/approve',
      body: {
        approval_id: 'approval',
        digest: createHash('sha256')
          .update(run.approvals![0].payload_json)
          .digest('hex'),
        choice,
      },
    });
  });

for (const action of ['confirm', 'reject'])
  test(`interpretation review ${action} sends only the exact review digest`, async ({
    page,
  }) => {
    const run = reviewRun('verification'),
      calls = await fixture(page, [run]);
    await openReview(page);
    const panel = page.getByRole('region', {
      name: 'Review extracted interpretation',
      exact: true,
    });
    await expect(panel).toContainText('/amount');
    await expect(panel).toContainText('unverified');
    await expect(panel).toContainText('9007199254740993');
    await expect(
      panel.getByRole('region', { name: 'Extracted value for review' }),
    ).toContainText('9007199254740993');
    await panel
      .getByRole('button', {
        name:
          action === 'confirm'
            ? 'Confirm interpretation'
            : 'Reject interpretation',
        exact: true,
      })
      .click();
    await expect.poll(() => calls.length).toBe(1);
    expect(calls[0]).toEqual({
      path: '/api/runs/review/' + (action === 'confirm' ? 'approve' : 'reject'),
      body: {
        approval_id: 'approval',
        digest: createHash('sha256')
          .update(run.approvals![0].payload_json)
          .digest('hex'),
      },
    });
  });

test('expired reviews cannot be confirmed and payload conflicts require reloading rather than silently retrying', async ({
  page,
}) => {
  const run = reviewRun('verification', 1),
    calls = await fixture(page, [run]);
  await openReview(page);
  const panel = page.getByRole('region', {
    name: 'Review extracted interpretation',
    exact: true,
  });
  await expect(panel).toContainText('expired');
  await expect(
    panel.getByRole('button', { name: 'Confirm interpretation' }),
  ).toBeDisabled();
  expect(calls.length).toBe(0);
});

test('server digest refusal is visible beside the pending review', async ({
  page,
}) => {
  await fixture(
    page,
    [reviewRun('verification')],
    'The approval payload changed or the digest does not match. Reload the request before deciding.',
  );
  await openReview(page);
  await page.getByRole('button', { name: 'Confirm interpretation' }).click();
  await expect(
    page.getByRole('alert', { name: 'Review feedback' }),
  ).toContainText('Reload the request');
});

for (const failure of [
  '',
  'The reviewed payload changed. Reload before deciding.',
])
  test(`delayed review ${failure ? 'failure' : 'success'} cannot replace another opened run`, async ({
    page,
  }) => {
    const pending = reviewRun('verification');
    const other: Run = {
      ...pending,
      id: 'other',
      name: 'Other result',
      status: 'success',
      output: 'Only the other result.',
      approvals: [],
    };
    await fixture(page, [pending, other]);
    let release!: () => void;
    const barrier = new Promise<void>((resolve) => {
      release = resolve;
    });
    let entered = false;
    await page.route('**/api/runs/review/approve', async (route) => {
      entered = true;
      await barrier;
      await route.fulfill({
        status: failure ? 409 : 200,
        json: failure ? { detail: failure } : {},
      });
    });
    await openReview(page);
    await page.getByRole('button', { name: 'Confirm interpretation' }).click();
    await expect.poll(() => entered).toBe(true);
    await page.getByRole('button', { name: 'History', exact: true }).click();
    await page.getByRole('button', { name: /^Other result Completed/ }).click();
    await expect(page.locator('[data-run-answer]')).toHaveText(
      'Only the other result.',
    );
    release();
    await expect(
      page.getByRole('button', { name: 'Save', exact: true }),
    ).toBeVisible();
    await expect(page.locator('[data-run-answer]')).toHaveText(
      'Only the other result.',
    );
    await expect(
      page.getByRole('alert', { name: 'Review feedback' }),
    ).toHaveCount(0);
  });

test('a second workflow cannot retain an earlier router JSON draft on screen', async ({
  page,
}) => {
  const calls = await fixture(page);
  await page.locator('[data-id="router"]').click();
  await page.getByLabel(/^Examples/).fill('[broken');
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page.getByRole('button', { name: 'New workflow', exact: true }).click();
  await page.getByLabel('Workflow template').selectOption('support');
  await page
    .getByRole('button', { name: 'Create workflow', exact: true })
    .click();
  const examples = page.getByLabel(/^Examples/);
  await expect(examples).not.toHaveValue('[broken');
  const displayed = JSON.parse(await examples.inputValue());
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(
    (calls[0].body as Workflow).nodes.find((n) => n.id === 'router')!.config
      .examples,
  ).toEqual(displayed);
});

test('overflowing JSON numbers stay raw in the draft and cannot become a hidden null policy', async ({
  page,
}) => {
  const calls = await fixture(page);
  await page
    .locator('input[type=file]')
    .setInputFiles({
      name: 'purchase.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify(createRequestWorkflow('purchase'))),
    });
  await expect(
    page.getByRole('textbox', { name: 'Workflow name' }),
  ).toHaveValue('Purchase request draft');
  await page.locator('[data-id="verify"]').click();
  const raw = '{"type":"object","properties":{"amount":{"maximum":1e309}}}';
  await page.getByLabel(/^Expected Schema/i).fill(raw);
  const before = calls.length;
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(
    page.getByRole('button', { name: /Expected Schema is not valid JSON/i }),
  ).toBeVisible();
  expect(calls.length).toBe(before);
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => calls.length).toBe(before + 1);
  expect(
    (calls.at(-1)!.body as Workflow).nodes.find((n) => n.id === 'verify')!
      .config.expected_schema,
  ).toBe(raw);
});

test('a review expiring while open disables continuation without sending an approval', async ({
  page,
}) => {
  await page.clock.install({ time: new Date('2026-10-13T12:00:00Z') });
  const expiry = Date.parse('2026-10-13T12:00:03Z') / 1000;
  const calls = await fixture(page, [reviewRun('verification', expiry)]);
  await openReview(page);
  const button = page.getByRole('button', { name: 'Confirm interpretation' });
  await expect(button).toBeEnabled();
  await page.clock.fastForward(4000);
  await expect(button).toBeDisabled();
  expect(calls).toEqual([]);
});

test('rejecting a business decision does not invent a false branch, and write review stays a distinct action', async ({
  page,
}) => {
  const calls = await fixture(page, [reviewRun('decision')]);
  await openReview(page);
  await page
    .getByRole('button', { name: 'Reject review', exact: true })
    .click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0].path).toBe('/api/runs/review/reject');
  expect(Object.keys(calls[0].body).sort()).toEqual(['approval_id', 'digest']);
});

test('write approvals still show the full outgoing request and do not use interpretation controls', async ({
  page,
}) => {
  const calls = await fixture(page, [reviewRun('write')]);
  await openReview(page);
  const panel = page.getByRole('region', { name: 'Review outgoing action' });
  await expect(panel).toContainText('https://example.invalid/send');
  await expect(
    panel.getByRole('button', { name: 'Confirm interpretation' }),
  ).toHaveCount(0);
  await panel.getByRole('button', { name: 'Approve and continue' }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(Object.keys(calls[0].body).sort()).toEqual(['approval_id', 'digest']);
});

for (const route of ['clarify', 'out_of_scope'])
  test(`router ${route} shows missing facts and a model-generated reply`, async ({
    page,
  }) => {
    const run: Run = {
      ...reviewRun('verification'),
      status: 'success',
      approvals: [],
      output: 'Please supply quantity.',
      events: [
        {
          seq: 0,
          node_id: 'router',
          status: 'success',
          timestamp: '',
          outputs: {
            decision: JSON.stringify({
              route,
              reason: 'No suitable request',
              missing_fields: ['quantity'],
              reply: 'Please supply quantity.',
            }),
          },
        },
      ],
    };
    await fixture(page, [run]);
    await openReview(page);
    const checks = page.getByRole('region', { name: 'Request checks' });
    await expect(checks).toContainText('model-generated');
    await expect(checks).toContainText('Missing fields: quantity');
  });

for (const width of [1280, 768])
  test(`long checks, reviews and answer stay scrollable at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 720 });
    const run = reviewRun('verification');
    run.status = 'success';
    run.output = 'Answer line\n'.repeat(70) + 'Done.';
    run.approvals = [];
    run.events = [
      {
        seq: 0,
        node_id: 'cache',
        status: 'success',
        timestamp: '',
        cached: true,
        outputs: {
          branch: 'continue',
          receipt: '{"v":1,"outcome":"not_common"}',
        },
      },
      {
        seq: 1,
        node_id: 'router',
        status: 'success',
        timestamp: '',
        outputs: {
          decision:
            '{"route":"proceed","reason":"A request","missing_fields":[],"reply":""}',
        },
      },
      {
        seq: 2,
        node_id: 'verify',
        status: 'success',
        timestamp: '',
        outputs: {
          report: JSON.stringify({
            verdict: 'needs_review',
            complete: true,
            fields: Array.from({ length: 20 }, (_, i) => ({
              field: `/field${i}`,
              status: 'unverified',
              reason: 'Long explanation é 🌍 '.repeat(20),
            })),
          }),
          _verification: { review_id: 'confirmed' },
        },
      },
    ];
    await fixture(page, [run]);
    await openReview(page);
    const region = page.getByRole('region', {
      name: 'Response and sources',
      exact: true,
    });
    await expect(
      page.getByRole('region', { name: 'Request checks', exact: true }),
    ).toContainText('not verification or business approval');
    await expect
      .poll(() => region.evaluate((e) => e.clientHeight))
      .toBeGreaterThan(0);
    await region
      .getByText('/field19', { exact: true })
      .scrollIntoViewIfNeeded();
    await expect(
      region.getByText('/field19', { exact: true }),
    ).toBeInViewport();
    await region.focus();
    await region.press('End');
    await expect
      .poll(() =>
        region.evaluate((e) =>
          Math.abs(e.scrollHeight - e.scrollTop - e.clientHeight),
        ),
      )
      .toBeLessThanOrEqual(2);
    const end = await region.evaluate((e) => {
      const text = e.querySelector('[data-run-answer]')!.firstChild!;
      const range = document.createRange();
      range.setStart(text, text.textContent!.length - 5);
      range.setEnd(text, text.textContent!.length);
      const bounds = range.getBoundingClientRect(),
        view = e.getBoundingClientRect();
      return bounds.top >= view.top && bounds.bottom <= view.bottom + 2;
    });
    expect(end).toBe(true);
    expect(await region.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(
      true,
    );
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath('checks-and-answer.png'),
    });
  });
