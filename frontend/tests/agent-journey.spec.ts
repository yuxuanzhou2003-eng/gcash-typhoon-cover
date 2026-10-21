import { expect, test, type BrowserContext, type Page } from '@playwright/test';

type AgentScenario = 'ready' | 'needs_review';

type AgentSnapshot = {
  portfolio: {
    id: string;
    version: number;
    merchants: Array<{ id: string; name: string }>;
  };
  storm: { id: string };
  bulletin: { id: string };
  rule: { id: string };
  modelRun: { id: string; inputHash: string; createdAt: string };
};

type AgentProposal = {
  id: string;
  title: string;
  detail: string;
  kind: 'review' | 'communication' | 'funding' | 'settlement';
  merchantIds: string[];
  evidenceIds: string[];
  status: 'proposed' | 'accepted' | 'dismissed' | 'done';
};

type AgentRun = {
  id: string;
  version: number;
  portfolioId: string;
  portfolioVersion: number;
  stormId: string;
  modelRunId: string;
  bulletinId: string;
  ruleId: string;
  inputHash: string;
  createdAt: string;
  updatedAt: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
  mode: 'live' | 'simulated';
  model: string;
  steps: Array<{
    key: 'intelligence' | 'operations' | 'review';
    status: 'completed';
    summary: string;
    findings: Array<{ text: string; evidenceIds: string[] }>;
    startedAt: string;
    completedAt: string;
  }>;
  facts: Array<{ id: string; label: string; value: string; source: string }>;
  proposals: AgentProposal[];
  review: { verdict: 'ready' | 'needs_review'; summary: string; issues: string[] };
};

type AgentMock = {
  scenario: AgentScenario;
  runs: AgentRun[];
  runPosts: Array<{ snapshot: AgentSnapshot; run: AgentRun }>;
  decisionPosts: Array<{ runId: string; proposalId: string; decision: string }>;
  paymentRequests: string[];
  proposalTitle: string;
};

const FIXED_AT = '2026-10-12T00:00:00.000Z';

function isAgentSnapshot(value: unknown): value is AgentSnapshot {
  if (!value || typeof value !== 'object') return false;
  const snapshot = value as Partial<AgentSnapshot>;
  return Boolean(
    snapshot.portfolio?.id
      && snapshot.portfolio.version
      && snapshot.portfolio.merchants?.length
      && snapshot.storm?.id
      && snapshot.bulletin?.id
      && snapshot.rule?.id
      && snapshot.modelRun?.id
      && snapshot.modelRun.inputHash,
  );
}

function buildRun(snapshot: AgentSnapshot, scenario: AgentScenario, sequence: number): AgentRun {
  const merchant = snapshot.portfolio.merchants[0];
  const factId = `fact-${sequence}-risk-snapshot`;
  const merchantFactId = `merchant:${merchant.id}`;
  const proposalTitle = `Review ${merchant.name} protection`;
  const findings = [
    { text: `The selected risk snapshot identifies ${merchant.name} for operator review.`, evidenceIds: [factId, merchantFactId] },
  ];
  const step = (key: AgentRun['steps'][number]['key'], summary: string) => ({
    key,
    status: 'completed' as const,
    summary,
    findings,
    startedAt: FIXED_AT,
    completedAt: FIXED_AT,
  });

  return {
    id: `agent-journey-${sequence}`,
    version: 1,
    portfolioId: snapshot.portfolio.id,
    portfolioVersion: snapshot.portfolio.version,
    stormId: snapshot.storm.id,
    modelRunId: snapshot.modelRun.id,
    bulletinId: snapshot.bulletin.id,
    ruleId: snapshot.rule.id,
    inputHash: snapshot.modelRun.inputHash,
    createdAt: snapshot.modelRun.createdAt || FIXED_AT,
    updatedAt: FIXED_AT,
    status: 'completed',
    mode: 'simulated',
    model: 'deterministic-journey-test',
    steps: [
      step('intelligence', 'Read the selected risk snapshot and its evidence.'),
      step('operations', 'Turn the finding into a concrete operator task.'),
      step('review', scenario === 'ready' ? 'The evidence and proposed task are ready for review.' : 'The result needs a human review before any task can be accepted.'),
    ],
    facts: [
      { id: factId, label: 'Risk snapshot', value: snapshot.modelRun.id, source: 'Synthetic risk snapshot' },
      { id: merchantFactId, label: 'Merchant', value: merchant.name, source: 'Selected portfolio records' },
    ],
    proposals: [{
      id: `proposal-${sequence}-review`,
      title: proposalTitle,
      detail: `Review the trigger evidence and protection status for ${merchant.name}.`,
      kind: 'review',
      merchantIds: [merchant.id],
      evidenceIds: [factId, merchantFactId],
      status: 'proposed',
    }],
    review: scenario === 'ready'
      ? { verdict: 'ready', summary: 'Evidence is ready for a human decision.', issues: [] }
      : { verdict: 'needs_review', summary: 'This result needs review before an operational task can be accepted.', issues: ['The simulated output requires an additional human check.'] },
  };
}

async function installAgentApi(context: BrowserContext, scenario: AgentScenario): Promise<AgentMock> {
  const mock: AgentMock = { scenario, runs: [], runPosts: [], decisionPosts: [], paymentRequests: [], proposalTitle: '' };
  let sequence = 0;

  context.on('request', request => {
    const url = new URL(request.url());
    if (request.method() === 'POST' && /payment|payout|pay/i.test(url.pathname)) mock.paymentRequests.push(url.pathname);
  });

  // Use a context route so requests made through the app's service worker are intercepted too.
  await context.route('**/api/agents/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (request.method() === 'POST' && /payment|payout|pay/i.test(path)) mock.paymentRequests.push(path);

    if (path.endsWith('/health') && request.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ configured: true, provider: 'deterministic-test', model: 'journey-test', mode: 'simulated' }),
      });
      return;
    }

    if (path.endsWith('/runs') && request.method() === 'GET') {
      const portfolioId = url.searchParams.get('portfolioId');
      const stormId = url.searchParams.get('stormId');
      const runs = mock.runs.filter(run => (!portfolioId || run.portfolioId === portfolioId) && (!stormId || run.stormId === stormId));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(runs) });
      return;
    }

    if (path.endsWith('/runs') && request.method() === 'POST') {
      const body = request.postDataJSON() as { snapshot?: unknown };
      if (!isAgentSnapshot(body.snapshot)) {
        await route.fulfill({ status: 400, contentType: 'application/json', body: JSON.stringify({ detail: 'Invalid agent snapshot in test request.' }) });
        return;
      }
      const run = buildRun(body.snapshot, mock.scenario, ++sequence);
      mock.runs = [run, ...mock.runs.filter(item => item.id !== run.id)];
      mock.runPosts.push({ snapshot: body.snapshot, run });
      mock.proposalTitle = run.proposals[0].title;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(run) });
      return;
    }

    const proposalMatch = path.match(/\/runs\/([^/]+)\/proposals\/([^/]+)$/);
    if (proposalMatch && request.method() === 'POST') {
      const [, runId, proposalId] = proposalMatch;
      const body = request.postDataJSON() as { decision?: string };
      const run = mock.runs.find(item => item.id === decodeURIComponent(runId));
      const proposal = run?.proposals.find(item => item.id === decodeURIComponent(proposalId));
      if (!run || !proposal || !body.decision) {
        await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'Unknown proposal in test request.' }) });
        return;
      }
      mock.decisionPosts.push({ runId: run.id, proposalId: proposal.id, decision: body.decision });
      proposal.status = body.decision === 'accept' ? 'accepted' : body.decision === 'complete' ? 'done' : 'dismissed';
      run.version += 1;
      run.updatedAt = FIXED_AT;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(run) });
      return;
    }

    const retryMatch = path.match(/\/runs\/([^/]+)\/retry$/);
    if (retryMatch && request.method() === 'POST') {
      const run = mock.runs.find(item => item.id === decodeURIComponent(retryMatch[1]));
      if (run) {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(run) });
        return;
      }
    }

    // Keep every agent request deterministic. A missing handler must never reach a live provider.
    await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: `Unhandled test agent route: ${request.method()} ${path}` }) });
  });

  return mock;
}

async function prepareDemoRisk(page: Page): Promise<void> {
  await page.goto('/#overview');
  await page.getByRole('button', { name: 'Explore demo', exact: true }).click();
  await page.getByRole('button', { name: 'Load demo dataset', exact: true }).click();
  await expect(page.getByLabel('Product rules', { exact: true })).toBeVisible();
  await page.getByLabel('Product rules', { exact: true }).selectOption({ index: 1 });
  await page.getByRole('button', { name: 'Run analysis', exact: true }).click();
  await expect(page.getByText('Your latest analysis is ready to explore.', { exact: true })).toBeVisible();
}

test('ready agent journey accepts a task, gates completion, and persists it', async ({ page, context }) => {
  const mock = await installAgentApi(context, 'ready');
  await prepareDemoRisk(page);

  await page.getByRole('button', { name: 'Run agents', exact: true }).click();
  await expect(page).toHaveURL(/#agents$/);
  const stages = page.locator('.workspace-flow-stage');
  await expect(stages).toHaveCount(3);
  await expect(stages.nth(0)).toContainText(/Intelligence|Understand the risk input/);
  await expect(stages.nth(1)).toContainText(/Operations|Plan the follow-up/);
  await expect(stages.nth(2)).toContainText(/Review|Check evidence and readiness/);
  await expect(page.getByText('Review ready', { exact: true })).toBeVisible();
  // The next operation must be visible without scrolling past the agent report.
  const decisionPosition = await page.getByRole('button', { name: 'Accept & open task', exact: true }).boundingBox();
  expect(decisionPosition).not.toBeNull();
  expect(decisionPosition!.y + decisionPosition!.height).toBeLessThan(1050);
  await stages.nth(0).getByText('View stage details', { exact: true }).click();
  await stages.nth(0).locator('.agent-evidence summary').first().click();
  await expect(stages.nth(0).locator('.agent-fact').first()).toBeVisible();
  await stages.nth(0).getByText('View stage details', { exact: true }).click();
  await page.screenshot({ path: 'test-results/agent-journey-ready.png', fullPage: true });
  expect(mock.runPosts).toHaveLength(1);
  expect(mock.runPosts[0].run.mode).toBe('simulated');
  expect(mock.runPosts[0].snapshot.portfolio.id).toBeTruthy();
  expect(mock.runPosts[0].run.steps).toHaveLength(3);
  expect(mock.runPosts[0].run.steps.every(step => step.status === 'completed')).toBe(true);
  expect(mock.runPosts[0].run.facts.length).toBeGreaterThan(0);
  expect(mock.runPosts[0].run.proposals[0].status).toBe('proposed');

  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1050 });

  await page.getByRole('button', { name: 'Accept & open task', exact: true }).click();
  await expect(page).toHaveURL(/#payouts$/);
  const focusedTask = page.locator('.task-flow-card').filter({ hasText: mock.proposalTitle });
  await expect(focusedTask).toBeVisible();
  await expect(focusedTask).toBeFocused();
  await page.screenshot({ path: 'test-results/agent-journey-accepted.png', fullPage: true });

  const acknowledge = focusedTask.getByRole('checkbox');
  const markComplete = focusedTask.getByRole('button', { name: 'Mark complete', exact: true });
  await expect(acknowledge).toBeVisible();
  await expect(markComplete).toBeDisabled();
  await acknowledge.check();
  await expect(markComplete).toBeEnabled();
  await markComplete.click();
  await expect(focusedTask).toHaveClass(/task-flow-card-done/);
  await expect(focusedTask).toContainText('Completion recorded by a human');

  await page.reload();
  await expect(page).toHaveURL(/#payouts$/);
  const persistedTask = page.locator('.task-flow-card').filter({ hasText: mock.proposalTitle });
  await expect(persistedTask).toBeVisible();
  await expect(persistedTask).toHaveClass(/task-flow-card-done/);
  await expect(persistedTask).toContainText('Completion recorded by a human');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'test-results/agent-journey-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  expect(mock.decisionPosts.map(item => item.decision)).toEqual(['accept', 'complete']);
  expect(mock.paymentRequests).toEqual([]);
});

test('needs-review blocks acceptance and a later bulletin makes accepted tasks read-only', async ({ page, context }) => {
  const mock = await installAgentApi(context, 'ready');
  await prepareDemoRisk(page);

  await page.getByRole('button', { name: 'Run agents', exact: true }).click();
  await expect(page).toHaveURL(/#agents$/);
  expect(mock.runPosts).toHaveLength(1);

  await page.getByRole('button', { name: 'Accept & open task', exact: true }).click();
  await expect(page).toHaveURL(/#payouts$/);
  await page.getByRole('link', { name: 'Agent workspace', exact: true }).click();
  mock.scenario = 'needs_review';
  await page.getByRole('button', { name: 'Run again', exact: true }).click();
  await expect(page.getByText('Needs review', { exact: true })).toBeVisible();
  const accept = page.getByRole('button', { name: 'Accept & open task', exact: true });
  await expect(accept).toBeDisabled();
  await expect(page.getByRole('heading', { name: 'AI check needs correction', exact: true })).toBeVisible();
  expect(mock.runPosts).toHaveLength(2);

  await page.getByRole('link', { name: 'Storm workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Next bulletin', exact: true }).click();
  await page.getByRole('link', { name: /Actions & payouts/ }).click();
  const staleTask = page.locator('.task-flow-card').filter({ hasText: mock.proposalTitle }).first();
  await expect(staleTask).toBeVisible();
  await expect(staleTask).toHaveClass(/is-stale/);
  await expect(staleTask.getByRole('checkbox')).toBeDisabled();
  await expect(staleTask.getByRole('button', { name: 'Mark complete', exact: true })).toBeDisabled();
  await expect(staleTask).toContainText(/Review context only|older|out of date/i);
  expect(mock.decisionPosts.map(item => item.decision)).toEqual(['accept']);
  expect(mock.paymentRequests).toEqual([]);
});
