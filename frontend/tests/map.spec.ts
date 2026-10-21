import { expect, test, type Locator, type Page } from '@playwright/test';

test.describe.configure({ timeout: 60_000 });

const firstPortfolioMerchants = [
  'Tacloban Bakery',
  'Cebu Market Cooperative',
  'Davao Logistics Hub',
  'Manila Community Pharmacy',
];

const secondPortfolioMerchants = [
  'Iloilo River Traders',
  'Legazpi Hardware Supply',
  'Cagayan de Oro Wholesale',
  'Baguio Mountain Grocer',
];

async function loadDemo(page: Page) {
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Explore demo', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Explore demo', exact: true }).click();
  await page.getByRole('button', { name: 'Load demo dataset', exact: true }).click();

  await expect(page.getByLabel('Portfolio', { exact: true })).toHaveValue('portfolio-core-visayas');
  await expect(page.getByLabel('Merchant exposure map', { exact: true })).toBeVisible();
}

async function hasSubstantialNaturalEarthPath(map: Locator) {
  return map.locator('path.natural-earth-land').evaluateAll((paths) => {
    return paths.some((path) => (path.getAttribute('d')?.length ?? 0) > 100);
  });
}

async function mapPaneTransform(map: Locator) {
  return map.locator('.leaflet-map-pane').evaluate((pane) => {
    const element = pane as HTMLElement;
    return element.style.transform || getComputedStyle(element).transform;
  });
}

async function expectMerchantMarkers(page: Page, names: string[]) {
  for (const name of names) {
    await expect(page.getByRole('button', { name: new RegExp(name, 'i') })).toBeVisible();
  }
}

test('Leaflet map loads its basemap, supports keyboard markers, navigation, portfolio switching, and mobile layout', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  await loadDemo(page);

  const mapShell = page.getByLabel('Merchant exposure map', { exact: true });
  const map = mapShell.locator('.leaflet-container');
  await expect(mapShell).toBeVisible();
  await expect(map).toBeVisible();

  // The default regression uses the bundled vector basemap. A visible map
  // shell alone is insufficient: at least one Natural Earth path must exist.
  await expect.poll(() => map.locator('path.natural-earth-land').count(), {
    timeout: 30_000,
    message: 'Expected the local Natural Earth basemap to render at least one land path',
  }).toBeGreaterThan(0);
  await expect.poll(() => hasSubstantialNaturalEarthPath(map), {
    timeout: 30_000,
    message: 'Expected a Natural Earth land path to contain non-trivial geometry',
  }).toBe(true);

  for (const name of ['Zoom in', 'Zoom out', 'Fit portfolio']) {
    await expect(page.getByRole('button', { name, exact: true })).toBeVisible();
  }

  await expectMerchantMarkers(page, firstPortfolioMerchants);
  const firstMarker = page.getByRole('button', {
    name: /Tacloban Bakery.*(?:not analysed|not analyzed|wind unavailable|below trigger|trigger eligible|trigger reached)/i,
  }).first();
  await expect(firstMarker).toHaveAttribute('aria-label', /Tacloban Bakery/i);
  await expect(firstMarker).toHaveAttribute('aria-label', /(?:not analysed|not analyzed|wind unavailable|below trigger|trigger eligible|trigger reached)/i);
  await firstMarker.focus();
  await expect(firstMarker).toBeFocused();
  await page.keyboard.press('Enter');
  const selectedMerchant = page.getByLabel('Selected merchant', { exact: true });
  await expect(selectedMerchant).toBeVisible();
  await expect(selectedMerchant).toContainText('Tacloban Bakery');

  const zoomReadout = page.getByText(/^Zoom \d+(\.\d+)?$/).first();
  await expect(zoomReadout).toBeVisible();
  const zoomBefore = await zoomReadout.textContent();
  await page.getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect.poll(() => zoomReadout.textContent()).not.toBe(zoomBefore);

  const mapBounds = await map.boundingBox();
  expect(mapBounds).not.toBeNull();
  if (!mapBounds) throw new Error('The Leaflet map did not expose a bounding box for drag testing');

  const transformBeforePan = await mapPaneTransform(map);
  const dragStartX = mapBounds.x + mapBounds.width * 0.62;
  const dragStartY = mapBounds.y + mapBounds.height * 0.5;
  await page.mouse.move(dragStartX, dragStartY);
  await page.mouse.down();
  await page.mouse.move(dragStartX - 100, dragStartY + 40, { steps: 8 });
  await page.mouse.up();
  await expect.poll(() => mapPaneTransform(map)).not.toBe(transformBeforePan);

  const transformBeforeFit = await mapPaneTransform(map);
  await page.getByRole('button', { name: 'Fit portfolio', exact: true }).click();
  await expect.poll(() => mapPaneTransform(map)).not.toBe(transformBeforeFit);

  await page.screenshot({ path: 'test-results/map-desktop.png', fullPage: true });

  const portfolio = page.getByLabel('Portfolio', { exact: true });
  await portfolio.selectOption({ index: 2 });
  await expect(portfolio).toHaveValue('portfolio-island-partners');
  await expectMerchantMarkers(page, secondPortfolioMerchants);
  await expect(page.getByRole('button', { name: /Tacloban Bakery/i })).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/map-mobile.png', fullPage: true });
  await expect.poll(() => page.evaluate(() => {
    const width = Math.max(document.documentElement.scrollWidth, document.body.scrollWidth);
    return width <= window.innerWidth + 1;
  })).toBe(true);
  expect(pageErrors).toEqual([]);
});

test('merchant markers remain available when the basemap fails', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));
  // The default provider is local GeoJSON. Block the request through both
  // Playwright routing and CDP in case the service worker handles the fetch.
  await page.route('**/maps/natural-earth.geojson', (route) => route.abort());
  await page.route('**/*', (route) => {
    const url = route.request().url();
    if (url.includes('/maps/natural-earth.geojson')) return route.abort();
    return route.continue();
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setBlockedURLs', {
    urls: ['*natural-earth.geojson*'],
  });

  await loadDemo(page);
  await expect(page.getByText('Basemap unavailable', { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Retry map', exact: true })).toBeVisible({ timeout: 15_000 });

  // A failed basemap must not remove the portfolio's interactive marker layer
  // or its accessible names.
  await expectMerchantMarkers(page, firstPortfolioMerchants);
  expect(pageErrors).toEqual([]);
});
