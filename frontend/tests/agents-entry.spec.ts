import { test, expect } from '@playwright/test';

test('demo on agent page explains missing rules and prepares fresh analysis in place', async ({ page, context }) => {
  // This tests preparation only: no model call, credentials or backend required.
  await context.route('**/api/agents/health', route => route.fulfill({ json: { configured: true, provider: 'Test provider', model: 'test', mode: 'live' } }));
  await context.route('**/api/agents/runs?*', route => route.fulfill({ json: [] }));
  const modelRequests: string[] = [];
  page.on('request', request => { if (request.method() === 'POST' && request.url().includes('/api/agents/')) modelRequests.push(request.url()); });
  await page.goto('/#agents');
  await expect(page.getByText('Loading agent history…', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Demo data', exact: true }).click();
  await page.getByRole('button', { name: 'Load demo dataset', exact: true }).click();
  await expect(page.getByLabel('Product rules', { exact: true })).toHaveValue('');
  await page.getByRole('button', { name: 'Choose rules', exact: true }).click();
  await expect(page.getByLabel('Product rules', { exact: true })).toBeFocused();
  await page.getByLabel('Product rules', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Run risk analysis', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run agents', exact: true })).toBeEnabled();
  await expect(page).toHaveURL(/#agents$/);
  await expect(page.getByText('Test provider · test', { exact: true })).toBeVisible();
  await page.getByLabel('Product rules', { exact: true }).selectOption({ index: 2 });
  await expect(page.getByRole('button', { name: 'Run agents', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Update risk analysis', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Run agents', exact: true })).toBeEnabled();
  expect(modelRequests).toEqual([]);
  await page.screenshot({ path: 'test-results/agents-entry-ready.png', fullPage: true });
});
