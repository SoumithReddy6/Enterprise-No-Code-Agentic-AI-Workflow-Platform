import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';

// Delete acts on what the user selected last. Clicking an edge selects that edge only: a node
// the editor had selected before is deselected, so Delete (or Backspace) removes the edge and
// never the node. Found in A5.3 Part 2 (B01 cleared the selection first to work around it).
const catalog: unknown = JSON.parse(readFileSync(new URL('./request-nodes.json', import.meta.url), 'utf8'));
const model = { id: 'tiny', provider: 'ollama', model: 'tiny:latest', credential_id: '', enabled: true };

async function editor(page: Page, unexpected: string[]) {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const fixtures: Record<string, unknown> = {
      '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [],
      '/api/credentials': [], '/api/models': [model], '/api/connections': [],
      '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null },
    };
    if (request.method() !== 'GET' || !Object.hasOwn(fixtures, path)) {
      unexpected.push(`${request.method()} ${path}`);
      await route.abort();
      return;
    }
    await route.fulfill({ json: fixtures[path] });
  });
  page.on('dialog', (d) => void d.accept());
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
  await settled(page);
}

const card = (page: Page, id: string) => page.locator(`.react-flow__node[data-id="${id}"]`);
const ids = (page: Page, kind: 'node' | 'edge') =>
  page.locator(`.react-flow__${kind}`).evaluateAll((els) => els.map((e) => e.getAttribute('data-id') ?? '').sort((a, b) => a.localeCompare(b)));

// Fitting the view is animated: measure only once the transform stops changing.
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

// The draft's agent -> response edge, clicked halfway between the two cards.
async function clickAgentResponseEdge(page: Page) {
  const above = (await card(page, 'agent').boundingBox())!;
  const below = (await card(page, 'response').boundingBox())!;
  await page.mouse.click(above.x + above.width / 2, (above.y + above.height + below.y) / 2);
  await expect(page.locator('.react-flow__edge.selected')).toHaveAttribute('data-id', 'agent-response');
}

for (const key of ['Delete', 'Backspace']) {
  test(`D01 ${key} after clicking an edge removes only that edge, not the selected node`, async ({ page }) => {
    const unexpected: string[] = [];
    await editor(page, unexpected);
    await card(page, 'agent').click();
    await expect(card(page, 'agent')).toHaveClass(/selected/);
    const nodes = await ids(page, 'node');
    const edges = await ids(page, 'edge');
    await clickAgentResponseEdge(page);
    await expect(card(page, 'agent')).not.toHaveClass(/selected/);
    await page.keyboard.press(key);
    await expect(page.locator('.react-flow__edge[data-id="agent-response"]')).toHaveCount(0);
    expect(await ids(page, 'node')).toEqual(nodes);
    expect(await ids(page, 'edge')).toEqual(edges.filter((id) => id !== 'agent-response'));
    expect(unexpected).toEqual([]);
  });
}

test('D02 the editor starts with a node selected: clicking an edge and Delete still keep it', async ({ page }) => {
  const unexpected: string[] = [];
  await editor(page, unexpected);
  const nodes = await ids(page, 'node');
  await clickAgentResponseEdge(page);
  await page.keyboard.press('Delete');
  await expect(page.locator('.react-flow__edge[data-id="agent-response"]')).toHaveCount(0);
  expect(await ids(page, 'node')).toEqual(nodes);
  expect(unexpected).toEqual([]);
});

test('D03 control: Delete with a selected node and no edge click removes that node', async ({ page }) => {
  const unexpected: string[] = [];
  await editor(page, unexpected);
  await card(page, 'agent').click();
  const nodes = await ids(page, 'node');
  await page.keyboard.press('Delete');
  await expect(card(page, 'agent')).toHaveCount(0);
  expect(await ids(page, 'node')).toEqual(nodes.filter((id) => id !== 'agent'));
  expect(unexpected).toEqual([]);
});

test('D04 clicking a node after an edge selects the node again, and Delete removes the node', async ({ page }) => {
  const unexpected: string[] = [];
  await editor(page, unexpected);
  await clickAgentResponseEdge(page);
  await card(page, 'response').click();
  await expect(card(page, 'response')).toHaveClass(/selected/);
  await expect(page.locator('.react-flow__edge.selected')).toHaveCount(0);
  const edges = await ids(page, 'edge');
  await page.keyboard.press('Delete');
  await expect(card(page, 'response')).toHaveCount(0);
  // The edge into the deleted node goes with it; no other edge is removed.
  expect(await ids(page, 'edge')).toEqual(edges.filter((id) => !id!.endsWith('-response')));
  expect(unexpected).toEqual([]);
});

// Drop a palette item on an empty part of the visible canvas (no card, control or minimap
// within one card's size). The editor selects the new node; returns its id.
async function dropNode(page: Page, item: string) {
  const canvas = page.locator('.canvas');
  const box = (await canvas.boundingBox())!;
  const taken = await page.locator('.react-flow__node, .react-flow__controls, .react-flow__minimap')
    .evaluateAll((els) => els.map((e) => e.getBoundingClientRect()).map((r) => ({ x: r.x, y: r.y, w: r.width, h: r.height })));
  const [w, h] = [taken[0].w + 24, taken[0].h + 24];
  let at: { x: number; y: number } | undefined;
  for (let y = box.y + 12; !at && y + h < box.y + box.height; y += 12)
    for (let x = box.x + 12; !at && x + w < box.x + box.width; x += 12)
      if (taken.every((t) => x + w < t.x || t.x + t.w < x || y + h < t.y || t.y + t.h < y)) at = { x: x + 12, y: y + 12 };
  expect(at, 'an empty part of the canvas').toBeDefined();
  const before = await ids(page, 'node');
  await page.getByRole('button', { name: item, exact: true }).dragTo(canvas, {
    targetPosition: { x: at!.x - box.x, y: at!.y - box.y }, force: true,
  });
  await expect(page.locator('.react-flow__node')).toHaveCount(before.length + 1);
  return (await ids(page, 'node')).find((id) => !before.includes(id))!;
}

test('D05 a node the editor selects itself deselects the edge: Delete removes the node only', async ({ page }) => {
  const unexpected: string[] = [];
  await editor(page, unexpected);
  await clickAgentResponseEdge(page);
  const edges = await ids(page, 'edge');
  const added = await dropNode(page, 'Agent node');
  await expect(card(page, added)).toHaveClass(/selected/);
  await expect(page.locator('.react-flow__edge.selected')).toHaveCount(0);
  await page.locator('.react-flow__pane').focus();
  await page.keyboard.press('Delete');
  await expect(card(page, added)).toHaveCount(0);
  expect(await ids(page, 'edge')).toEqual(edges);
  expect(unexpected).toEqual([]);
});
