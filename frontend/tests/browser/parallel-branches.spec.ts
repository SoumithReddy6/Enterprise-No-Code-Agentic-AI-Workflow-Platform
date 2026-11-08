import { readFileSync } from 'node:fs';
import { expect, test, type Locator, type Page } from '@playwright/test';
import type { Issue, Run, RunEvent, Workflow, WorkflowEdge, WorkflowNode } from '../../lib/workflow';

// A5.3 Part 2 (docs/superpowers/specs/2026-11-07-parallel-branches-editor-design.md).
// Generated from the backend registry; backend/tests/test_request_examples.py fails if it drifts.
const catalog: unknown = JSON.parse(readFileSync(new URL('./request-nodes.json', import.meta.url), 'utf8'));
// The fork B01 builds and the real /api/validate results for three broken forks;
// backend/tests/test_parallel_editor_contract.py (K01) keeps them true.
const fixture = JSON.parse(readFileSync(new URL('./parallel-issues.json', import.meta.url), 'utf8')) as {
  model: { id: string; provider: string; model: string; credential_id: string; enabled: boolean };
  valid: Workflow;
  broken: { name: string; workflow: Workflow; issues: Issue[] }[];
};

type Harness = {
  validated: Workflow[];
  unexpected: string[];
  issues: Issue[];
  run?: { stage: 1 | 2; workflow?: Workflow };
};

async function editor(page: Page, harness: Harness) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/validate') {
      const workflow = request.postDataJSON() as Workflow;
      harness.validated.push(workflow);
      const issues = harness.issues;
      await route.fulfill({ json: {
        valid: !issues.length, errors: issues.map((i) => (i.node_id ? `${i.node_id}: ` : '') + i.message),
        warnings: [], issues, warning_issues: [], workflow,
      } });
      return;
    }
    if (harness.run && request.method() === 'POST' && path === '/api/runs') {
      harness.run.workflow = (request.postDataJSON() as { workflow: Workflow }).workflow;
      await route.fulfill({ json: { id: 'fork-run' } });
      return;
    }
    if (harness.run && path === '/api/runs/fork-run/events') {
      // Both branches start; the stream then ends without `done`, so the editor polls the run.
      const body = runEvents(1).map((e) => `event: node\ndata: ${JSON.stringify(e)}\n\n`).join('');
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
      return;
    }
    if (harness.run && path === '/api/runs/fork-run') {
      await route.fulfill({ json: forkRun(harness.run.stage, harness.run.workflow!) });
      return;
    }
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [], '/api/models': [fixture.model], '/api/connections': [],
      '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null },
    };
    if (request.method() !== 'GET' || !Object.hasOwn(fixtures, path)) {
      harness.unexpected.push(`${request.method()} ${path}`);
      await route.abort();
      return;
    }
    await route.fulfill({ json: fixtures[path] });
  });
  page.on('dialog', (d) => void d.accept());
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
}

const harness = (): Harness => ({ validated: [], unexpected: [], issues: [] });
const card = (page: Page, id: string) => page.locator(`.react-flow__node[data-id="${id}"]`);
const source = (node: Locator) => node.locator('.react-flow__handle-bottom.source');
const target = (node: Locator) => node.locator('.react-flow__handle-top.target');

async function importWorkflow(page: Page, workflow: Workflow) {
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'workflow.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(workflow)),
  });
  await expect(page.getByRole('textbox', { name: 'Workflow name' })).toHaveValue(workflow.name);
  await settled(page);
}

async function validate(page: Page, h: Harness) {
  const before = h.validated.length;
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect.poll(() => h.validated.length).toBe(before + 1);
  return h.validated.at(-1)!;
}

// Show every node (the canvas's own fit-view control), and measure only once the view stops
// moving: fitting is animated.
async function settled(page: Page) {
  await page.locator('.react-flow__controls-fitview').click();
  const transform = () => page.locator('.react-flow__viewport').evaluate((e) => (e as HTMLElement).style.transform);
  let last = '';
  await expect.poll(async () => {
    const now = await transform();
    const same = now === last;
    last = now;
    return same;
  }, { intervals: [150] }).toBe(true);
}

// Drop a palette item on an empty part of the visible canvas, then name the new node (the
// editor selects it). Empty means no card, control or minimap within one card's size, so
// later drags reach the intended handles.
async function dropNode(page: Page, item: string, label?: string) {
  const canvas = page.locator('.canvas');
  const box = (await canvas.boundingBox())!;
  const taken = await page.locator('.react-flow__node, .react-flow__controls, .react-flow__minimap')
    .evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.x, y: r.y, w: r.width, h: r.height })));
  const card = taken[0];
  const [w, h] = [card.w + 24, card.h + 24];
  let at: { x: number; y: number } | undefined;
  for (let y = box.y + 12; !at && y + h < box.y + box.height; y += 12)
    for (let x = box.x + 12; !at && x + w < box.x + box.width; x += 12)
      if (taken.every((t) => x + w < t.x || t.x + t.w < x || y + h < t.y || t.y + t.h < y)) at = { x: x + 12, y: y + 12 };
  expect(at, 'an empty part of the canvas').toBeDefined();
  const ids = await page.locator('.react-flow__node').evaluateAll((els) => els.map((e) => e.getAttribute('data-id')));
  await page.getByRole('button', { name: item, exact: true }).dragTo(canvas, {
    targetPosition: { x: at!.x - box.x, y: at!.y - box.y }, force: true,
  });
  await expect(page.locator('.react-flow__node')).toHaveCount(ids.length + 1);
  const id = (await page.locator('.react-flow__node').evaluateAll((els) => els.map((e) => e.getAttribute('data-id'))))
    .find((x) => !ids.includes(x))!;
  if (label) await page.getByLabel('Node name').fill(label);
  return id;
}

async function connect(page: Page, from: string, to: string) {
  const before = await page.locator('.react-flow__edge').count();
  await source(card(page, from)).dragTo(target(card(page, to)));
  await expect(page.locator('.react-flow__edge')).toHaveCount(before + 1);
}

// The graph without generated ids, positions and edge ids: node ids become labels.
function shape(workflow: Workflow) {
  const label = new Map(workflow.nodes.map((n) => [n.id, n.label]));
  const ref = (value: string) => {
    const [id, port] = value.split('.');
    return label.has(id) ? `${label.get(id)}.${port}` : value;
  };
  const nodes = workflow.nodes
    .map((n: WorkflowNode) => ({
      label: n.label, type: n.type, version: n.version, config: n.config,
      inputs: Object.fromEntries(Object.entries(n.inputs).map(([k, v]) => [k, ref(v)])),
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const edges = workflow.edges
    // Both ports count: an edge into the wrong port of the right node is a different graph.
    .map((e: WorkflowEdge) =>
      `${label.get(e.source)}:${e.sourceHandle || ''} -> ${label.get(e.target)}:${e.targetHandle || ''} [${e.kind || 'flow'}]`)
    .sort();
  return { contract: workflow.contract, nodes, edges };
}

function runEvents(stage: 1 | 2): RunEvent[] {
  const at = '2026-11-07T00:00:00Z';
  const started: RunEvent[] = [
    { seq: 1, node_id: 'branchA', status: 'running', timestamp: at },
    { seq: 2, node_id: 'branchB', status: 'running', timestamp: at },
  ];
  return stage === 1 ? started : [
    ...started,
    { seq: 3, node_id: 'branchA', status: 'failed', error: 'Provider unavailable.', timestamp: at },
    { seq: 4, node_id: 'branchB', status: 'cancelled', timestamp: at },
  ];
}

function forkRun(stage: 1 | 2, workflow: Workflow): Run {
  return {
    id: 'fork-run', status: stage === 1 ? 'running' : 'failed', output: '',
    error: stage === 1 ? '' : 'branchA: Provider unavailable.', created_at: '2026-11-07T00:00:00Z',
    events: runEvents(stage), workflow, message: 'How do AI workflows work?',
  };
}

test('B01 a fork with a Join is built from the palette after the Request reader', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await expect(card(page, 'agent')).toContainText('tiny:latest');
  await settled(page);
  // The draft's Agent answers directly; replace that edge with two branches and a Join.
  // Clear the node selection first: Delete also removes a node the editor still has selected.
  await page.locator('.react-flow__pane').click({ position: { x: 20, y: 20 } });
  const above = (await card(page, 'agent').boundingBox())!;
  const below = (await card(page, 'response').boundingBox())!;
  await page.mouse.click(above.x + above.width / 2, (above.y + above.height + below.y) / 2);
  await expect(page.locator('.react-flow__edge.selected')).toHaveAttribute('data-id', 'agent-response');
  await page.keyboard.press('Delete');
  await expect(page.locator('.react-flow__edge[data-id="agent-response"]')).toHaveCount(0);
  await expect(page.locator('.react-flow__node')).toHaveCount(5);
  const a = await dropNode(page, 'Agent node', 'Branch A');
  const b = await dropNode(page, 'Agent node', 'Branch B');
  const join = await dropNode(page, 'Join');
  await settled(page);
  await connect(page, 'agent', a);
  await connect(page, 'agent', b);
  await connect(page, a, join);
  await connect(page, b, join);
  await connect(page, join, 'response');
  await card(page, 'response').click();
  const text = page.locator('label', { hasText: /^text/ }).locator('select');
  // After the Join both branches' outputs are offered (their order follows generated ids).
  expect(await text.locator('option').allInnerTexts()).toEqual(expect.arrayContaining(['Branch A → text', 'Branch B → text']));
  await text.selectOption({ label: 'Branch B → text' });
  const submitted = await validate(page, h);
  expect(shape(submitted)).toEqual(shape(fixture.valid));
  expect(h.unexpected).toEqual([]);
});

test('B02 the Join card counts the branches that arrive at it', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await importWorkflow(page, fixture.valid);
  await expect(card(page, 'join')).toContainText('Waits for 2 branches');
  const c = await dropNode(page, 'Agent node', 'Branch C');
  await settled(page);
  await connect(page, 'agent', c);
  await expect(card(page, 'join')).toContainText('Waits for 2 branches');
  await connect(page, c, 'join');
  await expect(card(page, 'join')).toContainText('Waits for 3 branches');
  expect(h.unexpected).toEqual([]);
});

test('B03 fork issues from the validator are listed and marked on their nodes', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  expect(fixture.broken.map((b) => b.name)).toEqual(['branch_ends_early', 'write_in_branch', 'nine_branches']);
  for (const broken of fixture.broken) {
    h.issues = [];
    await importWorkflow(page, broken.workflow);
    h.issues = broken.issues;
    await validate(page, h);
    const alert = page.getByRole('alert').filter({ hasText: 'Workflow needs attention' });
    for (const issue of broken.issues) await expect(alert).toContainText(issue.message);
    const marked = [...new Set(broken.issues.map((i) => String(i.node_id)))].sort((a, b) => a.localeCompare(b));
    await expect.poll(async () => (await page.locator('.react-flow__node:has(.workflow-card.invalid)')
      .evaluateAll((els) => els.map((e) => String(e.getAttribute('data-id')))))
      .sort((a, b) => a.localeCompare(b)))
      .toEqual(marked);
  }
  expect(h.unexpected).toEqual([]);
});

test('B04 branches show as running together, then failed and cancelled', async ({ page }) => {
  const h: Harness = { ...harness(), run: { stage: 1 } };
  await editor(page, h);
  await importWorkflow(page, fixture.valid);
  await page.getByRole('button', { name: 'Run workflow' }).click();
  const a = card(page, 'branchA').locator('.workflow-card');
  const b = card(page, 'branchB').locator('.workflow-card');
  await expect(a).toHaveClass(/\brunning\b/);
  await expect(b).toHaveClass(/\brunning\b/);
  h.run!.stage = 2;
  await expect(a).toHaveClass(/\bfailed\b/);
  await expect(b).toHaveClass(/\bcancelled\b/);
  await expect(b.locator('.node-status')).toHaveText(/cancelled/i);
  const border = (l: Locator) => l.evaluate((e) => getComputedStyle(e).borderTopStyle);
  expect(await border(b)).toBe('dashed');
  expect(await border(a)).toBe('solid');
  expect(h.unexpected).toEqual([]);
});

test('B05 the quick guide explains forks and no longer lists parallel execution as future work', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await page.getByRole('button', { name: /Quick guide/ }).click();
  const guide = page.getByRole('dialog', { name: 'Quick guide' });
  await expect(guide).toContainText('connect the last step of every branch to the same Join');
  await expect(guide).not.toContainText(/parallel execution/i);
  expect(h.unexpected).toEqual([]);
});
