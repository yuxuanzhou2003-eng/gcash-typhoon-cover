import { z } from 'zod';
import type { Action, Batch, Bulletin, ModelRun, Portfolio, Rule } from '../contracts';
import type { AgentFact, AgentFinding, AgentHealth, AgentProposal, AgentRun, AgentSnapshot, AgentStep } from './contracts';

const BASE = (import.meta.env.VITE_AGENT_API_BASE_URL || '/api/agents').replace(/\/+$/, '');

const id = z.string().min(1);
const stamp = z.iso.datetime();
const optionalString = z.string().nullable().optional().transform(value => value ?? undefined);
const optionalStamp = stamp.nullable().optional().transform(value => value ?? undefined);
const pointSchema = z.object({ lat: z.number().min(-90).max(90), lng: z.number().min(-180).max(180) });
const merchantSchema = pointSchema.extend({
  id,
  name: z.string().trim().min(1),
  region: z.string().trim().min(1),
  sumInsured: z.number().int().positive(),
  coverageStart: stamp,
  coverageEnd: stamp,
});
const portfolioSchema = z.object({
  id,
  name: z.string().trim().min(1),
  version: z.number().int().positive(),
  source: z.enum(['demo', 'import']),
  createdAt: stamp,
  merchants: z.array(merchantSchema),
});
const bulletinSchema = z.object({
  id,
  issuedAt: stamp,
  label: z.string().trim().min(1),
  wind: z.number().nonnegative(),
  center: pointSchema,
  track: z.array(pointSchema),
  phase: z.enum(['forecast', 'settlement']),
  source: z.string().min(1),
});
const ruleSchema = z.object({
  id,
  name: z.string().trim().min(1),
  version: z.string().trim().min(1),
  tiers: z.array(z.object({ wind: z.number().nonnegative(), fraction: z.number().min(0).max(1) })).min(1),
  expenseRate: z.number().nonnegative(),
  annualEventRate: z.number().nonnegative(),
  currency: z.literal('PHP'),
  source: z.string().min(1),
});
const modelRunSchema = z.object({
  id,
  portfolioId: id,
  portfolioVersion: z.number().int().positive(),
  stormId: id,
  bulletinId: id,
  ruleId: id,
  asOf: stamp,
  createdAt: stamp,
  status: z.enum(['queued', 'running', 'completed', 'partial', 'failed']),
  modelVersion: id,
  simulated: z.boolean(),
  inputHash: id,
  items: z.array(z.object({
    merchantId: id,
    wind: z.number().nonnegative().nullable(),
    payout: z.number().int().nonnegative(),
    eligible: z.boolean(),
    reason: z.string(),
  })),
  totalPayout: z.number().int().nonnegative(),
  affectedCount: z.number().int().nonnegative(),
  error: optionalString,
});
const actionSchema = z.object({
  id,
  portfolioId: id,
  stormId: id,
  runId: id,
  title: z.string().trim().min(1),
  detail: z.string(),
  type: z.enum(['review', 'funding', 'settlement']),
  status: z.enum(['open', 'done']),
});
const batchSchema = z.object({
  id,
  portfolioId: id,
  stormId: id,
  runId: id,
  version: z.number().int().positive(),
  status: z.enum(['draft', 'pending_approval', 'approved', 'executing', 'completed', 'partial_failure', 'reconciliation_required', 'rejected']),
  createdAt: stamp,
  items: z.array(z.object({
    id,
    merchantId: id,
    amount: z.number().int().nonnegative(),
    status: z.enum(['pending', 'succeeded', 'failed', 'unknown']),
    paymentKey: id,
    reference: z.string().optional(),
    attempts: z.number().int().nonnegative(),
  })),
  approvedBy: optionalString,
  approvedAt: optionalStamp,
});

export const agentFactSchema = z.object({ id, label: z.string().trim().min(1), value: z.string(), source: z.string() });
export const agentFindingSchema = z.object({ text: z.string().trim().min(1), evidenceIds: z.array(id) });
export const agentStepSchema = z.object({
  key: z.enum(['intelligence', 'operations', 'review']),
  status: z.enum(['waiting', 'running', 'completed', 'failed']),
  summary: z.string(),
  findings: z.array(agentFindingSchema),
  startedAt: optionalStamp,
  completedAt: optionalStamp,
  error: optionalString,
});
export const agentProposalSchema = z.object({
  id,
  title: z.string().trim().min(1),
  detail: z.string(),
  kind: z.enum(['review', 'communication', 'funding', 'settlement']),
  merchantIds: z.array(id),
  evidenceIds: z.array(id),
  status: z.enum(['proposed', 'accepted', 'dismissed', 'done']),
});
export const agentRunSchema = z.object({
  id,
  version: z.number().int().positive(),
  portfolioId: id,
  portfolioVersion: z.number().int().positive(),
  stormId: id,
  modelRunId: id,
  bulletinId: id,
  ruleId: id,
  inputHash: id,
  createdAt: stamp,
  updatedAt: stamp,
  status: z.enum(['queued', 'running', 'completed', 'failed', 'cancelled']),
  mode: z.enum(['live', 'simulated']),
  model: id,
  steps: z.array(agentStepSchema),
  facts: z.array(agentFactSchema),
  proposals: z.array(agentProposalSchema),
  review: z.object({ verdict: z.enum(['ready', 'needs_review']), summary: z.string(), issues: z.array(z.string()) }).nullable().optional().transform(value => value ?? undefined),
  error: optionalString,
});
export const agentHealthSchema = z.object({
  configured: z.boolean(),
  provider: z.string(),
  model: z.string(),
  mode: z.enum(['live', 'simulated']),
  message: optionalString,
});
export const agentSnapshotSchema = z.object({
  portfolio: portfolioSchema,
  storm: z.object({ id, name: z.string().trim().min(1) }),
  bulletin: bulletinSchema,
  rule: ruleSchema,
  modelRun: modelRunSchema,
  actions: z.array(actionSchema),
  batches: z.array(batchSchema),
});
export const agentRunsSchema = z.array(agentRunSchema);

export type AgentDecision = 'accept' | 'dismiss' | 'complete';
export interface AgentDecisionRequest {
  requestId: string;
  expectedVersion: number;
  decision: AgentDecision;
  inputHash: string;
}

const agentDecisionRequestSchema = z.object({
  requestId: z.uuid(),
  expectedVersion: z.number().int().positive(),
  decision: z.enum(['accept', 'dismiss', 'complete']),
  inputHash: id,
});
const retryRequestSchema = z.object({ requestId: z.uuid(), expectedVersion: z.number().int().positive() });

export class AgentApiError extends Error {
  readonly code: 'unavailable' | 'http' | 'invalid';
  readonly status?: number;

  constructor(message: string, code: 'unavailable' | 'http' | 'invalid', status?: number) {
    super(message);
    this.name = 'AgentApiError';
    this.code = code;
    this.status = status;
  }
}

export function isAgentServiceUnavailable(error: unknown): boolean {
  return error instanceof AgentApiError && error.code === 'unavailable';
}

export function createAgentRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID();
  if (typeof globalThis.crypto?.getRandomValues === 'function') {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  throw new AgentApiError('Unable to create a request id. Please reload and retry.', 'unavailable');
}

function responseMessage(body: unknown, status: number): string {
  if (typeof body === 'object' && body !== null) {
    const candidate = body as { message?: unknown; detail?: unknown; error?: unknown };
    if (typeof candidate.message === 'string' && candidate.message.trim()) return candidate.message;
    if (typeof candidate.detail === 'string' && candidate.detail.trim()) return candidate.detail;
    if (typeof candidate.error === 'string' && candidate.error.trim()) return candidate.error;
  }
  return `Agent request failed (${status}).`;
}

async function request<T>(path: string, schema: z.ZodType<T>, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new AgentApiError('Agent service unavailable. Check the service and retry.', 'unavailable');
  }

  const raw = await response.text();
  let body: unknown;
  try {
    body = raw ? JSON.parse(raw) : undefined;
  } catch {
    throw new AgentApiError('Agent service returned an unreadable response.', response.ok ? 'invalid' : 'http', response.status);
  }
  if (!response.ok) {
    const unavailable = response.status === 502 || response.status === 503 || response.status === 504;
    throw new AgentApiError(responseMessage(body, response.status), unavailable ? 'unavailable' : 'http', response.status);
  }
  const parsed = schema.safeParse(body);
  if (!parsed.success) {
    throw new AgentApiError('Agent service returned an invalid response.', 'invalid', response.status);
  }
  return parsed.data;
}

function post(body: unknown): RequestInit {
  return { method: 'POST', body: JSON.stringify(body) };
}

export const agentHealthKey = ['agent-health'] as const;
export const agentRunsKey = (portfolioId: string, stormId: string) => ['agent-runs', portfolioId, stormId] as const;

export function isRiskSnapshotReady(snapshot: AgentSnapshot | null): snapshot is AgentSnapshot {
  return Boolean(snapshot && (snapshot.modelRun.status === 'completed' || snapshot.modelRun.status === 'partial'));
}

export function isAgentRunCompatible(run: AgentRun, snapshot: AgentSnapshot | null): boolean {
  if (!snapshot) return false;
  return run.portfolioId === snapshot.portfolio.id
    && run.portfolioVersion === snapshot.portfolio.version
    && run.stormId === snapshot.storm.id
    && run.modelRunId === snapshot.modelRun.id
    && run.bulletinId === snapshot.bulletin.id
    && run.ruleId === snapshot.rule.id
    && run.inputHash === snapshot.modelRun.inputHash;
}

export interface StartAgentRunRequest {
  requestId: string;
  snapshot: AgentSnapshot;
}

type StartRunInput = AgentSnapshot | StartAgentRunRequest;

function startRunBody(input: StartRunInput, requestId?: string): StartAgentRunRequest {
  const body = 'snapshot' in input
    ? input
    : { requestId: requestId || createAgentRequestId(), snapshot: input };
  return { requestId: z.uuid().parse(body.requestId), snapshot: agentSnapshotSchema.parse(body.snapshot) };
}

export const agentApi = {
  health: () => request('/health', agentHealthSchema),
  listRuns: (portfolioId: string, stormId: string) => {
    const query = new URLSearchParams({ portfolioId, stormId });
    return request(`/runs?${query.toString()}`, agentRunsSchema);
  },
  startRun: (input: StartRunInput, requestId?: string) => request('/runs', agentRunSchema, post(startRunBody(input, requestId))),
  decideProposal: (runId: string, proposalId: string, decision: AgentDecisionRequest) => request(`/runs/${encodeURIComponent(runId)}/proposals/${encodeURIComponent(proposalId)}`, agentRunSchema, post(agentDecisionRequestSchema.parse(decision))),
  retryRun: (runId: string, expectedVersion: number, requestId = createAgentRequestId()) => request(`/runs/${encodeURIComponent(runId)}/retry`, agentRunSchema, post(retryRequestSchema.parse({ requestId, expectedVersion }))),
};

// Small aliases keep the integration surface explicit for pages that prefer verbs.
export const getAgentHealth = agentApi.health;
export const listAgentRuns = agentApi.listRuns;
export const startAgentRun = agentApi.startRun;
export const decideAgentProposal = agentApi.decideProposal;
export const retryAgentRun = agentApi.retryRun;

// Keep the contract types visible at the boundary for consumers that only import the API module.
export type { Action, Batch, Bulletin, ModelRun, Portfolio, Rule, AgentFact, AgentFinding, AgentHealth, AgentProposal, AgentRun, AgentSnapshot, AgentStep };
