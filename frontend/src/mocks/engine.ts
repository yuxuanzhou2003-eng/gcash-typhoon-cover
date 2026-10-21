import type {
  Action,
  Audit,
  Batch,
  BatchStatus,
  Bulletin,
  Command,
  Merchant,
  ModelRun,
  PayoutItem,
  Portfolio,
  PricingRun,
  RiskItem,
  Rule,
  Storm,
  Workspace,
} from '../contracts';
import { cursorKey } from '../contracts';
import { demoDataset, importedCatalog } from './fixtures';

const MODEL_VERSION = 'mock-1.0';
const MAX_RADIUS_DEGREES = 8;

type RunFault = 'none' | 'partial' | 'failure';
type BatchVersionCommand = { batchId: string; version: number };
type ApprovalBatchCommand = BatchVersionCommand & { type: string };
type ResolutionBatchCommand = BatchVersionCommand & { type: 'reconcile_batch' | 'retry_batch' };

function invariant(condition: unknown, message: string): asserts condition {
  if (!condition) {
    throw new Error(message);
  }
}

function nonEmpty(value: unknown, label: string): asserts value is string {
  invariant(typeof value === 'string' && value.trim().length > 0, `${label} is required`);
}

function finite(value: unknown, label: string): asserts value is number {
  invariant(typeof value === 'number' && Number.isFinite(value), `${label} must be finite`);
}

function money(value: unknown, label: string, allowZero = true): asserts value is number {
  finite(value, label);
  invariant(Number.isSafeInteger(value), `${label} must be an integer amount in centavos`);
  invariant(allowZero ? value >= 0 : value > 0, `${label} must be ${allowZero ? 'non-negative' : 'positive'}`);
}

function coordinate(value: unknown, label: string, min: number, max: number): asserts value is number {
  finite(value, label);
  invariant(value >= min && value <= max, `${label} is outside its valid range`);
}

function dateStamp(value: unknown, label: string): number {
  nonEmpty(value, label);
  const stamp = Date.parse(value);
  invariant(Number.isFinite(stamp), `${label} must be a valid date`);
  return stamp;
}

function validateMerchant(merchant: Merchant, label = 'merchant'): void {
  nonEmpty(merchant.id, `${label}.id`);
  nonEmpty(merchant.name, `${label}.name`);
  nonEmpty(merchant.region, `${label}.region`);
  coordinate(merchant.lat, `${label}.lat`, -90, 90);
  coordinate(merchant.lng, `${label}.lng`, -180, 180);
  money(merchant.sumInsured, `${label}.sumInsured`, false);
  const start = dateStamp(merchant.coverageStart, `${label}.coverageStart`);
  const end = dateStamp(merchant.coverageEnd, `${label}.coverageEnd`);
  invariant(start < end, `${label}.coverageStart must be before coverageEnd`);
}

function validateRule(rule: Rule, label = 'rule'): void {
  nonEmpty(rule.id, `${label}.id`);
  nonEmpty(rule.name, `${label}.name`);
  nonEmpty(rule.version, `${label}.version`);
  nonEmpty(rule.source, `${label}.source`);
  invariant(rule.currency === 'PHP', `${label}.currency must be PHP`);
  invariant(Array.isArray(rule.tiers) && rule.tiers.length > 0, `${label}.tiers must not be empty`);
  let previousWind = -Infinity;
  rule.tiers.forEach((tier, index) => {
    finite(tier.wind, `${label}.tiers[${index}].wind`);
    finite(tier.fraction, `${label}.tiers[${index}].fraction`);
    invariant(tier.wind >= 0, `${label}.tiers[${index}].wind must be non-negative`);
    invariant(tier.fraction >= 0 && tier.fraction <= 1, `${label}.tiers[${index}].fraction must be between 0 and 1`);
    invariant(tier.wind > previousWind, `${label}.tiers must be ordered by increasing wind`);
    previousWind = tier.wind;
  });
  finite(rule.expenseRate, `${label}.expenseRate`);
  finite(rule.annualEventRate, `${label}.annualEventRate`);
  invariant(rule.expenseRate >= 0, `${label}.expenseRate must be non-negative`);
  invariant(rule.annualEventRate >= 0, `${label}.annualEventRate must be non-negative`);
}

function validateTrackPoint(point: { lat: number; lng: number }, label: string): void {
  coordinate(point.lat, `${label}.lat`, -90, 90);
  coordinate(point.lng, `${label}.lng`, -180, 180);
}

function validateBulletin(bulletin: Bulletin, label = 'bulletin'): void {
  nonEmpty(bulletin.id, `${label}.id`);
  nonEmpty(bulletin.label, `${label}.label`);
  nonEmpty(bulletin.source, `${label}.source`);
  dateStamp(bulletin.issuedAt, `${label}.issuedAt`);
  finite(bulletin.wind, `${label}.wind`);
  invariant(bulletin.wind >= 0, `${label}.wind must be non-negative`);
  validateTrackPoint(bulletin.center, `${label}.center`);
  invariant(Array.isArray(bulletin.track) && bulletin.track.length > 0, `${label}.track must not be empty`);
  bulletin.track.forEach((point, index) => validateTrackPoint(point, `${label}.track[${index}]`));
  invariant(bulletin.phase === 'forecast' || bulletin.phase === 'settlement', `${label}.phase is invalid`);
}

function validatePortfolio(portfolio: Portfolio, label = 'portfolio'): void {
  nonEmpty(portfolio.id, `${label}.id`);
  nonEmpty(portfolio.name, `${label}.name`);
  invariant(portfolio.source === 'demo' || portfolio.source === 'import', `${label}.source is invalid`);
  finite(portfolio.version, `${label}.version`);
  invariant(Number.isSafeInteger(portfolio.version) && portfolio.version > 0, `${label}.version must be a positive integer`);
  dateStamp(portfolio.createdAt, `${label}.createdAt`);
  invariant(Array.isArray(portfolio.merchants), `${label}.merchants must be an array`);
  invariant(portfolio.merchants.length > 0, `${label}.merchants must not be empty`);
  const merchantIds = new Set<string>();
  portfolio.merchants.forEach((merchant, index) => {
    validateMerchant(merchant, `${label}.merchants[${index}]`);
    invariant(!merchantIds.has(merchant.id), `${label}.merchants contains duplicate id ${merchant.id}`);
    merchantIds.add(merchant.id);
  });
}

function validateStorm(storm: Storm, label = 'storm'): void {
  nonEmpty(storm.id, `${label}.id`);
  nonEmpty(storm.name, `${label}.name`);
  nonEmpty(storm.description, `${label}.description`);
  invariant(Array.isArray(storm.bulletins) && storm.bulletins.length > 0, `${label}.bulletins must not be empty`);
  const bulletinIds = new Set<string>();
  storm.bulletins.forEach((bulletin, index) => {
    validateBulletin(bulletin, `${label}.bulletins[${index}]`);
    invariant(!bulletinIds.has(bulletin.id), `${label}.bulletins contains duplicate id ${bulletin.id}`);
    bulletinIds.add(bulletin.id);
  });
  invariant(storm.bulletins.some((bulletin) => bulletin.phase === 'settlement'), `${label} needs a settlement bulletin`);
}

function cloneWorkspace(state: Workspace): Workspace {
  return structuredClone(state) as Workspace;
}

function slug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'item';
}

function hash(value: string): string {
  let result = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16777619);
  }
  return (result >>> 0).toString(16).padStart(8, '0');
}

function operationTimestamp(value: string): string {
  const stamp = dateStamp(value, 'operation timestamp');
  return new Date(stamp).toISOString();
}

function startMutation(state: Workspace): Workspace {
  const next = cloneWorkspace(state);
  finite(next.version, 'workspace.version');
  invariant(Number.isSafeInteger(next.version) && next.version >= 0, 'workspace.version must be a non-negative integer');
  next.version += 1;
  return next;
}

function appendAudit(next: Workspace, at: string, title: string, detail: string, portfolioId?: string, entityId?: string): void {
  const audit: Audit = {
    id: `audit-${next.version}-${next.audits.length + 1}`,
    at,
    portfolioId,
    title,
    detail,
    entityId,
  };
  next.audits.push(audit);
}

function requirePortfolio(state: Workspace, portfolioId: string): Portfolio {
  nonEmpty(portfolioId, 'portfolioId');
  const portfolio = state.portfolios.find((candidate) => candidate.id === portfolioId);
  invariant(portfolio, `Unknown portfolio ${portfolioId}`);
  return portfolio;
}

function requireRule(state: Workspace, ruleId: string): Rule {
  nonEmpty(ruleId, 'ruleId');
  const rule = state.rules.find((candidate) => candidate.id === ruleId);
  invariant(rule, `Unknown rule ${ruleId}`);
  return rule;
}

function requireStorm(state: Workspace, stormId: string): Storm {
  nonEmpty(stormId, 'stormId');
  const storm = state.storms.find((candidate) => candidate.id === stormId);
  invariant(storm, `Unknown storm ${stormId}`);
  return storm;
}

function requireCurrentBulletin(state: Workspace, portfolioId: string, stormId: string): Bulletin {
  const storm = requireStorm(state, stormId);
  requirePortfolio(state, portfolioId);
  const key = cursorKey(portfolioId, stormId);
  const cursor = state.cursors[key] ?? 0;
  finite(cursor, `cursor ${key}`);
  invariant(Number.isSafeInteger(cursor) && cursor >= 0 && cursor < storm.bulletins.length, `Cursor ${key} is out of range`);
  return storm.bulletins[cursor];
}

function currentCursor(state: Workspace, portfolioId: string, stormId: string): number {
  const storm = requireStorm(state, stormId);
  requirePortfolio(state, portfolioId);
  const cursor = state.cursors[cursorKey(portfolioId, stormId)] ?? 0;
  finite(cursor, `cursor ${cursorKey(portfolioId, stormId)}`);
  invariant(Number.isSafeInteger(cursor) && cursor >= 0 && cursor < storm.bulletins.length, 'Cursor is out of range');
  return cursor;
}

function requireRun(state: Workspace, runId: string): ModelRun {
  nonEmpty(runId, 'runId');
  const run = state.runs.find((candidate) => candidate.id === runId);
  invariant(run, `Unknown model run ${runId}`);
  return run;
}

function requireBatch(state: Workspace, batchId: string): Batch {
  nonEmpty(batchId, 'batchId');
  const batch = state.batches.find((candidate) => candidate.id === batchId);
  invariant(batch, `Unknown batch ${batchId}`);
  return batch;
}

function runFault(run: ModelRun): RunFault {
  if (run.error === 'synthetic-fault:partial') return 'partial';
  if (run.error === 'synthetic-fault:failure') return 'failure';
  return 'none';
}

function localWind(merchant: Merchant, bulletin: Bulletin): number {
  // This is intentionally a simple radial decay for a deterministic mock. It is not a weather model.
  const latitudeScale = Math.cos((bulletin.center.lat * Math.PI) / 180);
  const latDelta = merchant.lat - bulletin.center.lat;
  const lngDelta = (merchant.lng - bulletin.center.lng) * latitudeScale;
  const radius = Math.sqrt(latDelta * latDelta + lngDelta * lngDelta);
  const decay = Math.max(0, 1 - radius / MAX_RADIUS_DEGREES);
  return Math.max(0, Math.round(bulletin.wind * decay));
}

function tierFor(rule: Rule, wind: number): { wind: number; fraction: number } | undefined {
  let selected: { wind: number; fraction: number } | undefined;
  for (const tier of rule.tiers) {
    if (wind >= tier.wind) selected = tier;
  }
  return selected;
}

function moneyRound(value: number, label: string): number {
  finite(value, label);
  const rounded = Math.round(value);
  invariant(Number.isSafeInteger(rounded), `${label} exceeds safe centavo precision`);
  invariant(rounded >= 0, `${label} must be non-negative`);
  return rounded;
}

function calculateItems(portfolio: Portfolio, rule: Rule, bulletin: Bulletin): RiskItem[] {
  const issuedAt = dateStamp(bulletin.issuedAt, 'bulletin.issuedAt');
  return portfolio.merchants.map((merchant) => {
    const coverageStart = dateStamp(merchant.coverageStart, `${merchant.id}.coverageStart`);
    const coverageEnd = dateStamp(merchant.coverageEnd, `${merchant.id}.coverageEnd`);
    if (issuedAt < coverageStart || issuedAt > coverageEnd) {
      return {
        merchantId: merchant.id,
        wind: null,
        payout: 0,
        eligible: false,
        reason: 'Outside policy validity window',
      };
    }

    const wind = localWind(merchant, bulletin);
    const tier = tierFor(rule, wind);
    if (!tier || tier.fraction <= 0) {
      return {
        merchantId: merchant.id,
        wind,
        payout: 0,
        eligible: false,
        reason: 'Below synthetic trigger threshold',
      };
    }

    return {
      merchantId: merchant.id,
      wind,
      payout: moneyRound(merchant.sumInsured * tier.fraction, `${merchant.id}.payout`),
      eligible: true,
      reason: `Eligible at synthetic ${tier.wind} wind tier`,
    };
  });
}

function totalPayout(items: RiskItem[]): number {
  return items.reduce((sum, item) => {
    const result = sum + item.payout;
    invariant(Number.isSafeInteger(result), 'totalPayout exceeds safe centavo precision');
    return result;
  }, 0);
}

function ensureAction(
  next: Workspace,
  portfolioId: string,
  stormId: string,
  runId: string,
  type: Action['type'],
  title: string,
  detail: string,
): void {
  const existing = next.actions.find(
    (action) => action.portfolioId === portfolioId && action.stormId === stormId && action.type === type,
  );
  if (existing) {
    const changed = existing.runId !== runId || existing.title !== title || existing.detail !== detail;
    if (changed) {
      existing.runId = runId;
      existing.title = title;
      existing.detail = detail;
      existing.status = 'open';
    }
    return;
  }
  const action: Action = {
    id: `action-${type}-${slug(portfolioId)}-${slug(stormId)}`,
    portfolioId,
    stormId,
    runId,
    title,
    detail,
    type,
    status: 'open',
  };
  next.actions.push(action);
}

function closeActions(next: Workspace, portfolioId: string, stormId: string, types: Action['type'][], detail: string): void {
  for (const action of next.actions) {
    if (action.portfolioId !== portfolioId || action.stormId !== stormId) continue;
    if (!types.includes(action.type)) continue;
    action.status = 'done';
    action.detail = detail;
  }
}

function assertRunCurrent(state: Workspace, run: ModelRun): { portfolio: Portfolio; storm: Storm; bulletin: Bulletin } {
  const portfolio = requirePortfolio(state, run.portfolioId);
  const storm = requireStorm(state, run.stormId);
  invariant(portfolio.version === run.portfolioVersion, `Run ${run.id} is stale for portfolio ${portfolio.id}`);
  const bulletin = requireCurrentBulletin(state, run.portfolioId, run.stormId);
  invariant(bulletin.id === run.bulletinId, `Run ${run.id} is stale for the current bulletin`);
  invariant(storm.bulletins.some((candidate) => candidate.id === run.bulletinId), `Run ${run.id} references an invalid bulletin`);
  return { portfolio, storm, bulletin };
}

function assertRunLatest(state: Workspace, run: ModelRun): void {
  const runIndex = state.runs.findIndex((candidate) => candidate.id === run.id);
  invariant(runIndex >= 0, `Unknown model run ${run.id}`);
  const newerResult = state.runs.slice(runIndex + 1).find(
    (candidate) => candidate.portfolioId === run.portfolioId
      && candidate.stormId === run.stormId
      && (candidate.status === 'completed' || candidate.status === 'partial')
      && candidate.inputHash !== run.inputHash,
  );
  invariant(!newerResult, `Run ${run.id} is obsolete; a newer model result exists for this portfolio and storm`);
}

function activeBatchStatus(status: BatchStatus): boolean {
  return status === 'pending_approval'
    || status === 'approved'
    || status === 'executing'
    || status === 'completed'
    || status === 'partial_failure'
    || status === 'reconciliation_required';
}

function committedAmount(
  state: Workspace,
  portfolioId: string,
  merchantId: string,
  excludeBatchId?: string,
): number {
  return state.batches.reduce((sum, batch) => {
    if (batch.id === excludeBatchId || batch.portfolioId !== portfolioId || !activeBatchStatus(batch.status)) return sum;
    return batch.items.reduce((itemSum, item) => {
      const include = batch.status === 'completed'
        ? item.status === 'succeeded'
        : batch.status === 'partial_failure'
          ? item.status === 'succeeded'
          : item.status === 'succeeded' || item.status === 'pending' || item.status === 'unknown';
      return include && item.merchantId === merchantId ? itemSum + item.amount : itemSum;
    }, sum);
  }, 0);
}

function merchantFor(portfolio: Portfolio, merchantId: string): Merchant {
  const merchant = portfolio.merchants.find((candidate) => candidate.id === merchantId);
  invariant(merchant, `Unknown merchant ${merchantId} in portfolio ${portfolio.id}`);
  return merchant;
}

function assertBatchCurrent(state: Workspace, batch: Batch): { run: ModelRun; portfolio: Portfolio; storm: Storm; bulletin: Bulletin } {
  const run = requireRun(state, batch.runId);
  invariant(run.portfolioId === batch.portfolioId && run.stormId === batch.stormId, `Batch ${batch.id} does not own its run`);
  invariant(run.status === 'completed', `Batch ${batch.id} requires a completed run`);
  const current = assertRunCurrent(state, run);
  invariant(current.bulletin.phase === 'settlement', `Batch ${batch.id} requires a settlement bulletin`);
  return { run, ...current };
}

function paymentReference(paymentKey: string): string {
  return `mock-pay-${hash(paymentKey)}`;
}

function addCatalog(next: Workspace): void {
  for (const rule of importedCatalog.rules) {
    if (!next.rules.some((candidate) => candidate.id === rule.id)) {
      validateRule(rule);
      next.rules.push(structuredClone(rule) as Rule);
    }
  }
  for (const storm of importedCatalog.storms) {
    if (!next.storms.some((candidate) => candidate.id === storm.id)) {
      validateStorm(storm);
      next.storms.push(structuredClone(storm) as Storm);
    }
  }
  for (const portfolio of next.portfolios) {
    for (const storm of next.storms) {
      const key = cursorKey(portfolio.id, storm.id);
      if (next.cursors[key] === undefined) next.cursors[key] = 0;
    }
  }
}

function commandLoadDemo(state: Workspace): Workspace {
  for (const portfolio of demoDataset.portfolios) validatePortfolio(portfolio);
  for (const rule of demoDataset.rules) validateRule(rule);
  for (const storm of demoDataset.storms) validateStorm(storm);
  const next = startMutation(state);
  next.portfolios = structuredClone(demoDataset.portfolios) as Portfolio[];
  next.rules = structuredClone(demoDataset.rules) as Rule[];
  next.storms = structuredClone(demoDataset.storms) as Storm[];
  next.cursors = {};
  for (const portfolio of next.portfolios) {
    for (const storm of next.storms) {
      next.cursors[cursorKey(portfolio.id, storm.id)] = 0;
    }
  }
  next.runs = [];
  next.actions = [];
  next.batches = [];
  next.audits = [];
  next.pricing = [];
  return next;
}

function commandImportPortfolio(state: Workspace, command: Extract<Command, { type: 'import_portfolio' }>, now: string): Workspace {
  nonEmpty(command.payload.name, 'payload.name');
  invariant(Array.isArray(command.payload.merchants), 'payload.merchants must be an array');
  const idBase = `portfolio-import-${slug(command.payload.name)}`;
  const portfolio: Portfolio = {
    id: `${idBase}-${state.version + 1}`,
    name: command.payload.name.trim(),
    version: 1,
    source: 'import',
    createdAt: now,
    merchants: structuredClone(command.payload.merchants) as Merchant[],
  };
  validatePortfolio(portfolio, 'payload');
  const next = startMutation(state);
  next.portfolios.push(portfolio);
  addCatalog(next);
  for (const storm of next.storms) {
    next.cursors[cursorKey(portfolio.id, storm.id)] = 0;
  }
  appendAudit(next, now, 'Imported portfolio', `${portfolio.name} is available in the synthetic catalog`, portfolio.id, portfolio.id);
  return next;
}

function commandAdvance(state: Workspace, command: Extract<Command, { type: 'advance' }>, now: string): Workspace {
  const portfolio = requirePortfolio(state, command.portfolioId);
  const storm = requireStorm(state, command.stormId);
  const key = cursorKey(command.portfolioId, command.stormId);
  const cursor = currentCursor(state, command.portfolioId, command.stormId);
  invariant(cursor + 1 < storm.bulletins.length, `Storm ${storm.id} is already at its final bulletin`);
  const currentBulletin = storm.bulletins[cursor];
  const running = state.runs.some(
    (run) => run.portfolioId === portfolio.id && run.stormId === storm.id && run.bulletinId === currentBulletin.id && run.status === 'running',
  );
  invariant(!running, `Complete the running model for ${portfolio.id} before advancing`);
  const next = startMutation(state);
  next.cursors[key] = cursor + 1;
  appendAudit(next, now, 'Advanced bulletin', `Moved ${storm.name} to bulletin ${storm.bulletins[cursor + 1].label}`, portfolio.id, storm.id);
  return next;
}

function commandRunModel(state: Workspace, command: Extract<Command, { type: 'run_model' }>, now: string): Workspace {
  const portfolio = requirePortfolio(state, command.portfolioId);
  const rule = requireRule(state, command.ruleId);
  const storm = requireStorm(state, command.stormId);
  const bulletin = requireCurrentBulletin(state, command.portfolioId, command.stormId);
  const fault: RunFault = command.fault ?? 'none';
  invariant(fault === 'none' || fault === 'partial' || fault === 'failure', 'Unknown model fault scenario');
  invariant(
    !state.runs.some(
      (run) => run.portfolioId === portfolio.id && run.stormId === storm.id && run.bulletinId === bulletin.id && run.status === 'running',
    ),
    'A model run is already running for the current bulletin',
  );
  const items = calculateItems(portfolio, rule, bulletin);
  const next = startMutation(state);
  const run: ModelRun = {
    id: `run-${next.version}-${next.runs.length + 1}`,
    portfolioId: portfolio.id,
    portfolioVersion: portfolio.version,
    stormId: storm.id,
    bulletinId: bulletin.id,
    ruleId: rule.id,
    asOf: bulletin.issuedAt,
    createdAt: now,
    status: 'running',
    modelVersion: MODEL_VERSION,
    simulated: true,
    inputHash: hash(JSON.stringify({
      portfolioId: portfolio.id,
      portfolioVersion: portfolio.version,
      stormId: storm.id,
      bulletin,
      rule,
      merchants: portfolio.merchants,
    })),
    items,
    totalPayout: totalPayout(items),
    affectedCount: items.filter((item) => item.payout > 0).length,
  };
  if (fault === 'partial') run.error = 'synthetic-fault:partial';
  if (fault === 'failure') run.error = 'synthetic-fault:failure';
  next.runs.push(run);
  appendAudit(next, now, 'Started synthetic model run', `${storm.name} · ${bulletin.label}`, portfolio.id, run.id);
  return next;
}

function commandCompleteRun(state: Workspace, command: Extract<Command, { type: 'complete_run' }>, now: string): Workspace {
  const run = requireRun(state, command.runId);
  invariant(run.status === 'running', `Run ${run.id} is already complete`);
  const current = assertRunCurrent(state, run);
  const fault = runFault(run);
  const next = startMutation(state);
  const nextRun = next.runs.find((candidate) => candidate.id === run.id);
  invariant(nextRun, `Unknown model run ${run.id}`);
  if (fault === 'failure') {
    nextRun.status = 'failed';
    nextRun.error = 'Synthetic model failure';
  } else if (fault === 'partial') {
    nextRun.status = 'partial';
    nextRun.error = 'Synthetic partial model result';
  } else {
    nextRun.status = 'completed';
    delete nextRun.error;
  }
  if (current.bulletin.phase === 'settlement' && nextRun.status === 'completed' && nextRun.totalPayout > 0) {
    closeActions(next, run.portfolioId, run.stormId, ['review'], 'Superseded by the latest completed settlement result.');
    ensureAction(next, run.portfolioId, run.stormId, run.id, 'settlement', 'Settlement ready', 'Review the completed synthetic settlement before creating obligations.');
  } else if (current.bulletin.phase === 'settlement' && nextRun.status === 'completed') {
    closeActions(next, run.portfolioId, run.stormId, ['review', 'settlement', 'funding'], 'No positive trigger was found in the latest settlement result.');
  } else {
    if (current.bulletin.phase === 'settlement') {
      closeActions(next, run.portfolioId, run.stormId, ['settlement'], 'Superseded by the latest incomplete settlement result.');
    }
    ensureAction(next, run.portfolioId, run.stormId, run.id, 'review', 'Review model result', 'Review the synthetic model result and its evidence.');
  }
  appendAudit(next, now, 'Completed synthetic model run', `${nextRun.status} · ${current.bulletin.label}`, run.portfolioId, run.id);
  return next;
}

function commandActionDone(state: Workspace, command: Extract<Command, { type: 'action_done' }>, now: string): Workspace {
  nonEmpty(command.actionId, 'actionId');
  const action = state.actions.find((candidate) => candidate.id === command.actionId);
  invariant(action, `Unknown action ${command.actionId}`);
  if (action.status === 'done') return cloneWorkspace(state);
  const next = startMutation(state);
  const nextAction = next.actions.find((candidate) => candidate.id === command.actionId);
  invariant(nextAction, `Unknown action ${command.actionId}`);
  nextAction.status = 'done';
  appendAudit(next, now, 'Completed action', nextAction.title, nextAction.portfolioId, nextAction.id);
  return next;
}

function commandCreateBatch(state: Workspace, command: Extract<Command, { type: 'create_batch' }>, now: string): Workspace {
  const run = requireRun(state, command.runId);
  invariant(run.status === 'completed', `Run ${run.id} must be completed before creating a batch`);
  const current = assertRunCurrent(state, run);
  invariant(current.bulletin.phase === 'settlement', 'Only settlement bulletins can create payout obligations');
  assertRunLatest(state, run);
  invariant(!state.batches.some((batch) => batch.portfolioId === run.portfolioId && batch.stormId === run.stormId), 'A payout obligation already exists for this storm and portfolio');
  const positiveItems = run.items.filter((item) => item.eligible && item.payout > 0);
  invariant(positiveItems.length > 0, 'The completed run has no positive payout items');
  const next = startMutation(state);
  const batchId = `batch-${next.version}-${next.batches.length + 1}`;
  const payoutItems: PayoutItem[] = [];
  for (const [index, item] of positiveItems.entries()) {
    const merchant = merchantFor(current.portfolio, item.merchantId);
    const available = merchant.sumInsured - committedAmount(state, run.portfolioId, merchant.id);
    const amount = Math.min(item.payout, Math.max(0, available));
    if (amount <= 0) continue;
    payoutItems.push({
      id: `${batchId}-item-${index + 1}`,
      merchantId: merchant.id,
      amount: moneyRound(amount, `${merchant.id}.batchAmount`),
      status: 'pending',
      paymentKey: `obligation:${run.portfolioId}:${run.stormId}:${merchant.id}`,
      attempts: 0,
    });
  }
  invariant(payoutItems.length > 0, 'No positive payout remains after the paid limit ledger');
  const batch: Batch = {
    id: batchId,
    portfolioId: run.portfolioId,
    stormId: run.stormId,
    runId: run.id,
    version: 1,
    status: 'draft',
    createdAt: now,
    items: payoutItems,
  };
  next.batches.push(batch);
  ensureAction(next, run.portfolioId, run.stormId, run.id, 'funding', 'Fund payout batch', 'Submit the synthetic payout batch for approval.');
  closeActions(next, run.portfolioId, run.stormId, ['settlement'], 'Settlement reviewed; payout draft created.');
  appendAudit(next, now, 'Created payout batch', `${payoutItems.length} positive payout items`, run.portfolioId, batch.id);
  return next;
}

function assertBatchVersion(batch: Batch, version: number): void {
  finite(version, 'batch version');
  invariant(Number.isSafeInteger(version), 'batch version must be an integer');
  invariant(version === batch.version, `Batch ${batch.id} is stale; expected version ${batch.version}`);
}

function reserveBatch(state: Workspace, batch: Batch, portfolio: Portfolio): void {
  for (const item of batch.items) {
    const merchant = merchantFor(portfolio, item.merchantId);
    const committed = committedAmount(state, portfolio.id, merchant.id, batch.id);
    invariant(committed + item.amount <= merchant.sumInsured, `Payout limit exceeded for merchant ${merchant.id}`);
    money(item.amount, `${item.id}.amount`, false);
  }
}

function commandSubmitBatch(state: Workspace, command: BatchVersionCommand, now: string): Workspace {
  const batch = requireBatch(state, command.batchId);
  assertBatchVersion(batch, command.version);
  invariant(batch.status === 'draft', `Batch ${batch.id} cannot be submitted from ${batch.status}`);
  const current = assertBatchCurrent(state, batch);
  assertRunLatest(state, current.run);
  reserveBatch(state, batch, current.portfolio);
  const next = startMutation(state);
  const nextBatch = requireBatch(next, batch.id);
  nextBatch.status = 'pending_approval';
  nextBatch.version += 1;
  closeActions(next, batch.portfolioId, batch.stormId, ['funding'], 'Payout batch submitted for approval.');
  appendAudit(next, now, 'Submitted payout batch', 'Payout amounts are reserved pending approval', batch.portfolioId, batch.id);
  return next;
}

function commandApproveOrRejectBatch(
  state: Workspace,
  command: ApprovalBatchCommand,
  now: string,
): Workspace {
  const batch = requireBatch(state, command.batchId);
  assertBatchVersion(batch, command.version);
  invariant(batch.status === 'pending_approval', `Batch ${batch.id} cannot transition from ${batch.status}`);
  const current = assertBatchCurrent(state, batch);
  assertRunLatest(state, current.run);
  if (command.type === 'approve_batch') reserveBatch(state, batch, current.portfolio);
  const next = startMutation(state);
  const nextBatch = requireBatch(next, batch.id);
  if (command.type === 'approve_batch') {
    nextBatch.status = 'approved';
    nextBatch.approvedBy = 'mock-approver';
    nextBatch.approvedAt = now;
    appendAudit(next, now, 'Approved payout batch', 'Synthetic approval recorded', batch.portfolioId, batch.id);
  } else {
    nextBatch.status = 'rejected';
    ensureAction(next, batch.portfolioId, batch.stormId, batch.runId, 'review', 'Review rejected payout batch', 'The payout batch was rejected; review the evidence before taking further action.');
    appendAudit(next, now, 'Rejected payout batch', 'Synthetic approval was rejected', batch.portfolioId, batch.id);
  }
  nextBatch.version += 1;
  return next;
}

function commandPayBatch(state: Workspace, command: Extract<Command, { type: 'pay_batch' }>, now: string): Workspace {
  const batch = requireBatch(state, command.batchId);
  assertBatchVersion(batch, command.version);
  invariant(batch.status === 'approved', `Batch ${batch.id} cannot be paid from ${batch.status}`);
  const current = assertBatchCurrent(state, batch);
  assertRunLatest(state, current.run);
  invariant(command.scenario === 'success' || command.scenario === 'failure' || command.scenario === 'timeout', 'Unknown payment scenario');
  const next = startMutation(state);
  const nextBatch = requireBatch(next, batch.id);
  nextBatch.status = 'executing';
  for (const [index, item] of nextBatch.items.entries()) {
    item.attempts += 1;
    if (command.scenario === 'success' || index > 0) {
      item.status = 'succeeded';
      item.reference = paymentReference(item.paymentKey);
    } else if (command.scenario === 'failure') {
      item.status = 'failed';
      delete item.reference;
    } else {
      item.status = 'unknown';
      delete item.reference;
    }
  }
  if (command.scenario === 'success') nextBatch.status = 'completed';
  if (command.scenario === 'failure') nextBatch.status = 'partial_failure';
  if (command.scenario === 'timeout') nextBatch.status = 'reconciliation_required';
  nextBatch.version += 1;
  appendAudit(next, now, 'Executed payout batch', `${command.scenario} payment scenario`, batch.portfolioId, batch.id);
  return next;
}

function commandReconcileOrRetryBatch(
  state: Workspace,
  command: ResolutionBatchCommand,
  now: string,
): Workspace {
  const batch = requireBatch(state, command.batchId);
  assertBatchVersion(batch, command.version);
  assertBatchCurrent(state, batch);
  if (command.type === 'reconcile_batch') {
    invariant(batch.status === 'reconciliation_required', `Batch ${batch.id} is not waiting for reconciliation`);
    invariant(batch.items.some((item) => item.status === 'unknown'), `Batch ${batch.id} has no unknown payments to reconcile`);
  } else {
    invariant(batch.status === 'partial_failure', `Batch ${batch.id} is not waiting for a retry`);
    invariant(batch.items.some((item) => item.status === 'failed'), `Batch ${batch.id} has no failed payments to retry`);
  }
  const next = startMutation(state);
  const nextBatch = requireBatch(next, batch.id);
  const targetStatus = command.type === 'reconcile_batch' ? 'unknown' : 'failed';
  for (const item of nextBatch.items) {
    if (item.status !== targetStatus) continue;
    item.attempts += 1;
    item.status = 'succeeded';
    item.reference = paymentReference(item.paymentKey);
  }
  nextBatch.status = 'completed';
  nextBatch.version += 1;
  appendAudit(
    next,
    now,
    command.type === 'reconcile_batch' ? 'Reconciled payout batch' : 'Retried payout batch',
    'Payment keys were preserved while unresolved items succeeded',
    batch.portfolioId,
    batch.id,
  );
  return next;
}

function commandPrice(state: Workspace, command: Extract<Command, { type: 'price' }>, now: string): Workspace {
  const portfolio = requirePortfolio(state, command.portfolioId);
  const rule = requireRule(state, command.ruleId);
  finite(command.days, 'days');
  invariant(Number.isSafeInteger(command.days) && command.days > 0, 'days must be a positive integer');
  const exposure = portfolio.merchants.reduce((sum, merchant) => {
    const next = sum + merchant.sumInsured;
    invariant(Number.isSafeInteger(next), 'sum exposure exceeds safe centavo precision');
    return next;
  }, 0);
  const weightedPayout = rule.tiers.reduce((sum, tier) => sum + tier.fraction, 0) / rule.tiers.length;
  const riskCost = moneyRound(exposure * rule.annualEventRate * weightedPayout * (command.days / 365), 'riskCost');
  const expense = moneyRound(riskCost * rule.expenseRate, 'expense');
  const premium = moneyRound(riskCost + expense, 'premium');
  const next = startMutation(state);
  const pricing: PricingRun = {
    id: `pricing-${next.version}-${next.pricing.length + 1}`,
    portfolioId: portfolio.id,
    portfolioVersion: portfolio.version,
    ruleId: rule.id,
    days: command.days,
    riskCost,
    expense,
    premium,
    createdAt: now,
    assumption: 'Synthetic annualEventRate × sum insured × weighted payout, prorated by days/365; illustrative only and not a final policy.',
  };
  next.pricing.push(pricing);
  appendAudit(next, now, 'Created synthetic price indication', `${command.days} day illustrative indication`, portfolio.id, pricing.id);
  return next;
}

function commandReset(state: Workspace): Workspace {
  const next = emptyWorkspace();
  next.version = state.version + 1;
  return next;
}

export function emptyWorkspace(): Workspace {
  return {
    version: 0,
    portfolios: [],
    rules: [],
    storms: [],
    cursors: {},
    runs: [],
    actions: [],
    batches: [],
    audits: [],
    pricing: [],
  };
}

export function applyCommand(state: Workspace, command: Command, now = new Date().toISOString()): Workspace {
  invariant(state && typeof state === 'object', 'workspace state is required');
  invariant(command && typeof command === 'object', 'command is required');
  const operationAt = operationTimestamp(now);
  switch (command.type) {
    case 'load_demo':
      return commandLoadDemo(state);
    case 'import_portfolio':
      return commandImportPortfolio(state, command, operationAt);
    case 'advance':
      return commandAdvance(state, command, operationAt);
    case 'run_model':
      return commandRunModel(state, command, operationAt);
    case 'complete_run':
      return commandCompleteRun(state, command, operationAt);
    case 'action_done':
      return commandActionDone(state, command, operationAt);
    case 'create_batch':
      return commandCreateBatch(state, command, operationAt);
    case 'submit_batch':
      return commandSubmitBatch(state, command, operationAt);
    case 'approve_batch':
      return commandApproveOrRejectBatch(state, command, operationAt);
    case 'reject_batch':
      return commandApproveOrRejectBatch(state, command, operationAt);
    case 'pay_batch':
      return commandPayBatch(state, command, operationAt);
    case 'reconcile_batch':
    case 'retry_batch':
      return commandReconcileOrRetryBatch(state, command, operationAt);
    case 'price':
      return commandPrice(state, command, operationAt);
    case 'reset':
      return commandReset(state);
    default: {
      const unreachable: never = command;
      throw new Error(`Unsupported command ${(unreachable as { type: string }).type}`);
    }
  }
}
