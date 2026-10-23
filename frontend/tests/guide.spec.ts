import { test, expect, type Page } from '@playwright/test';

test.use({ storageState: { cookies: [], origins: [] } });

async function atStep(page: Page, title: string) {
  await expect(page.locator('.guide-tip h3')).toHaveText(title);
}

async function next(page: Page, title: string) {
  await atStep(page, title);
  await page.locator('.guide-tip .guide-next').click();
}

// Footer buttons must keep their label on one line, even for the longest page name.
async function expectSingleLineButtons(page: Page) {
  for (const button of await page.locator('.guide-btns button').all()) {
    const lines = await button.evaluate(el => { const range = document.createRange(); range.selectNodeContents(el); return new Set([...range.getClientRects()].map(r => Math.round(r.top))).size; });
    expect(lines, await button.textContent() ?? '').toBe(1);
  }
}

test('a first-time user completes the guided tour from empty workspace to a traced payout', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome to merchant typhoon cover' })).toBeVisible();
  await page.screenshot({ path: 'test-results/guide-welcome.png' });
  await page.getByRole('button', { name: 'Start the guided tour →' }).click();

  await next(page, '1 · Overview');
  await atStep(page, 'Load the demo data');
  await page.waitForTimeout(700);
  await page.screenshot({ path: 'test-results/guide-spotlight.png' });
  await page.getByRole('button', { name: 'Demo data', exact: true }).click();
  await expect(page.locator('.guide-tip')).toHaveCount(0);
  await page.getByRole('button', { name: 'Load demo dataset', exact: true }).click();
  await next(page, 'Three choices drive every page');
  await atStep(page, 'Choose Plan A');
  await page.getByLabel('Product rules', { exact: true }).selectOption({ index: 1 });
  await atStep(page, 'Run your first analysis');
  await page.getByRole('button', { name: 'Run analysis', exact: true }).click();
  await next(page, 'Where the storm meets your merchants');
  await next(page, 'What it means in money');
  await next(page, 'Your progress');
  await atStep(page, 'Next: the merchants');
  await page.getByRole('button', { name: 'Next: Portfolio →' }).click();

  await expect(page).toHaveURL(/#portfolio$/);
  for (const title of ['2 · Portfolio', 'Switch between portfolios', 'The policy register', 'Bring your own merchants']) await next(page, title);
  await atStep(page, 'Next: where the cover comes from');
  await page.getByRole('button', { name: 'Next: Pricing lab →' }).click();

  await expect(page).toHaveURL(/#pricing$/);
  for (const title of ['3 · Pricing lab', 'The payout terms', 'Test the key assumption', 'Cover by location']) await next(page, title);
  await atStep(page, 'Next: follow a storm');
  await expectSingleLineButtons(page);
  await page.waitForTimeout(400);
  await page.locator('.guide-tip').screenshot({ path: 'test-results/guide-next-button.png' });
  await page.getByRole('button', { name: 'Next: Storm workspace →' }).click();

  await expect(page).toHaveURL(/#storm$/);
  await next(page, '4 · Storm workspace');
  await next(page, 'The bulletin timeline');
  for (const title of ['Move to the next forecast', 'One more forecast', 'Reach settlement']) {
    await atStep(page, title);
    await page.getByRole('button', { name: 'Next bulletin', exact: true }).click();
  }
  await atStep(page, 'Analyse the settlement bulletin');
  await page.getByRole('button', { name: 'Run analysis', exact: true }).click();
  await next(page, 'Who reaches the trigger');
  await atStep(page, 'Create the payout draft');
  await expect(page.getByRole('button', { name: 'Create payout draft', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Create payout draft', exact: true }).click();
  await page.getByRole('button', { name: 'Next: Agent workspace →' }).click();

  await expect(page).toHaveURL(/#agents$/);
  for (const title of ['5 · Agent workspace', 'Start the agents', 'Follow the three stages']) await next(page, title);
  await atStep(page, 'Next: approve and pay');
  await expectSingleLineButtons(page);
  await page.getByRole('button', { name: 'Next: Actions & payouts →' }).click();

  await expect(page).toHaveURL(/#payouts$/);
  await next(page, '6 · Actions & payouts');
  await next(page, 'The payout batch');
  await atStep(page, 'Submit for approval');
  await page.getByRole('button', { name: 'Submit for approval', exact: true }).click();
  await atStep(page, 'Switch to the approver');
  await page.getByLabel('Demo role').selectOption('Approver');
  await atStep(page, 'Approve the batch');
  await expect(page.locator('.guide-ring')).toBeVisible();
  await page.waitForTimeout(700);
  await page.screenshot({ path: 'test-results/guide-approve.png' });
  await page.getByRole('button', { name: 'Approve batch', exact: true }).click();
  await atStep(page, 'Simulate the payment');
  await page.getByRole('button', { name: 'Simulate payment', exact: true }).click();
  await expect(page.getByText('All simulated payments reconciled')).toBeVisible();
  await page.getByRole('button', { name: 'Next: Evidence →' }).click();

  await expect(page).toHaveURL(/#evidence$/);
  for (const title of ['7 · Evidence', 'The activity ledger', 'Model runs']) await next(page, title);
  await atStep(page, 'You have run the whole flow');
  await page.getByRole('button', { name: 'Done ✓' }).click();
  await expect(page.locator('.guide-tip')).toHaveCount(0);
  await expect(page.getByText('Approved payout batch').first()).toBeVisible();

  await page.reload();
  await expect(page.locator('.guide-tip')).toHaveCount(0);
  await expect(page.getByRole('heading', { name: 'Welcome to merchant typhoon cover' })).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('skipping the tour stops it, and the help window can replay it', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Start the guided tour →' }).click();
  await atStep(page, '1 · Overview');
  await page.getByRole('button', { name: 'Skip tour' }).click();
  await page.goto('/#portfolio');
  await page.waitForTimeout(800);
  await expect(page.locator('.guide-tip')).toHaveCount(0);

  await page.getByRole('button', { name: 'About this workspace' }).click();
  await page.getByRole('button', { name: 'Replay guided tour' }).click();
  await expect(page.getByRole('heading', { name: 'Welcome to merchant typhoon cover' })).toBeVisible();
  await page.getByRole('button', { name: 'Explore on my own' }).click();
  await expect(page.locator('.guide-layer')).toHaveCount(0);
});

test('the guide card stays on screen at phone width', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Start the guided tour →' }).click();
  await page.locator('.guide-tip .guide-next').click();
  await atStep(page, 'Load the demo data');
  const box = await page.locator('.guide-tip').boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390 && box.y >= 0 && box.y + box.height <= 844).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.waitForTimeout(700);
  await page.screenshot({ path: 'test-results/guide-mobile.png' });
});
