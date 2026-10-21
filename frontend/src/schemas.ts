import { z } from 'zod';
import type { Workspace } from './contracts';

const id = z.string().min(1);
const stamp = z.iso.datetime();
const version = z.number().int().positive();
const amount = z.number().int().nonnegative();
const point = z.object({ lat: z.number().min(-90).max(90), lng: z.number().min(-180).max(180) });
export const merchantSchema = point.extend({
  id, name: z.string().trim().min(1), region: z.string().trim().min(1),
  sumInsured: z.number().int().positive(), coverageStart: stamp, coverageEnd: stamp,
}).refine(m => new Date(m.coverageStart) < new Date(m.coverageEnd), 'Coverage end must follow start');
export const portfolioImportSchema = z.object({ name: z.string().trim().min(1), merchants: z.array(merchantSchema).min(1).max(1000) })
  .refine(p => new Set(p.merchants.map(m => m.id)).size === p.merchants.length, 'Merchant IDs must be unique within the portfolio');

// HTTP and mock responses pass through the same boundary before reaching any page.
export const workspaceSchema: z.ZodType<Workspace> = z.object({
  version: z.number().int().nonnegative(),
  portfolios: z.array(z.object({ id, name: id, version, source: z.enum(['demo', 'import']), createdAt: stamp, merchants: z.array(merchantSchema) })),
  rules: z.array(z.object({ id, name: id, version: id, tiers: z.array(z.object({ wind: z.number().nonnegative(), fraction: z.number().min(0).max(1) })).min(1), expenseRate: z.number().nonnegative(), annualEventRate: z.number().nonnegative(), currency: z.literal('PHP'), source: id })),
  storms: z.array(z.object({ id, name: id, description: z.string(), bulletins: z.array(z.object({ id, issuedAt: stamp, label: id, wind: z.number().nonnegative(), center: point, track: z.array(point), phase: z.enum(['forecast', 'settlement']), source: id })).min(1) })),
  cursors: z.record(z.string(), z.number().int().nonnegative()),
  runs: z.array(z.object({ id, portfolioId: id, portfolioVersion: version, stormId: id, bulletinId: id, ruleId: id, asOf: stamp, createdAt: stamp, status: z.enum(['queued', 'running', 'completed', 'partial', 'failed']), modelVersion: id, simulated: z.boolean(), inputHash: id, items: z.array(z.object({ merchantId: id, wind: z.number().nonnegative().nullable(), payout: amount, eligible: z.boolean(), reason: z.string() })), totalPayout: amount, affectedCount: z.number().int().nonnegative(), error: z.string().optional() })),
  actions: z.array(z.object({ id, portfolioId: id, stormId: id, runId: id, title: id, detail: z.string(), type: z.enum(['review', 'funding', 'settlement']), status: z.enum(['open', 'done']) })),
  batches: z.array(z.object({ id, portfolioId: id, stormId: id, runId: id, version, status: z.enum(['draft', 'pending_approval', 'approved', 'executing', 'completed', 'partial_failure', 'reconciliation_required', 'rejected']), createdAt: stamp, items: z.array(z.object({ id, merchantId: id, amount, status: z.enum(['pending', 'succeeded', 'failed', 'unknown']), paymentKey: id, reference: z.string().optional(), attempts: z.number().int().nonnegative() })), approvedBy: z.string().optional(), approvedAt: stamp.optional() })),
  audits: z.array(z.object({ id, at: stamp, portfolioId: z.string().optional(), title: id, detail: z.string(), entityId: z.string().optional() })),
  pricing: z.array(z.object({ id, portfolioId: id, portfolioVersion: version, ruleId: id, days: z.number().int().min(1).max(365), riskCost: amount, expense: amount, premium: amount, createdAt: stamp, assumption: z.string() })),
});
