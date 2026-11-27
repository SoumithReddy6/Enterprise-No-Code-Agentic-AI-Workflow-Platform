import { readFileSync, writeFileSync } from 'node:fs';
import { expect, test } from '@playwright/test';
const catalog = JSON.parse(readFileSync(new URL('./catalog.json', import.meta.url), 'utf8'));
const recorded = JSON.parse(readFileSync(new URL('./events.json', import.meta.url), 'utf8'));
const place: Record<string, [number, number, string]> = { input: [300, 0, 'Chat input'], r: [300, 180, 'Draft until approved'], writer: [80, 380, 'Writer'], judge: [520, 380, 'Judge'], out: [300, 560, 'Answer'] };
const workflow = { ...recorded.workflow, description: '', nodes: recorded.workflow.nodes.map((n: { id: string }) => ({ version: 1, inputs: {}, config: {}, ...n, label: place[n.id][2], position: { x: place[n.id][0], y: place[n.id][1] } })) };
const events = recorded.events as { seq: number; node_id?: string; kind?: string; status: string }[];
const stage1 = events.slice(0, events.findIndex((e) => e.kind === 'loop_item') + 1); // through iteration 1's loop_item
const run = (evs: unknown[], status: string) => ({ id: 'rep-run', status, output: status === 'success' ? 'draft two' : '', error: '', created_at: '2026-11-13T00:00:00Z', events: evs, workflow, message: 'go' });
test('probe', async ({ page }) => {
  const notes: Record<string, unknown> = {}; const consoleErrors: string[] = []; let stage = 1; let submitted: unknown = workflow;
  page.on('console', (m) => { if (['error', 'warning'].includes(m.type())) consoleErrors.push(m.text().slice(0, 300)); });
  await page.route('**/api/**', async (route) => {
    const r = route.request(); const path = new URL(r.url()).pathname;
    if (r.method() === 'POST' && path === '/api/validate') { await route.fulfill({ json: { valid: true, errors: [], warnings: [], issues: [], warning_issues: [], workflow: r.postDataJSON() } }); return; }
    if (r.method() === 'POST' && path === '/api/runs') { submitted = (r.postDataJSON() as { workflow: unknown }).workflow; await route.fulfill({ json: { id: 'rep-run' } }); return; }
    if (path === '/api/runs/rep-run/events') { await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: stage1.map((e) => `event: node\ndata: ${JSON.stringify(e)}\n\n`).join('') }); return; }
    if (path === '/api/runs/rep-run') { await route.fulfill({ json: stage === 1 ? { ...run(stage1, 'running'), workflow: submitted } : { ...run(events, 'success'), workflow: submitted } }); return; }
    const f: Record<string, unknown> = { '/api/auth/status': { enabled: false }, '/api/nodes': catalog, '/api/workflows': [], '/api/credentials': [], '/api/models': [{ id: 'm', provider: 'ollama', model: 'fixture', credential_id: '', enabled: true }], '/api/connections': [], '/api/knowledge-bases': [], '/api/runs/page': { items: [], next_cursor: null } };
    if (r.method() !== 'GET' || !Object.hasOwn(f, path)) { await route.abort(); return; }
    await route.fulfill({ json: f[path] });
  });
  page.on('dialog', (d) => void d.accept());
  await page.setViewportSize({ width: 1600, height: 1100 });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Chat input', exact: true })).toBeVisible();
  notes.palette_repeat = await page.getByRole('button', { name: /Repeat/ }).count();
  notes.palette_for_each = await page.getByRole('button', { name: /For each/ }).count();
  notes.palette_control = (await page.locator('button').allInnerTexts()).filter((x) => /Condition|Switch|Calculate|Request reader|Join|Repeat|For each/.test(x));
  await page.locator('input[type=file]').first().setInputFiles({ name: 'w.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(workflow)) });
  await expect(page.locator('.react-flow__node[data-id="judge"]')).toBeVisible();
  await page.locator('.react-flow__controls-fitview').click(); await page.waitForTimeout(600);
  const r = page.locator('.react-flow__node[data-id="r"]');
  notes.repeat_card_text = await r.innerText();
  notes.repeat_handles = await r.locator('.react-flow__handle').evaluateAll((hs) => hs.map((h) => `${h.classList.contains('source') ? 'source' : 'target'}:${h.getAttribute('data-handleid') ?? '(none)'}`));
  notes.edges_in_workflow = workflow.edges.length;
  notes.edges_drawn = await page.locator('.react-flow__edge').evaluateAll((es) => es.map((e) => e.getAttribute('data-id')));
  await page.screenshot({ path: new URL('./canvas.png', import.meta.url).pathname });
  await r.click();
  notes.repeat_inspector = (await page.locator('.settings-section').allInnerTexts()).map((t) => t.slice(0, 900));
  notes.repeat_inspector_controls = await page.locator('.settings-section').locator('input, select, textarea').evaluateAll((els) => els.map((e) => `${e.tagName.toLowerCase()}:${(e as HTMLInputElement).type || ''}:${(e.closest('label')?.textContent || '').trim().slice(0, 40)}=${String((e as HTMLInputElement).value).slice(0, 120)}`));
  await page.screenshot({ path: new URL('./inspector-repeat.png', import.meta.url).pathname });
  for (const id of ['out', 'writer', 'judge']) {
    await page.locator(`.react-flow__node[data-id="${id}"]`).click();
    notes[`bindings_${id}`] = await page.locator('aside select, .inspector select').evaluateAll((ss) => ss.slice(0, 3).map((s) => [...(s as HTMLSelectElement).options].map((o) => o.textContent)));
  }
  // Run: stage 1 = through iteration 1's loop_item; then the finished run.
  await page.getByRole('button', { name: 'Run workflow' }).click();
  await page.waitForTimeout(1500);
  notes.stage1_card_status = Object.fromEntries(await Promise.all(['r', 'writer', 'judge'].map(async (id) => [id, (await page.locator(`.react-flow__node[data-id="${id}"] .node-status`).innerText()).trim()])));
  stage = 2; await page.waitForTimeout(4000);
  notes.final_card_status = Object.fromEntries(await Promise.all(['r', 'writer', 'judge', 'out'].map(async (id) => [id, (await page.locator(`.react-flow__node[data-id="${id}"] .node-status`).innerText()).trim()])));
  await page.getByRole('button', { name: /events|log/i }).first().click().catch(() => {});
  notes.event_rows = await page.locator('.event-row summary').allInnerTexts();
  await page.screenshot({ path: new URL('./events.png', import.meta.url).pathname });
  // Can a loop edge be drawn? Drag the Repeat's bottom handle to the Judge (an existing loop target).
  const before = (await page.locator('.react-flow__edge').count());
  await r.locator('.react-flow__handle.source').first().dragTo(page.locator('.react-flow__node[data-id="judge"] .react-flow__handle.target').first());
  notes.drag_repeat_to_judge = { before, after: await page.locator('.react-flow__edge').count() };
  notes.console = consoleErrors;
  writeFileSync(new URL('./notes.json', import.meta.url).pathname, JSON.stringify(notes, null, 1));
});
