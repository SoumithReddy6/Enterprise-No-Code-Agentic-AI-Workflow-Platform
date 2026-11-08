import { readFileSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
const catalog = JSON.parse(readFileSync(new URL('./catalog.json', import.meta.url), 'utf8'));
const node = (id: string, type: string, label: string, x: number, y: number, inputs: Record<string, string> = {}, config: Record<string, unknown> = {}) =>
  ({ id, type, version: 1, label, position: { x, y }, inputs, config });
const workflow = {
  version: 1, contract: 2, name: 'Fork probe', description: '',
  nodes: [
    node('input', 'chat_input', 'Chat input', 300, 0),
    node('a', 'agent', 'Branch A', 100, 180, { input: 'input.message' }, { provider: 'demo', model: 'demo' }),
    node('b', 'agent', 'Branch B', 500, 180, { input: 'input.message' }, { provider: 'demo', model: 'demo' }),
    node('join', 'join', 'Join', 300, 360),
    node('out', 'response', 'Answer', 300, 540, { text: 'a.text' }),
  ],
  edges: [
    { id: 'e1', source: 'input', target: 'a' }, { id: 'e2', source: 'input', target: 'b' },
    { id: 'e3', source: 'a', target: 'join' }, { id: 'e4', source: 'b', target: 'join' },
    { id: 'e5', source: 'join', target: 'out' },
  ],
};
test('probe', async ({ page }) => {
  const notes: Record<string, unknown> = {};
  await page.route('**/api/**', async (route) => {
    const r = route.request(); const path = new URL(r.url()).pathname;
    if (r.method() === 'POST' && path === '/api/validate') {
      await route.fulfill({ json: { valid: true, errors: [], warnings: [], issues: [], warning_issues: [], workflow: r.postDataJSON() } }); return;
    }
    const f: Record<string, unknown> = { '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [], '/api/credentials': [], '/api/models': [], '/api/connections': [], '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null } };
    if (r.method() !== 'GET' || !Object.hasOwn(f, path)) { await route.abort(); return; }
    await route.fulfill({ json: f[path] });
  });
  page.on('dialog', (d) => void d.accept());
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
  notes.palette_join_button = await page.getByRole('button', { name: 'Join', exact: true }).count();
  notes.palette_control = await page.locator('button').allInnerTexts().then((t) => t.filter((x) => /Condition|Switch|Calculate|Request reader|Join/.test(x)));
  await page.locator('input[type=file]').first().setInputFiles({ name: 'w.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(workflow)) });
  await expect(page.locator('.react-flow__node', { hasText: 'Branch B' })).toBeVisible();
  const joinNode = page.locator('.react-flow__node[data-id="join"]');
  notes.join_card_text = await joinNode.innerText();
  notes.join_handles = { target: await joinNode.locator('.react-flow__handle.target').count(), source: await joinNode.locator('.react-flow__handle.source').count() };
  notes.edges_drawn = await page.locator('.react-flow__edge').count();
  await page.screenshot({ path: new URL('./canvas.png', import.meta.url).pathname });
  for (const id of ['out', 'b']) {
    await page.locator(`.react-flow__node[data-id="${id}"]`).click();
    const select = page.locator('.inspector select, aside select').first();
    notes[`options_${id}`] = await select.locator('option').allInnerTexts();
  }
  const src = page.locator('.react-flow__node[data-id="a"] .react-flow__handle.source').last();
  const dst = page.locator('.react-flow__node[data-id="join"] .react-flow__handle.target');
  await page.locator('.react-flow__node[data-id="join"]').scrollIntoViewIfNeeded();
  await page.getByRole('button', { name: /fit view/i }).click().catch(() => {});
  await src.dragTo(dst);
  notes.edges_after_duplicate_drag = await page.locator('.react-flow__edge').count();
  const s2 = page.locator('.react-flow__node[data-id="b"] .react-flow__handle.source').last();
  const d2 = page.locator('.react-flow__node[data-id="out"] .react-flow__handle.target');
  await s2.dragTo(d2);
  notes.edges_after_new_drag = await page.locator('.react-flow__edge').count();
  writeFileSync(new URL('./notes.json', import.meta.url).pathname, JSON.stringify(notes, null, 1));
});
