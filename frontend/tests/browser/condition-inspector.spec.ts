import { readFileSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import type { Workflow } from '../../lib/workflow';

// Generated from the backend registry; backend/tests/test_conditions.py fails if it drifts.
const condition: unknown = JSON.parse(readFileSync(new URL('./condition-node.json', import.meta.url), 'utf8'));

async function editorWithCondition(page: Page, validated: Workflow[], unexpected: string[]) {
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
      '/api/auth/status': { enabled: false }, '/api/nodes': [condition], '/api/workflows': [],
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
  await page.goto('/');
  await page.locator('.palette-node', { hasText: 'Condition' }).click();
}

async function validatedCondition(page: Page, validated: Workflow[]) {
  const before = validated.length;
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect.poll(() => validated.length).toBe(before + 1);
  return validated.at(-1)!.nodes.find((node) => node.type === 'condition')!.config;
}

test('the condition inspector shows each operator\'s settings and sends only those', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editorWithCondition(page, validated, unexpected);
  // A field's accessible name is its title followed by its description.
  const setting = (name: string) => page.getByLabel(new RegExp(`^${name}`));
  const operator = setting('Operator');

  await expect(operator).toHaveValue('contains');
  await expect(setting('Contains')).toBeVisible();
  await expect(setting('Compare to')).toHaveCount(0);
  await expect(setting('Options')).toHaveCount(0);

  await setting('Contains').fill('refund');
  expect(await validatedCondition(page, validated)).toEqual({ operator: 'contains', field: '', compare_as: 'text', case_sensitive: false, contains: 'refund' });

  await operator.selectOption('gt');
  await expect(setting('Contains')).toHaveCount(0);
  await expect(setting('Compare as')).toHaveCount(0);
  await setting('Compare to').fill('1000');
  await setting('Field').fill('amount');
  expect(await validatedCondition(page, validated)).toEqual({ operator: 'gt', field: 'amount', case_sensitive: false, compare_to: '1000' });

  await operator.selectOption('in');
  await expect(setting('Compare to')).toHaveCount(0);
  await expect(setting('Compare as')).toBeVisible();
  await setting('Options').fill('["high", "urgent"]');
  expect(await validatedCondition(page, validated)).toEqual({ operator: 'in', field: 'amount', case_sensitive: false, options: ['high', 'urgent'] });

  await operator.selectOption('empty');
  for (const name of ['Contains', 'Compare to', 'Options', 'Compare as', 'Case Sensitive']) await expect(setting(name)).toHaveCount(0);
  expect(await validatedCondition(page, validated)).toEqual({ operator: 'empty', field: 'amount', case_sensitive: false });
  expect(unexpected).toEqual([]);
});

test('an invalid Options edit is never validated or run as the previous list', async ({ page }) => {
  const validated: Workflow[] = [];
  const unexpected: string[] = [];
  await editorWithCondition(page, validated, unexpected);
  const setting = (name: string) => page.getByLabel(new RegExp(`^${name}`));
  await setting('Operator').selectOption('in');
  const options = setting('Options');
  await options.fill('["high"]');
  expect((await validatedCondition(page, validated)).options).toEqual(['high']);

  const before = validated.length;
  await options.fill('["urgent",');
  await expect(page.getByText('Enter valid JSON before leaving this field.', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Validate', exact: true }).click();
  await expect(page.getByText(/: Options is not valid JSON yet\. Fix it before validating or running\./)).toBeVisible();
  await expect(page.getByText('Workflow is valid and ready to run.')).toHaveCount(0);
  await page.getByRole('button', { name: 'Run workflow', exact: true }).click();
  await expect(page.getByText('Fix the validation issues below.')).toBeVisible();
  expect(validated.length).toBe(before);

  // The visible edit survives leaving the node and coming back.
  await page.getByRole('button', { name: 'Close node settings', exact: true }).click();
  await page.locator('.react-flow__node', { hasText: 'Condition' }).click();
  await expect(options).toHaveValue('["urgent",');

  await options.fill('["urgent"]');
  expect((await validatedCondition(page, validated)).options).toEqual(['urgent']);
  expect(unexpected).toEqual([]);
});
