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
  await expect(card.locator('.switch-route .react-flow__handle')).toHaveCount(4);
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

// ------------------------------------------------------------------ review E01-E04 (2026-10-23)

test('a malformed Switch draft opens, shows the bad entry and can be repaired without losing data', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await editor(page, validated, unexpected);
  const broken = structuredClone(connected);
  routeNode(broken).config.cases = [null, ...(routeNode(connected).config.cases as unknown[]).slice(0, 2)];
  await importWorkflow(page, broken);
  await page.locator('[data-id="route"]').click();
  const cases = page.getByRole('region', { name: 'Switch cases' });
  await expect(cases.getByText(/^Case 1 is not valid: it needs an id, a label and a rule\. Saved data: null$/)).toBeVisible();
  // Saving before a repair keeps the draft exactly as it was.
  let saved = await validate(page, validated);
  expect(routeNode(saved).config.cases).toEqual(routeNode(broken).config.cases);
  await cases.getByRole('group', { name: 'Case 1' }).getByRole('button', { name: 'Replace with an empty case', exact: true }).click();
  saved = await validate(page, validated);
  const repaired = routeNode(saved).config.cases as { id: string; label: string }[];
  expect(repaired).toHaveLength(3);
  expect(repaired[0].id).toMatch(/^case_[a-z0-9]{6}$/);
  expect(repaired.slice(1)).toEqual((routeNode(connected).config.cases as unknown[]).slice(0, 2));

  // A case list that is not a list at all is shown, and only replaced on request.
  const notList = structuredClone(connected);
  routeNode(notList).config.cases = 'oops';
  notList.name = 'Not a list';
  await importWorkflow(page, notList);
  await page.locator('[data-id="route"]').click();
  await expect(cases.getByText('The saved cases are not a list, so they cannot be shown as cases: "oops"', { exact: true })).toBeVisible();
  // The draft has connections from routes that no longer exist, and the button says so.
  await cases.getByRole('button', { name: 'Start a new case list and remove its connections', exact: true }).click();
  saved = await validate(page, validated);
  expect(routeNode(saved).config.cases).toHaveLength(1);
  expect(errors).toEqual([]);
  expect(unexpected).toEqual([]);
});

/** Distance between every drawn connection's start and the point where its own handle
 * anchors it: the outer side of the handle (right edge for a right handle, bottom edge for a
 * bottom handle), at the handle's centre line. Both x and y are compared. */
async function edgeAnchors(page: Page) {
  return page.evaluate(() => Array.from(document.querySelectorAll('.react-flow__edge')).flatMap((edge) => {
    const id = edge.getAttribute('data-id') ?? '';
    const path = edge.querySelector('.react-flow__edge-path') as SVGPathElement | null;
    const meta = (window as unknown as { __edgeSources?: Record<string, [string, string]> }).__edgeSources?.[id];
    if (!path || !meta) return [];
    const handle = document.querySelector(`.react-flow__node[data-id="${meta[0]}"] [data-handleid="${meta[1]}"]`);
    if (!handle) return [{ id, delta: 999 }];
    const rect = handle.getBoundingClientRect();
    const right = handle.classList.contains('react-flow__handle-right');
    const bottom = handle.classList.contains('react-flow__handle-bottom');
    const anchor = { x: right ? rect.right : rect.x + rect.width / 2, y: bottom ? rect.bottom : rect.y + rect.height / 2 };
    const start = new DOMPoint(path.getPointAtLength(0).x, path.getPointAtLength(0).y).matrixTransform(path.getScreenCTM()!);
    return [{ id, delta: Math.hypot(anchor.x - start.x, anchor.y - start.y) }];
  }));
}

test('drawn connections stay on their own handles after reorder, add, remove and undo', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  await importWorkflow(page, connected);
  await page.evaluate((edges) => {
    (window as unknown as { __edgeSources: Record<string, [string, string]> }).__edgeSources =
      Object.fromEntries(edges.filter((e) => e.sourceHandle).map((e) => [e.id, [e.source, e.sourceHandle!]]));
  }, connected.edges);
  await page.locator('[data-id="route"]').click();
  // Wait for exactly the expected lines, then require every one on its own handle. (An empty
  // list must never count as settled.)
  const settled = async (lines: number) => {
    await expect.poll(async () => {
      const anchors = await edgeAnchors(page);
      return anchors.length === lines ? Math.max(...anchors.map((a) => a.delta)) : 999;
    }, { timeout: 3000 }).toBeLessThan(1);
  };
  await settled(4);
  const group = (n: number) => page.getByRole('group', { name: `Case ${n}` });
  await group(3).getByRole('button', { name: 'Move up', exact: true }).click();
  await settled(4);
  await group(1).getByRole('button', { name: 'Move down', exact: true }).click();
  await settled(4);
  await page.getByRole('region', { name: 'Switch cases' }).getByRole('button', { name: 'Add case', exact: true }).click();
  await settled(4);
  await group(2).getByRole('button', { name: 'Remove case and its connection', exact: true }).click();
  await settled(3);
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await settled(4);
  expect(unexpected).toEqual([]);
});

test('fifteen cases with long labels draw sixteen separate, readable routes', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  const many = structuredClone(connected);
  routeNode(many).config.cases = Array.from({ length: 15 }, (_, i) => ({
    id: `case_${String(i).padStart(2, '0')}`, label: `Customer success department ${String(i + 1).padStart(2, '0')}`, when: rule(`value${i}`),
  }));
  many.edges = many.edges.filter((e) => e.source !== 'route');
  await importWorkflow(page, many);
  const card = page.locator('[data-id="route"]');
  await expect(card.locator('.switch-route .react-flow__handle')).toHaveCount(16);
  const measure = await card.evaluate((el) => {
    const boxes = (selector: string) => Array.from(el.querySelectorAll(selector)).map((e) => {
      const r = e.getBoundingClientRect();
      return { text: e.textContent, x: r.x, y: r.y, w: r.width, h: r.height, cut: e.scrollWidth > e.clientWidth };
    });
    const overlaps = (list: ReturnType<typeof boxes>) => list.flatMap((a, i) => list.slice(i + 1)
      .filter((b) => Math.min(a.x + a.w, b.x + b.w) > Math.max(a.x, b.x) && Math.min(a.y + a.h, b.y + b.h) > Math.max(a.y, b.y))
      .map((b) => [a.text, b.text]));
    const labels = boxes('.switch-route .port-label');
    const handles = boxes('.switch-route .react-flow__handle');
    return { labels, labelOverlaps: overlaps(labels), handleOverlaps: overlaps(handles),
      labelHandleOverlaps: labels.flatMap((l) => handles.filter((h) => Math.min(l.x + l.w, h.x + h.w) > Math.max(l.x, h.x)
        && Math.min(l.y + l.h, h.y + h.h) > Math.max(l.y, h.y)).map(() => l.text)) };
  });
  expect(measure.labelOverlaps).toEqual([]);
  expect(measure.handleOverlaps).toEqual([]);
  expect(measure.labelHandleOverlaps).toEqual([]);
  // Long labels get real width (cut with an ellipsis only at the card edge); short ones are
  // shown whole. The full text is always available on hover. Independent of the system font.
  const [cases, fallback] = [measure.labels.slice(0, 15), measure.labels[15]];
  for (const label of cases) expect(label.w).toBeGreaterThan(100);
  expect(fallback).toMatchObject({ text: 'Default', cut: false });
  await expect(card.getByTitle('Customer success department 15', { exact: true })).toHaveCount(1);
  await expect(card.getByText('Default', { exact: true })).toBeVisible();
  expect(unexpected).toEqual([]);
});

test('invalid JSON in a case rule stops Validate and Run locally and names the case', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  await importWorkflow(page, connected);
  await page.locator('[data-id="route"]').click();
  await page.getByLabel(/^Case 1 Operator/).selectOption('in');
  const options = page.getByLabel(/^Case 1 Options/);
  await options.fill('["billing"]');
  await validate(page, validated);
  const before = validated.length;
  await options.fill('["urgent",');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByText(/Case 1 Options is not valid JSON yet\. Fix it before validating or running\./).first()).toBeVisible();
  await page.getByRole('button', { name: 'Run workflow', exact: true }).click();
  await expect(page.getByText('Fix the validation issues below.')).toBeVisible();
  expect(validated.length).toBe(before);
  // Fixing the text makes it valid again.
  await options.fill('["urgent"]');
  const saved = await validate(page, validated);
  expect((routeNode(saved).config.cases as { when: { options: unknown } }[])[0].when.options).toEqual(['urgent']);
  expect(unexpected).toEqual([]);
});

// ------------------------------------------------------------------ review E05 (2026-10-24)

const dangling = (w: Workflow) => {
  const ids = new Set([...((routeNode(w).config.cases as unknown[]) ?? [])
    .flatMap((c) => (c && typeof c === 'object' && typeof (c as { id?: unknown }).id === 'string' ? [(c as { id: string }).id] : [])), 'default']);
  return w.edges.filter((e) => e.source === routeNode(w).id && !ids.has(String(e.sourceHandle))).map((e) => e.id);
};

for (const action of ['Replace with an empty case', 'Remove case and its connection'] as const)
  test(`repairing a malformed connected case with "${action}" leaves no orphaned connection, and one undo restores it`, async ({ page }) => {
    const validated: Workflow[] = [];
    const unexpected: string[] = [];
    await editor(page, validated, unexpected);
    const broken = structuredClone(connected);
    (routeNode(broken).config.cases as unknown[])[0] = { id: 'case_billng', label: 'Billing', when: null };
    await importWorkflow(page, broken);
    await page.locator('[data-id="route"]').click();
    const first = page.getByRole('group', { name: 'Case 1' });
    await expect(first.getByText(/^Case 1 is not valid/)).toBeVisible();
    await first.getByRole('button', { name: action, exact: true }).click();
    const saved = await validate(page, validated);
    expect(dangling(saved)).toEqual([]);
    if (action === 'Replace with an empty case') {
      // The valid, unique id is kept, so its connection still belongs to the repaired case.
      expect((routeNode(saved).config.cases as { id: string; label: string }[])[0]).toMatchObject({ id: 'case_billng', label: 'Billing' });
      expect(routeEdges(saved)).toEqual(routeEdges(connected));
    } else {
      expect(caseIds(saved)).toEqual(['case_techno', 'case_accnts']);
      expect(routeEdges(saved)).toEqual(routeEdges(connected).filter((e) => !e.startsWith('case_billng')));
    }
    await page.getByRole('button', { name: 'Undo', exact: true }).click();
    const restored = await validate(page, validated);
    expect(routeNode(restored).config.cases).toEqual(routeNode(broken).config.cases);
    expect(routeEdges(restored)).toEqual(routeEdges(connected));
    expect(unexpected).toEqual([]);
  });

test('starting a new case list removes connections from routes that no longer exist, keeping Default', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editor(page, validated, unexpected);
  const notList = structuredClone(connected);
  routeNode(notList).config.cases = 'oops';
  await importWorkflow(page, notList);
  await page.locator('[data-id="route"]').click();
  await page.getByRole('button', { name: 'Start a new case list and remove its connections', exact: true }).click();
  const saved = await validate(page, validated);
  expect(dangling(saved)).toEqual([]);
  expect(routeEdges(saved)).toEqual(['default->other']);
  await expect(page.getByText(/· 2 connections$/)).toBeVisible();
  // Undo restores the unreadable list and every connection in one step. The restored draft
  // is unparsed again, so Validate stays local, and an unreadable list draws no route
  // handles to hang lines on; the canvas footer counts the restored connections.
  await page.getByRole('button', { name: 'Undo', exact: true }).click();
  await expect(page.getByText(new RegExp(`· ${connected.edges.length} connections$`))).toBeVisible();
  await page.locator('[data-id="route"]').click();
  await expect(page.getByText('The saved cases are not a list, so they cannot be shown as cases: "oops"', { exact: true })).toBeVisible();
  expect(unexpected).toEqual([]);
});


for (const action of ['Replace with an empty case', 'Remove'] as const)
  test(`a malformed case with the reserved id "default" never takes the Default connection ("${action}")`, async ({ page }) => {
    const validated: Workflow[] = [];
    const unexpected: string[] = [];
    await editor(page, validated, unexpected);
    const broken = structuredClone(connected);
    (routeNode(broken).config.cases as unknown[]).push({ id: 'default', label: 'Broken case', when: null });
    await importWorkflow(page, broken);
    await page.locator('[data-id="route"]').click();
    const bad = page.getByRole('group', { name: 'Case 4' });
    await expect(bad.getByText(/^Case 4 is not valid/)).toBeVisible();
    // The automatic Default line is not this entry's, so the plain Remove label applies.
    await bad.getByRole('button', { name: action, exact: true }).click();
    const saved = await validate(page, validated);
    expect(routeEdges(saved)).toEqual(routeEdges(connected));
    expect(routeEdges(saved)).toContain('default->other');
    expect(dangling(saved)).toEqual([]);
    expect(unexpected).toEqual([]);
  });
