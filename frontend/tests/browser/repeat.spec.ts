import { readFileSync } from 'node:fs';
import { expect, test, type Locator, type Page } from '@playwright/test';
import type { Issue, Run, RunEvent, Workflow, WorkflowEdge, WorkflowNode } from '../../lib/workflow';

// A6.1 Part 2 (docs/superpowers/specs/2026-11-13-bounded-loops-editor-design.md, revision 3).
// Generated from the backend registry; backend/tests/test_request_examples.py fails if it drifts.
const catalog: unknown = JSON.parse(readFileSync(new URL('./request-nodes.json', import.meta.url), 'utf8'));
// K01 (backend/tests/test_repeat_editor_contract.py): the Repeat B01 builds, and the real
// /api/validate results for four broken Repeats.
const issues = JSON.parse(readFileSync(new URL('./repeat-issues.json', import.meta.url), 'utf8')) as {
  model: { id: string; provider: string; model: string; credential_id: string; enabled: boolean };
  valid: Workflow;
  broken: { name: string; workflow: Workflow; issues: Issue[] }[];
};
// K02: a real two-iteration run (rejected, then accepted) and the real normalized imports.
const recorded = JSON.parse(readFileSync(new URL('./repeat-run-events.json', import.meta.url), 'utf8')) as {
  workflow: Workflow;
  events: RunEvent[];
  imports: { name: 'repeat' | 'repeat_output' | 'for_each_legacy'; file: Workflow; normalized: Workflow }[];
};
const imported = (name: string) => recorded.imports.find((i) => i.name === name)!;

type Harness = {
  validated: Workflow[];
  runs: Workflow[];
  unexpected: string[];
  warnings: string[];
  issues: Issue[];
  // The real normalized response for the next import; other validations echo the request.
  normalized?: Workflow;
  run?: { stage: 1 | 2 };
};

async function editor(page: Page, h: Harness) {
  page.on('console', (m) => { if (/\b008\b|Couldn't create edge/.test(m.text())) h.warnings.push(m.text()); });
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() === 'POST' && path === '/api/validate') {
      const workflow = request.postDataJSON() as Workflow;
      h.validated.push(workflow);
      const response = h.normalized ?? workflow;
      h.normalized = undefined;
      await route.fulfill({ json: {
        valid: !h.issues.length, errors: h.issues.map((i) => (i.node_id ? `${i.node_id}: ` : '') + i.message),
        warnings: [], issues: h.issues, warning_issues: [], workflow: response,
      } });
      return;
    }
    if (request.method() === 'POST' && path === '/api/runs') {
      h.runs.push((request.postDataJSON() as { workflow: Workflow }).workflow);
      if (!h.run) { h.unexpected.push('POST /api/runs'); await route.abort(); return; }
      await route.fulfill({ json: { id: 'repeat-run' } });
      return;
    }
    if (h.run && path === '/api/runs/repeat-run/events') {
      // Iteration 1 only; the stream then ends without `done`, so the editor polls the run.
      const body = stageEvents(1).map((e) => `event: node\ndata: ${JSON.stringify(e)}\n\n`).join('');
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body });
      return;
    }
    if (h.run && path === '/api/runs/repeat-run') {
      await route.fulfill({ json: repeatRun(h.run.stage, h.runs.at(-1)!) });
      return;
    }
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [], '/api/models': [issues.model], '/api/connections': [],
      '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null },
    };
    if (request.method() !== 'GET' || !Object.hasOwn(fixtures, path)) {
      h.unexpected.push(`${request.method()} ${path}`);
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

const harness = (): Harness => ({ validated: [], runs: [], unexpected: [], warnings: [], issues: [] });
const card = (page: Page, id: string) => page.locator(`.react-flow__node[data-id="${id}"]`);
const port = (node: Locator, id: string) => node.locator(`.react-flow__handle.source[data-handleid="${id}"]`);
const target = (node: Locator) => node.locator('.react-flow__handle-top.target');
const edge = (page: Page, id: string) => page.locator(`.react-flow__edge[data-id="${id}"]`);
const inspector = (page: Page) => page.locator('.inspector');
const nodeOf = (w: Workflow, id: string) => w.nodes.find((n) => n.id === id)!;
const stable = (edges: WorkflowEdge[]) => edges.map((e) => ({
  id: e.id, source: e.source, target: e.target, kind: e.kind ?? 'flow',
  sourceHandle: e.sourceHandle ?? null, targetHandle: e.targetHandle ?? null,
}));

async function importWorkflow(page: Page, h: Harness, name: string) {
  const { file, normalized } = imported(name);
  h.normalized = normalized;
  await page.locator('input[type=file]').first().setInputFiles({
    name: 'workflow.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(file)),
  });
  await expect(page.getByRole('textbox', { name: 'Workflow name' })).toHaveValue(file.name);
  await settled(page);
}

async function importBroken(page: Page, workflow: Workflow) {
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

async function exported(page: Page): Promise<Workflow> {
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export workflow JSON' }).click();
  const file = await (await download).path();
  return JSON.parse(readFileSync(file, 'utf8')) as Workflow;
}

// Show every node, and measure only once the animated fit stops moving.
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

// Drop a palette item on an empty part of the visible canvas, then name the new node.
async function dropNode(page: Page, item: string, label?: string) {
  const canvas = page.locator('.canvas');
  const box = (await canvas.boundingBox())!;
  const taken = await page.locator('.react-flow__node, .react-flow__controls, .react-flow__minimap')
    .evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.x, y: r.y, w: r.width, h: r.height })));
  const [w, hh] = [taken[0].w + 60, taken[0].h + 24];
  let at: { x: number; y: number } | undefined;
  for (let y = box.y + 12; !at && y + hh < box.y + box.height; y += 12)
    for (let x = box.x + 12; !at && x + w < box.x + box.width; x += 12)
      if (taken.every((t) => x + w < t.x || t.x + t.w < x || y + hh < t.y || t.y + t.h < y)) at = { x: x + 12, y: y + 12 };
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

async function connect(page: Page, from: Locator, to: Locator) {
  const before = await page.locator('.react-flow__edge').count();
  await from.dragTo(to);
  await expect(page.locator('.react-flow__edge')).toHaveCount(before + 1);
}

// A point on an edge's drawn path, in page coordinates (0 = start, 1 = end, 0.5 = middle).
async function pointOn(page: Page, id: string, at: number) {
  return edge(page, id).locator('path.react-flow__edge-path').evaluate((path, at) => {
    const p = path as SVGPathElement;
    const point = p.getPointAtLength(p.getTotalLength() * at).matrixTransform(p.getScreenCTM()!);
    return { x: point.x, y: point.y };
  }, at);
}

// `at` moves the click off a segment that another line shares (both of the reader's routes
// leave it together).
async function clickEdge(page: Page, id: string, at = 0.5) {
  await page.locator('.react-flow__pane').click({ position: { x: 20, y: 20 } });
  const middle = await pointOn(page, id, at);
  await page.mouse.click(middle.x, middle.y);
  await expect(page.locator('.react-flow__edge.selected')).toHaveAttribute('data-id', id);
}

// Where a line attaches: its start inside the source handle, its end inside the target handle.
async function attaches(page: Page, id: string, from: Locator, to: Locator) {
  const inside = async (point: { x: number; y: number }, handle: Locator) => {
    const box = (await handle.boundingBox())!;
    const slack = 3;
    return point.x >= box.x - slack && point.x <= box.x + box.width + slack
      && point.y >= box.y - slack && point.y <= box.y + box.height + slack;
  };
  expect(await inside(await pointOn(page, id, 0), from), `${id} starts at its source port`).toBe(true);
  expect(await inside(await pointOn(page, id, 1), to), `${id} ends at its target port`).toBe(true);
}

// The graph without generated ids, positions and edge ids: node ids become labels, also in
// a loop node's body and check settings.
function shape(workflow: Workflow) {
  const label = new Map(workflow.nodes.map((n) => [n.id, n.label]));
  const ref = (value: string) => {
    const [id, port] = value.split('.');
    return label.has(id) ? `${label.get(id)}.${port}` : value;
  };
  const nodes = workflow.nodes
    .map((n: WorkflowNode) => {
      const config = { ...n.config };
      for (const key of ['body', 'check'])
        if (['repeat', 'for_each'].includes(n.type) && typeof config[key] === 'string' && label.has(config[key] as string))
          config[key] = label.get(config[key] as string);
      return {
        label: n.label, type: n.type, version: n.version, config,
        inputs: Object.fromEntries(Object.entries(n.inputs).map(([k, v]) => [k, ref(v)])),
      };
    })
    .sort((a, b) => a.label.localeCompare(b.label));
  const edges = workflow.edges
    .map((e: WorkflowEdge) =>
      `${label.get(e.source)}:${e.sourceHandle || ''} -> ${label.get(e.target)}:${e.targetHandle || ''} [${e.kind || 'flow'}]`)
    .sort();
  return { contract: workflow.contract, nodes, edges };
}

const stageEvents = (stage: 1 | 2) => recorded.events.filter((e) => stage === 2 || e.seq <= 8);

function repeatRun(stage: 1 | 2, workflow: Workflow): Run {
  return {
    id: 'repeat-run', status: stage === 1 ? 'running' : 'success', output: stage === 1 ? '' : 'draft two',
    error: '', created_at: '2026-11-13T00:00:00Z', events: stageEvents(stage), workflow, message: 'go',
  };
}

test('B01 a writer-and-judge Repeat is built from the palette after the Request reader', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await expect(card(page, 'agent')).toContainText('tiny:latest');
  await settled(page);
  // The draft's Agent answers directly. Remove its two flow edges first: an attached body
  // cannot also be a flow step (K01 old_edges_kept).
  await clickEdge(page, 'agent-response');
  await page.keyboard.press('Delete');
  await expect(edge(page, 'agent-response')).toHaveCount(0);
  await clickEdge(page, 'reader-agent', 0.9);
  await page.keyboard.press('Delete');
  await expect(edge(page, 'reader-agent')).toHaveCount(0);
  const repeat = await dropNode(page, 'Repeat until');
  const judge = await dropNode(page, 'Agent node', 'Judge');
  await settled(page);
  await connect(page, port(card(page, 'reader'), 'continue'), target(card(page, repeat)));
  await connect(page, port(card(page, repeat), 'body'), target(card(page, 'agent')));
  await connect(page, port(card(page, repeat), 'check'), target(card(page, judge)));
  await connect(page, port(card(page, repeat), 'flow'), target(card(page, 'response')));
  await card(page, judge).click();
  await page.getByLabel(/^Output JSON schema/).fill(JSON.stringify(
    { type: 'object', required: ['pass'], properties: { pass: { type: 'boolean' } } }));
  await card(page, 'response').click();
  await page.locator('label', { hasText: /^text/ }).locator('select').selectOption({ label: 'Repeat until → text' });
  const submitted = await validate(page, h);
  expect(shape(submitted)).toEqual(shape(issues.valid));
  expect(h.unexpected).toEqual([]);
});

test('B02 imported loop connections are drawn at their ports and exported unchanged', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await importWorkflow(page, h, 'repeat');
  const r = card(page, 'r');
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
  await expect(edge(page, 'e2').locator('.react-flow__edge-text')).toHaveText('body');
  await expect(edge(page, 'e3').locator('.react-flow__edge-text')).toHaveText('check');
  await attaches(page, 'e0', card(page, 'input').locator('.react-flow__handle-bottom.source'), target(r));
  await attaches(page, 'e1', port(r, 'flow'), target(card(page, 'out')));
  await attaches(page, 'e2', port(r, 'body'), target(card(page, 'writer')));
  await attaches(page, 'e3', port(r, 'check'), target(card(page, 'judge')));
  expect(stable((await exported(page)).edges)).toEqual(stable(imported('repeat').normalized.edges));
  // The Repeat's normal outgoing edge saved with sourceHandle 'output'.
  await importWorkflow(page, h, 'repeat_output');
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
  await attaches(page, 'e1', port(card(page, 'r'), 'flow'), target(card(page, 'out')));
  expect(stable((await exported(page)).edges)).toEqual(stable(imported('repeat_output').normalized.edges));
  // An older For each: its loop and flow edges name no source port.
  await importWorkflow(page, h, 'for_each_legacy');
  const each = card(page, 'each');
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
  await attaches(page, 'd', port(each, 'body'), target(card(page, 'summarize')));
  await attaches(page, 'c', port(each, 'flow'), target(card(page, 'out')));
  expect(stable((await exported(page)).edges)).toEqual(stable(imported('for_each_legacy').normalized.edges));
  // Only the imported edges count: the default draft logs 008 for its reader routes while
  // the catalog loads (before this change too, not a Repeat matter).
  const importedIds = new Set(recorded.imports.flatMap((i) => i.normalized.edges.map((e) => e.id)));
  expect(h.warnings.filter((w) => [...importedIds].some((id) => w.includes(`edge id: ${id}.`)))).toEqual([]);
  expect(h.unexpected).toEqual([]);
});

test('B03 the inspector edits the stop rule; deleting the checker clears it in one undoable change', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await importWorkflow(page, h, 'repeat');
  await card(page, 'r').click();
  await expect(inspector(page).getByLabel('Body', { exact: true })).toHaveText('writer');
  await expect(inspector(page).getByLabel('Checker', { exact: true })).toHaveText('judge');
  await expect(inspector(page).getByRole('textbox', { name: /^(Body|Check|Until)\b/ })).toHaveCount(0);
  // eq uses Compare as; gte does not, so changing the operator must drop it (no stale operand).
  await page.getByLabel(/^Stop rule Compare as/).selectOption('number');
  await page.getByLabel(/^Stop rule Operator/).selectOption('gte');
  await page.getByLabel(/^Stop rule Field/).fill('score');
  await page.getByLabel(/^Stop rule Compare to/).fill('0.8');
  let saved = await validate(page, h);
  expect(nodeOf(saved, 'r').config.until).toEqual({ operator: 'gte', field: 'score', compare_to: '0.8' });
  const checked = async (check: string, edges: number) => {
    saved = await validate(page, h);
    expect(nodeOf(saved, 'r').config.check).toBe(check);
    expect(saved.edges.filter((e) => e.kind === 'loop' && e.sourceHandle === 'check')).toHaveLength(edges);
  };
  const undo = async () => {
    await page.getByRole('button', { name: 'Undo', exact: true }).click();
    await expect(card(page, 'judge')).toHaveCount(1);
    await expect(edge(page, 'e3')).toHaveCount(1);
    await checked('judge', 1);
  };
  // (a) The check edge, with the keyboard.
  await clickEdge(page, 'e3');
  await page.keyboard.press('Delete');
  await expect(edge(page, 'e3')).toHaveCount(0);
  await checked('', 0);
  await undo();
  // (b) The Judge node, with the keyboard.
  await card(page, 'judge').click();
  await page.keyboard.press('Delete');
  await expect(card(page, 'judge')).toHaveCount(0);
  await checked('', 0);
  await undo();
  // (c) The Judge node, with the inspector's Delete node.
  await card(page, 'judge').click();
  await page.getByRole('button', { name: 'Delete node' }).click();
  await expect(card(page, 'judge')).toHaveCount(0);
  await checked('', 0);
  await undo();
  // (d) A second node dragged to the occupied check port is refused.
  // The fitted import fills the canvas: zoom out (the canvas's own control) to make room.
  await page.locator('.react-flow__controls-zoomout').click();
  await page.locator('.react-flow__controls-zoomout').click();
  const other = await dropNode(page, 'Agent node', 'Second judge');
  await settled(page);
  const edges = await page.locator('.react-flow__edge').count();
  await port(card(page, 'r'), 'check').dragTo(target(card(page, other)));
  await expect(page.getByText('The check port is already connected. Delete its connection first.')).toBeVisible();
  await expect(page.locator('.react-flow__edge')).toHaveCount(edges);
  await checked('judge', 1);
  expect(h.unexpected).toEqual([]);
});

test('B04 the Repeat shows running until it ends, and the log names every iteration', async ({ page }) => {
  const h: Harness = { ...harness(), run: { stage: 1 } };
  await editor(page, h);
  await importWorkflow(page, h, 'repeat');
  await page.getByRole('button', { name: 'Run workflow' }).click();
  const r = card(page, 'r').locator('.workflow-card');
  await expect(card(page, 'writer').locator('.workflow-card')).toHaveClass(/\bsuccess\b/);
  await expect(r).toHaveClass(/\brunning\b/);
  await expect(r).not.toHaveClass(/\binfo\b/);
  h.run!.stage = 2;
  await expect(r).toHaveClass(/\bsuccess\b/);
  await page.getByRole('button', { name: /^Execution log/ }).click();
  const log = page.locator('.event-list');
  for (const text of ['writer · iteration 1', 'judge · iteration 1', 'writer · iteration 2', 'judge · iteration 2',
    'Iteration 1 did not meet the stop rule.', 'Iteration 2 met the stop rule.', 'Stopped by its rule after 2 of 3 iterations.'])
    await expect(log).toContainText(text);
  expect(h.unexpected).toEqual([]);
});

test('B05 Repeat issues from the validator are listed and marked on their nodes', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  expect(issues.broken.map((b) => b.name)).toEqual(['checker_without_schema', 'write_in_body', 'rule_field_not_required', 'old_edges_kept']);
  for (const broken of issues.broken) {
    h.issues = [];
    await importBroken(page, broken.workflow);
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

test('B06 the quick guide explains Repeat', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await page.getByRole('button', { name: /Quick guide/ }).click();
  const guide = page.getByRole('dialog', { name: 'Quick guide' });
  await expect(guide).toContainText('connect its body port to the step that writes an answer');
  await expect(guide).toContainText('the check port to an Agent that judges it');
  expect(h.unexpected).toEqual([]);
});

test('B07 unfinished JSON in the stop rule stays visible and blocks Validate and Run locally', async ({ page }) => {
  const h = harness();
  await editor(page, h);
  await importWorkflow(page, h, 'repeat');
  await card(page, 'r').click();
  await page.getByLabel(/^Stop rule Operator/).selectOption('in');
  const options = page.getByLabel(/^Stop rule Options/);
  await options.fill('["gold"]');
  await validate(page, h);
  const before = h.validated.length;
  await options.fill('["gold",');
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByText('Stop rule Options is not valid JSON yet. Fix it before validating or running.').first()).toBeVisible();
  await expect(options).toHaveValue('["gold",');
  await page.getByRole('button', { name: 'Run workflow', exact: true }).click();
  await expect(page.getByText('Fix the validation issues below.')).toBeVisible();
  expect(h.validated.length).toBe(before);
  expect(h.runs).toEqual([]);
  await options.fill('["gold"]');
  const saved = await validate(page, h);
  expect((nodeOf(saved, 'r').config.until as { options: unknown }).options).toEqual(['gold']);
  expect(h.unexpected).toEqual([]);
});
