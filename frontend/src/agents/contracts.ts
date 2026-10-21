import type { Action, Batch, Bulletin, ModelRun, Portfolio, Rule } from '../contracts';

export interface AgentSnapshot {
  portfolio: Portfolio;
  storm: { id: string; name: string };
  bulletin: Bulletin;
  rule: Rule;
  modelRun: ModelRun;
  actions: Action[];
  batches: Batch[];
}
export interface AgentFact { id: string; label: string; value: string; source: string; }
export interface AgentFinding { text: string; evidenceIds: string[]; }
export interface AgentStep {
  key: 'intelligence' | 'operations' | 'review';
  status: 'waiting' | 'running' | 'completed' | 'failed';
  summary: string;
  findings: AgentFinding[];
  startedAt?: string;
  completedAt?: string;
  error?: string;
}
export interface AgentProposal {
  id: string;
  title: string;
  detail: string;
  kind: 'review' | 'communication' | 'funding' | 'settlement';
  merchantIds: string[];
  evidenceIds: string[];
  status: 'proposed' | 'accepted' | 'dismissed' | 'done';
}
export interface AgentRun {
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
  steps: AgentStep[];
  facts: AgentFact[];
  proposals: AgentProposal[];
  review?: { verdict: 'ready' | 'needs_review'; summary: string; issues: string[] };
  error?: string;
}
export interface AgentHealth { configured: boolean; provider: string; model: string; mode: 'live' | 'simulated'; message?: string; }
