export type Page = 'overview' | 'portfolio' | 'pricing' | 'storm' | 'agents' | 'payouts' | 'evidence';
export type PaymentStatus = 'pending' | 'succeeded' | 'failed' | 'unknown';
export type BatchStatus = 'draft' | 'pending_approval' | 'approved' | 'executing' | 'completed' | 'partial_failure' | 'reconciliation_required' | 'rejected';
export interface Merchant { id: string; name: string; region: string; lat: number; lng: number; sumInsured: number; coverageStart: string; coverageEnd: string; }
export interface Portfolio { id: string; name: string; version: number; source: 'demo' | 'import'; createdAt: string; merchants: Merchant[]; }
export interface Rule { id: string; name: string; version: string; tiers: { wind: number; fraction: number }[]; expenseRate: number; annualEventRate: number; currency: 'PHP'; source: string; }
export interface TrackPoint { lat: number; lng: number; }
export interface Bulletin { id: string; issuedAt: string; label: string; wind: number; center: TrackPoint; track: TrackPoint[]; phase: 'forecast' | 'settlement'; source: string; }
export interface Storm { id: string; name: string; description: string; bulletins: Bulletin[]; }
export interface RiskItem { merchantId: string; wind: number | null; payout: number; eligible: boolean; reason: string; }
export interface ModelRun { id: string; portfolioId: string; portfolioVersion: number; stormId: string; bulletinId: string; ruleId: string; asOf: string; createdAt: string; status: 'queued' | 'running' | 'completed' | 'partial' | 'failed'; modelVersion: string; simulated: boolean; inputHash: string; items: RiskItem[]; totalPayout: number; affectedCount: number; error?: string; }
export interface Action { id: string; portfolioId: string; stormId: string; runId: string; title: string; detail: string; type: 'review' | 'funding' | 'settlement'; status: 'open' | 'done'; }
export interface PayoutItem { id: string; merchantId: string; amount: number; status: PaymentStatus; paymentKey: string; reference?: string; attempts: number; }
export interface Batch { id: string; portfolioId: string; stormId: string; runId: string; version: number; status: BatchStatus; createdAt: string; items: PayoutItem[]; approvedBy?: string; approvedAt?: string; }
export interface Audit { id: string; at: string; portfolioId?: string; title: string; detail: string; entityId?: string; }
export interface PricingRun { id: string; portfolioId: string; portfolioVersion: number; ruleId: string; days: number; riskCost: number; expense: number; premium: number; createdAt: string; assumption: string; }
export interface Workspace { version: number; portfolios: Portfolio[]; rules: Rule[]; storms: Storm[]; cursors: Record<string, number>; runs: ModelRun[]; actions: Action[]; batches: Batch[]; audits: Audit[]; pricing: PricingRun[]; }
export type Command =
 | { type: 'load_demo' }
 | { type: 'import_portfolio'; payload: { name: string; merchants: Merchant[] } }
 | { type: 'advance'; portfolioId: string; stormId: string }
 | { type: 'run_model'; portfolioId: string; stormId: string; ruleId: string; fault?: 'none' | 'partial' | 'failure' }
 | { type: 'complete_run'; runId: string }
 | { type: 'action_done'; actionId: string }
 | { type: 'create_batch'; runId: string }
 | { type: 'submit_batch' | 'approve_batch' | 'reject_batch'; batchId: string; version: number }
 | { type: 'pay_batch'; batchId: string; version: number; scenario: 'success' | 'failure' | 'timeout' }
 | { type: 'reconcile_batch' | 'retry_batch'; batchId: string; version: number }
 | { type: 'price'; portfolioId: string; ruleId: string; days: number }
 | { type: 'reset' };
export interface CommandRequest { requestId: string; expectedVersion: number; command: Command; }
export const cursorKey = (portfolioId: string, stormId: string) => `${portfolioId}:${stormId}`;
