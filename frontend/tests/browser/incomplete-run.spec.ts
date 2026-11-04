import { expect, test } from '@playwright/test';
import { seed, type Run } from '../../lib/workflow';
import type { TruncationCause } from '../../lib/run-status';

const causes: TruncationCause[] = [
  { node_id: 'each', kind: 'loop', source: 'confirmed', reason: '2 of 3 items did not run.' },
  { node_id: 'agent', kind: 'agent', source: 'confirmed', reason: 'The agent reached its action limit.' },
  { node_id: 'tickets', kind: 'tool', source: 'confirmed', reason: 'Jira returned 100 of 200 issues.' },
  { node_id: 'second', kind: 'budget', source: 'confirmed', reason: 'The action budget refused this node.' },
];
const answer = ['Temporary layout fixture.', ...Array.from({ length: 24 }, (_, i) => `Paragraph ${i + 1}: the answer remains reachable.`), 'Done.'].join('\n\n');

for (const width of [1280, 768]) {
  for (const variant of ['four causes', 'long reasons', 'legacy'] as const) {
    test(`${variant}: causes and answer remain scrollable at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 720 });
      const recorded = variant === 'legacy'
        ? [{ ...causes[0], source: 'legacy_checkpoint_unverified' as const, reason: 'Completeness cannot be verified from this older checkpoint.' }]
        : causes.map(cause => ({ ...cause, reason: cause.reason + (variant === 'long reasons' ? ' Explanation with multi-byte text é and a-long-unbroken-word-'.repeat(14) : '') }));
      const run: Run = {
        id: 'layout-fixture', name: 'Layout fixture', status: 'success', output: answer,
        error: '', created_at: '2026-10-04T12:00:00Z', finished_at: '2026-10-04T12:00:01Z',
        workflow: { ...seed, name: 'Layout fixture' }, message: 'Layout fixture', events: [],
        truncated: true, truncation_source: recorded[0].source,
        truncation_causes: recorded, truncation_reason: recorded.map(cause => cause.reason).join('; '),
      };
      const unexpected: string[] = [];
      await page.route('**/api/**', async route => {
        const request = route.request();
        const path = new URL(request.url()).pathname;
        const fixtures: Record<string, unknown> = {
          '/api/auth/status': { enabled: false }, '/api/nodes': [], '/api/workflows': [],
          '/api/credentials': [], '/api/models': [], '/api/connections': [], '/api/knowledge-bases': [],
          '/api/runs/page': { items: [run], next_cursor: null }, '/api/runs/layout-fixture': run,
        };
        if (request.method() !== 'GET' || !Object.hasOwn(fixtures, path)) {
          unexpected.push(`${request.method()} ${path}`);
          await route.abort();
          return;
        }
        await route.fulfill({ json: fixtures[path] });
      });
      await page.goto('/');
      await page.getByRole('button', { name: 'Close node settings', exact: true }).click();
      await page.getByRole('button', { name: 'History', exact: true }).click();
      await page.getByRole('button', { name: /^Layout fixture Completed/ }).click();
      const response = page.getByRole('region', { name: 'Response and sources', exact: true });
      const panel = page.getByRole('region', { name: 'Incomplete run', exact: true });
      await expect(panel).toBeVisible();
      await expect(panel.getByRole('listitem')).toHaveCount(recorded.length);
      await expect(panel).toContainText(variant === 'legacy' ? 'Unverified:' : 'Confirmed:');
      await expect.poll(() => response.evaluate(element => element.clientHeight)).toBeGreaterThan(0);
      for (const item of await panel.getByRole('listitem').all()) {
        await item.scrollIntoViewIfNeeded();
        await expect(item).toBeInViewport({ ratio: 0.01 });
      }
      await response.focus();
      await response.press('End');
      await expect.poll(() => response.evaluate(element => Math.abs(element.scrollHeight - element.clientHeight - element.scrollTop))).toBeLessThanOrEqual(2);
      const visibleEnd = await response.evaluate(element => {
        const pre = element.querySelector('pre')!;
        const range = document.createRange();
        const text = pre.firstChild!;
        range.setStart(text, text.textContent!.length - 5);
        range.setEnd(text, text.textContent!.length);
        const end = range.getBoundingClientRect(), view = element.getBoundingClientRect();
        return { text: range.toString(), visible: end.top >= view.top && end.bottom <= view.bottom + 2 };
      });
      expect(visibleEnd).toEqual({ text: 'Done.', visible: true });
      expect(await response.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      expect(unexpected).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath('answer-reachable.png') });
    });
  }
}
