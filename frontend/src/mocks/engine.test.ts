import { describe, expect, it } from 'vitest';
import type { Command, Merchant, Rule, Workspace } from '../contracts';
import { applyCommand, emptyWorkspace } from './engine';

const portfolioId = 'portfolio-core-visayas';
const stormId = 'storm-rai-replay';
const ruleId = 'rule-plan-a';

function demo(): Workspace {
  return applyCommand(emptyWorkspace(), { type: 'load_demo' });
}

function advanceToSettlement(state: Workspace): Workspace {
  let next = state;
  next = applyCommand(next, { type: 'advance', portfolioId, stormId });
  next = applyCommand(next, { type: 'advance', portfolioId, stormId });
  next = applyCommand(next, { type: 'advance', portfolioId, stormId });
  return next;
}

function completedSettlementRun(state: Workspace, fault?: 'none' | 'partial' | 'failure'): Workspace {
  const runCommand: Command = { type: 'run_model', portfolioId, stormId, ruleId, fault };
  let next = applyCommand(state, runCommand);
  const run = next.runs[next.runs.length - 1];
  return applyCommand(next, { type: 'complete_run', runId: run.id });
}

function approvedBatch(state: Workspace): Workspace {
  const withRun = completedSettlementRun(state);
  let next = applyCommand(withRun, { type: 'create_batch', runId: withRun.runs[withRun.runs.length - 1].id });
  const batch = next.batches[next.batches.length - 1];
  next = applyCommand(next, { type: 'submit_batch', batchId: batch.id, version: batch.version });
  const submitted = next.batches[next.batches.length - 1];
  return applyCommand(next, { type: 'approve_batch', batchId: submitted.id, version: submitted.version });
}

describe('mock business engine', () => {
  it('starts blank and loads only through an explicit demo command', () => {
    const blank = emptyWorkspace();
    expect(blank.portfolios).toHaveLength(0);
    expect(blank.rules).toHaveLength(0);
    expect(blank.storms).toHaveLength(0);

    const loaded = demo();
    expect(loaded.portfolios).toHaveLength(2);
    expect(loaded.portfolios.flatMap((portfolio) => portfolio.merchants.map((merchant) => merchant.region))).toEqual(
      expect.arrayContaining(['Tacloban, Eastern Visayas', 'Cebu City, Central Visayas']),
    );
    expect(loaded.storms[0].name).toBe('Rai replay');
    expect(loaded.storms[0].bulletins).toHaveLength(4);
    expect(loaded.storms[0].bulletins.every((bulletin) => bulletin.source.toLowerCase().includes('synthetic'))).toBe(true);
  });

  it('imports validated centavo policies and attaches a usable synthetic catalog', () => {
    const merchants: Merchant[] = [
      {
        id: 'imported-one',
        name: 'Imported One',
        region: 'Quezon City, Metro Manila',
        lat: 14.676,
        lng: 121.0437,
        sumInsured: 10_000_000,
        coverageStart: '2026-01-01T00:00:00.000Z',
        coverageEnd: '2026-12-31T23:59:59.000Z',
      },
    ];
    const imported = applyCommand(emptyWorkspace(), { type: 'import_portfolio', payload: { name: 'Imported book', merchants } });
    expect(imported.portfolios).toHaveLength(1);
    expect(imported.rules.some((rule) => rule.id === 'rule-import-default')).toBe(true);
    expect(imported.storms.some((storm) => storm.id === 'storm-import-replay')).toBe(true);
    expect(imported.cursors[`${imported.portfolios[0].id}:storm-import-replay`]).toBe(0);
    expect(() => applyCommand(emptyWorkspace(), {
      type: 'import_portfolio',
      payload: { name: 'Invalid', merchants: [{ ...merchants[0], sumInsured: 1.25 }] },
    })).toThrow(/integer amount in centavos/);
  });

  it('computes deterministic items and preserves input immutability', () => {
    const state = advanceToSettlement(demo());
    const before = structuredClone(state);
    const first = completedSettlementRun(state);
    const second = completedSettlementRun(state);
    expect(first.runs[first.runs.length - 1].items).toEqual(second.runs[second.runs.length - 1].items);
    expect(first.runs[first.runs.length - 1].totalPayout).toBe(
      first.runs[first.runs.length - 1].items.reduce((sum, item) => sum + item.payout, 0),
    );
    expect(state).toEqual(before);
  });

  it('guards stale runs and records fault outcomes while deduplicating actions', () => {
    let state = demo();
    const running = applyCommand(state, { type: 'run_model', portfolioId, stormId, ruleId });
    const runId = running.runs[running.runs.length - 1].id;
    expect(() => applyCommand(running, { type: 'advance', portfolioId, stormId })).toThrow(/running/);
    const stale = structuredClone(running);
    stale.portfolios[0].version += 1;
    expect(() => applyCommand(stale, { type: 'complete_run', runId })).toThrow(/stale/);

    state = advanceToSettlement(demo());
    state = completedSettlementRun(state, 'partial');
    state = completedSettlementRun(state, 'failure');
    expect(state.runs.at(-2)?.status).toBe('partial');
    expect(state.runs.at(-1)?.status).toBe('failed');
    expect(state.actions.filter((action) => action.type === 'review')).toHaveLength(1);
  });

  it('updates a deduplicated action to the latest bulletin result', () => {
    let state = demo();
    state = completedSettlementRun(state);
    const firstRun = state.runs.at(-1);
    const firstAction = state.actions.find((action) => action.type === 'review');
    expect(firstAction?.runId).toBe(firstRun?.id);
    const firstItems = firstRun?.items;
    state = applyCommand(state, { type: 'advance', portfolioId, stormId });
    state = applyCommand(state, { type: 'run_model', portfolioId, stormId, ruleId });
    const forecastRun = state.runs.at(-1);
    state = applyCommand(state, { type: 'complete_run', runId: forecastRun?.id ?? '' });
    const reviewAction = state.actions.find((action) => action.type === 'review');
    expect(reviewAction?.runId).toBe(forecastRun?.id);
    expect(reviewAction?.status).toBe('open');
    expect(forecastRun?.items).not.toEqual(firstItems);
  });

  it('pays Plan A one-day steps as a share of each merchant cover', () => {
    const state = completedSettlementRun(advanceToSettlement(demo()));
    const payouts = Object.fromEntries(state.runs.at(-1)?.items.map((item) => [item.merchantId, item.payout]) ?? []);
    expect(payouts).toEqual({
      'merchant-tacloban-bakery': 1_372_500,
      'merchant-cebu-market': 2_940_414,
      'merchant-davao-logistics': 0,
      'merchant-manila-pharmacy': 0,
    });
  });

  it('tops a later, stronger storm up to the cover instead of paying twice', () => {
    let state = approvedBatch(advanceToSettlement(demo()));
    const rai = state.storms.find((storm) => storm.id === stormId);
    const second = structuredClone(rai!);
    second.id = 'storm-second-test';
    second.bulletins = second.bulletins.map((bulletin) => ({ ...bulletin, id: `${bulletin.id}-second`, wind: 70 }));
    state.storms.push(second);
    for (let step = 0; step < 3; step += 1) state = applyCommand(state, { type: 'advance', portfolioId, stormId: second.id });
    state = applyCommand(state, { type: 'run_model', portfolioId, stormId: second.id, ruleId });
    state = applyCommand(state, { type: 'complete_run', runId: state.runs.at(-1)?.id ?? '' });
    expect(state.runs.at(-1)?.items.find((item) => item.merchantId === 'merchant-cebu-market')?.payout).toBe(3_166_600);
    state = applyCommand(state, { type: 'create_batch', runId: state.runs.at(-1)?.id ?? '' });
    const amounts = Object.fromEntries(state.batches.at(-1)?.items.map((item) => [item.merchantId, item.amount]) ?? []);
    expect(amounts['merchant-cebu-market']).toBe(226_186);
    expect(amounts['merchant-tacloban-bakery']).toBeUndefined();
  });

  it('closes old settlement and funding actions when a newer result has no trigger', () => {
    let state = advanceToSettlement(demo());
    state = completedSettlementRun(state);
    const run = state.runs.at(-1);
    state = applyCommand(state, { type: 'create_batch', runId: run?.id ?? '' });
    expect(state.actions.some((action) => action.status === 'open' && (action.type === 'settlement' || action.type === 'funding'))).toBe(true);
    const noTrigger: Rule = {
      id: 'rule-no-trigger-test',
      name: 'No trigger test rule',
      version: 'test-1',
      tiers: [{ wind: 1000, fraction: 0.5 }],
      expenseRate: 0,
      annualEventRate: 0,
      currency: 'PHP',
      source: 'Synthetic test rule',
    };
    state.rules.push(noTrigger);
    state = applyCommand(state, { type: 'run_model', portfolioId, stormId, ruleId: noTrigger.id });
    state = applyCommand(state, { type: 'complete_run', runId: state.runs.at(-1)?.id ?? '' });
    expect(state.actions.filter((action) => action.status === 'open')).toHaveLength(0);
  });

  it('creates one settlement obligation per portfolio and enforces versioned payment transitions', () => {
    let state = advanceToSettlement(demo());
    state = completedSettlementRun(state);
    const run = state.runs.at(-1);
    expect(run?.status).toBe('completed');
    state = applyCommand(state, { type: 'create_batch', runId: run?.id ?? '' });
    const batch = state.batches.at(-1);
    expect(batch?.items.every((item) => item.amount > 0 && Number.isInteger(item.amount))).toBe(true);
    expect(() => applyCommand(state, { type: 'create_batch', runId: run?.id ?? '' })).toThrow(/already exists/);

    state = applyCommand(state, { type: 'submit_batch', batchId: batch?.id ?? '', version: batch?.version ?? 0 });
    const submitted = state.batches.at(-1);
    expect(submitted?.status).toBe('pending_approval');
    expect(() => applyCommand(state, { type: 'approve_batch', batchId: submitted?.id ?? '', version: 1 })).toThrow(/stale/);
    state = applyCommand(state, { type: 'approve_batch', batchId: submitted?.id ?? '', version: submitted?.version ?? 0 });
    const approved = state.batches.at(-1);
    state = applyCommand(state, { type: 'pay_batch', batchId: approved?.id ?? '', version: approved?.version ?? 0, scenario: 'failure' });
    const failed = state.batches.at(-1);
    expect(failed?.status).toBe('partial_failure');
    expect(failed?.items.slice(1).every((item) => item.status === 'succeeded')).toBe(true);
    const paymentKey = failed?.items[0].paymentKey;
    state = applyCommand(state, { type: 'retry_batch', batchId: failed?.id ?? '', version: failed?.version ?? 0 });
    const retried = state.batches.at(-1);
    expect(retried?.status).toBe('completed');
    expect(retried?.items[0].paymentKey).toBe(paymentKey);
    expect(retried?.items.every((item) => item.status === 'succeeded')).toBe(true);
  });

  it('rejects an old draft after a different rule recalculation, while allowing reconciliation already in flight', () => {
    let state = advanceToSettlement(demo());
    state = completedSettlementRun(state);
    const oldRun = state.runs.at(-1);
    state = applyCommand(state, { type: 'create_batch', runId: oldRun?.id ?? '' });
    const draft = state.batches.at(-1);
    state = applyCommand(state, { type: 'run_model', portfolioId, stormId, ruleId: 'rule-original-design' });
    state = applyCommand(state, { type: 'complete_run', runId: state.runs.at(-1)?.id ?? '' });
    expect(() => applyCommand(state, { type: 'submit_batch', batchId: draft?.id ?? '', version: draft?.version ?? 0 })).toThrow(/obsolete/);

    state = approvedBatch(advanceToSettlement(demo()));
    const approved = state.batches.at(-1);
    state = applyCommand(state, { type: 'pay_batch', batchId: approved?.id ?? '', version: approved?.version ?? 0, scenario: 'timeout' });
    const timedOut = state.batches.at(-1);
    state = applyCommand(state, { type: 'run_model', portfolioId, stormId, ruleId: 'rule-original-design' });
    state = applyCommand(state, { type: 'complete_run', runId: state.runs.at(-1)?.id ?? '' });
    state = applyCommand(state, { type: 'reconcile_batch', batchId: timedOut?.id ?? '', version: timedOut?.version ?? 0 });
    expect(state.batches.at(-1)?.status).toBe('completed');
  });

  it('closes settlement and funding tasks as a payout moves through the workflow', () => {
    let state = advanceToSettlement(demo());
    state = completedSettlementRun(state);
    const run = state.runs.at(-1);
    state = applyCommand(state, { type: 'create_batch', runId: run?.id ?? '' });
    const draft = state.batches.at(-1);
    expect(state.actions.find((action) => action.type === 'settlement')?.status).toBe('done');
    expect(state.actions.find((action) => action.type === 'funding')?.status).toBe('open');
    state = applyCommand(state, { type: 'submit_batch', batchId: draft?.id ?? '', version: draft?.version ?? 0 });
    expect(state.actions.find((action) => action.type === 'funding')?.status).toBe('done');
    const submitted = state.batches.at(-1);
    state = applyCommand(state, { type: 'approve_batch', batchId: submitted?.id ?? '', version: submitted?.version ?? 0 });
    const approved = state.batches.at(-1);
    state = applyCommand(state, { type: 'pay_batch', batchId: approved?.id ?? '', version: approved?.version ?? 0, scenario: 'success' });
    expect(state.actions.filter((action) => (action.type === 'settlement' || action.type === 'funding') && action.status === 'open')).toHaveLength(0);

    let rejected = advanceToSettlement(demo());
    rejected = completedSettlementRun(rejected);
    rejected = applyCommand(rejected, { type: 'create_batch', runId: rejected.runs.at(-1)?.id ?? '' });
    const rejectedDraft = rejected.batches.at(-1);
    rejected = applyCommand(rejected, { type: 'submit_batch', batchId: rejectedDraft?.id ?? '', version: rejectedDraft?.version ?? 0 });
    const pending = rejected.batches.at(-1);
    rejected = applyCommand(rejected, { type: 'reject_batch', batchId: pending?.id ?? '', version: pending?.version ?? 0 });
    const review = rejected.actions.find((action) => action.type === 'review');
    expect(review?.status).toBe('open');
    expect(review?.detail).toMatch(/rejected/i);
  });

  it('reconciles a timeout with the same payment keys and isolates portfolio cursors', () => {
    let state = advanceToSettlement(demo());
    state = approvedBatch(state);
    const approved = state.batches.at(-1);
    state = applyCommand(state, { type: 'pay_batch', batchId: approved?.id ?? '', version: approved?.version ?? 0, scenario: 'timeout' });
    const timedOut = state.batches.at(-1);
    const keys = timedOut?.items.map((item) => item.paymentKey);
    state = applyCommand(state, { type: 'reconcile_batch', batchId: timedOut?.id ?? '', version: timedOut?.version ?? 0 });
    const reconciled = state.batches.at(-1);
    expect(reconciled?.status).toBe('completed');
    expect(reconciled?.items.map((item) => item.paymentKey)).toEqual(keys);

    const otherPortfolio = 'portfolio-island-partners';
    expect(state.cursors[`${otherPortfolio}:${stormId}`]).toBe(0);
    state = applyCommand(state, { type: 'advance', portfolioId: otherPortfolio, stormId });
    expect(state.cursors[`${otherPortfolio}:${stormId}`]).toBe(1);
    expect(state.cursors[`${portfolioId}:${stormId}`]).toBe(3);
  });

  it('creates integer centavo pricing indications with an explicit illustrative assumption', () => {
    const state = applyCommand(demo(), { type: 'price', portfolioId, ruleId, days: 30 });
    const pricing = state.pricing[0];
    expect(pricing.premium).toBe(pricing.riskCost + pricing.expense);
    expect(Number.isInteger(pricing.riskCost)).toBe(true);
    expect(pricing.assumption.toLowerCase()).toContain('illustrative');
  });

  it('allows deterministic operation timestamps while keeping bulletin asOf synthetic', () => {
    const now = '2026-10-12T14:00:00.000Z';
    let state = applyCommand(emptyWorkspace(), { type: 'load_demo' }, now);
    state = advanceToSettlement(state);
    state = applyCommand(state, { type: 'run_model', portfolioId, stormId, ruleId }, now);
    const run = state.runs.at(-1);
    expect(run?.createdAt).toBe(now);
    expect(run?.asOf).toBe('2026-01-15T00:00:00.000Z');
    expect(state.audits.at(-1)?.at).toBe(now);
  });
});
