import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import type { Workflow } from '../../lib/workflow';

// Generated from the backend registry; backend/tests/test_request_examples.py fails if it drifts.
const catalog: unknown = JSON.parse(readFileSync(new URL('./request-nodes.json', import.meta.url), 'utf8'));

async function editor(page: Page, validated: Workflow[], unexpected: string[]) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/validate') {
      const workflow = request.postDataJSON() as Workflow;
      validated.push(workflow);
      await route.fulfill({ json: { valid: true, errors: [], warnings: [], workflow } });
      return;
    }
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [], '/api/models': [], '/api/connections': [], '/api/knowledge-bases': [],
      '/api/runs/page': { items: [], next_cursor: null },
    };
    if (request.method() !== 'GET' || !Object.hasOwn(fixtures, path)) {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort();
      return;
    }
    await route.fulfill({ json: fixtures[path] });
  });
  page.on('dialog', (d) => void d.accept());
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
}

async function validate(page: Page, validated: Workflow[]) {
  const before = validated.length;
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect.poll(() => validated.length).toBe(before + 1);
  return validated.at(-1)!;
}

const routeNode = (w: Workflow) => w.nodes.find((n) => n.type === 'switch')!;
const caseIds = (w: Workflow) => (routeNode(w).config.cases as { id: string }[]).map((c) => c.id);
const routeEdges = (w: Workflow) =>
  w.edges.filter((e) => e.source === routeNode(w).id).map((e) => `${e.sourceHandle}->${e.target}`).sort((a, b) => a.localeCompare(b));

const rule = (compare_to: string) => ({ field: 'category', operator: 'eq', compare_to });
const connected: Workflow = {
  version: 1, contract: 2, name: 'Support routing', description: '',
  nodes: [
    { id: 'input', type: 'chat_input', version: 1, label: 'Chat input', position: { x: 300, y: 40 }, inputs: {}, config: {} },
    { id: 'route', type: 'switch', version: 1, label: 'Route ticket', position: { x: 300, y: 220 }, inputs: { value: 'input.message' },
      config: { cases: [
        { id: 'case_billng', label: 'Billing', when: rule('billing') },
        { id: 'case_techno', label: 'Technical', when: rule('technical') },
        { id: 'case_accnts', label: 'Account', when: rule('account') },
      ] } },
    ...['billing', 'technical', 'account', 'other'].map((name, i) => ({
      id: name, type: 'response', version: 1 as const, label: name, position: { x: 40 + i * 200, y: 420 },
      inputs: { text: 'input.message' }, config: {},
    })),
  ],
  edges: [
    { id: 'a', source: 'input', target: 'route' },
    { id: 'b', source: 'route', target: 'billing', sourceHandle: 'case_billng' },
    { id: 't', source: 'route', target: 'technical', sourceHandle: 'case_techno' },
    { id: 'c', source: 'route', target: 'account', sourceHandle: 'case_accnts' },
    { id: 'd', source: 'route', target: 'other', sourceHandle: 'default' },
  ],
};

async function importWorkflow(page: Page, workflow: Workflow) {
  await page.locator('input[type=file]').setInputFiles({
    name: 'workflow.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(workflow)),
  });
  await expect(page.getByRole('textbox', { name: 'Workflow name' })).toHaveValue(workflow.name);
}

test('a Switch from the palette gets cases with generated ids and draws one handle per case plus Default', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  await page.locator('.palette-node', { hasText: 'Switch' }).click();
  const cases = page.getByRole('region', { name: 'Switch cases' });
  await expect(cases.getByRole('group')).toHaveCount(1);
  await cases.getByRole('button', { name: 'Add case', exact: true }).click();
  await cases.getByRole('button', { name: 'Add case', exact: true }).click();
  await expect(cases.getByRole('group')).toHaveCount(3);
  for (const [index, label] of ['Billing', 'Technical', 'Account'].entries()) {
    await page.getByLabel(`Case ${index + 1} label`, { exact: true }).fill(label);
    await page.getByLabel(new RegExp(`^Case ${index + 1} Field`)).fill('category');
    await page.getByLabel(new RegExp(`^Case ${index + 1} Compare to`)).fill(label.toLowerCase());
  }
  const saved = await validate(page, validated);
  const ids = caseIds(saved);
  expect(ids).toHaveLength(3);
  expect(new Set(ids).size).toBe(3);
  for (const id of ids) expect(id).toMatch(/^case_[a-z0-9]{6}$/);
  expect((routeNode(saved).config.cases as { label: string; when: unknown }[]).map((c) => [c.label, c.when])).toEqual([
    ['Billing', rule('billing')], ['Technical', rule('technical')], ['Account', rule('account')],
  ]);
  const card = page.locator('.react-flow__node', { hasText: 'Switch' });
  for (const label of ['Billing', 'Technical', 'Account', 'Default']) await expect(card.getByText(label, { exact: true })).toBeVisible();
  await expect(card.locator('.react-flow__handle-bottom')).toHaveCount(4);
  expect(unexpected).toEqual([]);
});

test('renaming and reordering cases keep ids and connections; removing a connected case removes only its connection', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  await importWorkflow(page, connected);
  await page.locator('[data-id="route"]').click();
  const original = routeEdges(connected);

  await page.getByLabel('Case 2 label', { exact: true }).fill('Tech support');
  await page.getByRole('group', { name: 'Case 3' }).getByRole('button', { name: 'Move up', exact: true }).click();
  let saved = await validate(page, validated);
  expect(caseIds(saved)).toEqual(['case_billng', 'case_accnts', 'case_techno']);
  expect((routeNode(saved).config.cases as { label: string }[]).map((c) => c.label)).toEqual(['Billing', 'Account', 'Tech support']);
  expect(routeEdges(saved)).toEqual(original);

  // A connected case has no plain delete: the button names the connection it removes.
  const tech = page.getByRole('group', { name: 'Case 3' });
  await expect(tech.getByRole('button', { name: 'Remove case', exact: true })).toHaveCount(0);
  await tech.getByRole('button', { name: 'Remove case and its connection', exact: true }).click();
  saved = await validate(page, validated);
  expect(caseIds(saved)).toEqual(['case_billng', 'case_accnts']);
  expect(routeEdges(saved)).toEqual(original.filter((e) => !e.startsWith('case_techno')));
  expect(saved.nodes.some((n) => n.id === 'technical')).toBe(true);

  // One undo restores the case and its connection together.
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  saved = await validate(page, validated);
  expect(caseIds(saved)).toEqual(['case_billng', 'case_accnts', 'case_techno']);
  expect(routeEdges(saved)).toEqual(original);
  expect(unexpected).toEqual([]);
});

test('a case with the same rule as an earlier one is warned about but still saves', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  await importWorkflow(page, connected);
  await page.locator('[data-id="route"]').click();
  await expect(page.getByText(/can never match/)).toHaveCount(0);
  await page.getByLabel(/^Case 3 Compare to/).fill('billing');
  await expect(page.getByRole('group', { name: 'Case 3' }).getByText('This case can never match: case 1 has the same rule.', { exact: true })).toBeVisible();
  const saved = await validate(page, validated);
  expect((routeNode(saved).config.cases as { when: unknown }[])[2].when).toEqual(rule('billing'));
  expect(unexpected).toEqual([]);
});

test('an older workflow without a Switch opens and saves unchanged', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  const old = JSON.parse(readFileSync(new URL('../../../examples/purchase-routing.json', import.meta.url), 'utf8')) as Workflow;
  await importWorkflow(page, old);
  const saved = await validate(page, validated);
  const byKey = (a: unknown[], b: unknown[]) => JSON.stringify(a).localeCompare(JSON.stringify(b));
  const shape = (w: Workflow) => ({
    nodes: w.nodes.map((n) => [n.id, n.type, n.inputs, n.config]).sort(byKey),
    edges: w.edges.map((e) => [e.source, e.target, e.sourceHandle ?? null]).sort(byKey),
  });
  expect(shape(saved)).toEqual(shape(old));
  expect(unexpected).toEqual([]);
});
