import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, ArrowRight, Check, ChevronRight, ExternalLink, FileSearch, History, LoaderCircle, Play, RefreshCw, RotateCcw, ShieldCheck, Sparkles } from 'lucide-react';
import type { AgentFinding, AgentHealth, AgentProposal, AgentRun, AgentSnapshot, AgentStep } from './contracts';
import { AgentApiError, agentApi, agentHealthKey, agentRunsKey, createAgentRequestId, isAgentRunCompatible, isRiskSnapshotReady } from './api';
import './agents.css';
import './workspace-flow.css';

export interface AgentWorkspaceProps {
  snapshot: AgentSnapshot | null;
  portfolioId: string;
  stormId: string;
  onOpenStorm: () => void;
  onOpenPayouts: (runId?: string, proposalId?: string) => void;
  onStartAgents: () => void;
  startingAgents: boolean;
  startError?: string;
  requestedRunId?: string;
  preparation?: AgentPreparation;
}

export interface AgentPreparation {
  reason: 'portfolio' | 'storm' | 'rule' | 'running' | 'failed' | 'stale' | 'missing';
  busy: boolean;
  onRunAnalysis: () => void;
  onChooseInputs: () => void;
}

const STEP_ORDER: AgentStep['key'][] = ['intelligence', 'operations', 'review'];

const STEP_COPY: Record<AgentStep['key'], { number: string; name: string; title: string; description: string }> = {
  intelligence: { number: '01', name: 'Intelligence', title: 'Read the risk evidence', description: 'Finds the facts that support the response.' },
  operations: { number: '02', name: 'Operations', title: 'Draft the follow-up', description: 'Turns findings into operator tasks.' },
  review: { number: '03', name: 'Review', title: 'Check task readiness', description: 'Checks evidence before human approval.' },
};

type FlowStatus = AgentStep['status'] | 'queued';
type Decision = 'accept' | 'dismiss';

function formatStamp(value?: string): string {
  if (!value) return 'Time not available';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Time not available';
  return `${new Intl.DateTimeFormat('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Manila' }).format(date)} PHT`;
}

function statusLabel(status: AgentRun['status'] | AgentStep['status'] | FlowStatus): string {
  return status === 'queued' ? 'Queued' : status === 'running' ? 'Running' : status === 'completed' ? 'Completed' : status === 'failed' ? 'Failed' : status === 'cancelled' ? 'Cancelled' : status === 'waiting' ? 'Waiting' : 'Ready';
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Agent service unavailable. Check the service and retry.';
}

function isActiveRun(run: AgentRun): boolean {
  return run.status === 'queued' || run.status === 'running';
}

function runSnapshotTime(run: AgentRun): string {
  const fact = run.facts.find(item => {
    const id = item.id.toLowerCase().replaceAll('_', '-');
    const label = item.label.toLowerCase();
    return id === 'model-run:as-of' || id === 'model-run-as-of' || (label.includes('model run') && label.includes('as of'));
  });
  return fact?.value || run.updatedAt;
}

function getStep(run: AgentRun | undefined, key: AgentStep['key']): AgentStep | undefined {
  return run?.steps.find(step => step.key === key);
}

function getFlowStatus(run: AgentRun | undefined, key: AgentStep['key'], startingAgents: boolean): FlowStatus {
  if (!run) return startingAgents && key === 'intelligence' ? 'queued' : 'waiting';
  const step = getStep(run, key);
  if (step?.status && step.status !== 'waiting') return step.status;
  if (run.status === 'queued' && key === 'intelligence') return 'queued';
  return step?.status || 'waiting';
}

function flowStatusLabel(status: FlowStatus): string {
  return status === 'completed' ? 'Complete' : status === 'running' ? 'In progress' : status === 'failed' ? 'Needs attention' : status === 'queued' ? 'Queued' : 'Waiting';
}

function runName(run: AgentRun, snapshot: AgentSnapshot | null): string {
  if (snapshot && isAgentRunCompatible(run, snapshot)) return `${snapshot.storm.name} · ${snapshot.bulletin.label}`;
  return `Agent review · ${formatStamp(run.createdAt)}`;
}

function ProviderBadge({ health, loading, unavailable }: { health?: AgentHealth; loading: boolean; unavailable: boolean }) {
  const live = health?.mode === 'live';
  const mode = live ? 'Live AI' : health?.mode === 'simulated' ? 'Simulated AI' : loading ? 'Checking service' : 'Agent service';
  const detail = health?.provider && health.model ? `${health.provider} · ${health.model}` : unavailable ? 'Unavailable' : 'Provider status';
  return <div className={`agent-provider-badge ${unavailable ? 'is-unavailable' : live ? 'is-live' : 'is-simulated'}`} aria-label={`${mode}: ${detail}`}><span className="agent-provider-dot" /><span><strong>{mode}</strong><small>{detail}</small></span></div>;
}

function ServiceAlert({ healthQuery, runsQuery, onRetry }: { healthQuery: ReturnType<typeof useQuery<AgentHealth>>; runsQuery: ReturnType<typeof useQuery<AgentRun[]>>; onRetry: () => void }) {
  const unavailable = healthQuery.isError || (healthQuery.isSuccess && !healthQuery.data.configured) || runsQuery.isError;
  if (!unavailable) return null;
  const message = healthQuery.isError ? errorMessage(healthQuery.error) : healthQuery.data && !healthQuery.data.configured ? healthQuery.data.message || 'Agent service unavailable. Configure the provider before running the pipeline.' : runsQuery.isError ? errorMessage(runsQuery.error) : 'Agent service unavailable.';
  return <div className="agent-alert" role="alert"><AlertCircle size={17} /><div><strong>Agent service unavailable</strong><p>{message}</p></div><button className="agent-button agent-button-secondary" type="button" onClick={onRetry} disabled={healthQuery.isFetching || runsQuery.isFetching}><RefreshCw size={14} className={healthQuery.isFetching || runsQuery.isFetching ? 'agent-spin' : undefined} />Retry</button></div>;
}

function EvidenceFact({ fact }: { fact: { id: string; label: string; value: string; source: string } }) {
  const sourceIsLink = /^(https?:\/\/|mailto:)/i.test(fact.source);
  return <div className="agent-fact workspace-flow-fact"><div><strong>{fact.label}</strong><span>{fact.value}</span></div><small><span className="workspace-flow-fact-id">{fact.id}</span>{sourceIsLink ? <a href={fact.source} target="_blank" rel="noreferrer">{fact.source}<ExternalLink size={11} /></a> : fact.source || 'Source not supplied'}</small></div>;
}

function evidenceFacts(run: AgentRun, ids: string[]) {
  return ids.map(id => run.facts.find(fact => fact.id === id)).filter((fact): fact is AgentRun['facts'][number] => Boolean(fact));
}

function FindingEvidence({ finding, run }: { finding: AgentFinding; run: AgentRun }) {
  const facts = evidenceFacts(run, finding.evidenceIds);
  return <div className="agent-finding"><p>{finding.text}</p>{finding.evidenceIds.length > 0 && <details className="agent-evidence"><summary><FileSearch size={13} />{finding.evidenceIds.length} evidence {finding.evidenceIds.length === 1 ? 'fact' : 'facts'}<ChevronRight size={13} /></summary><div className="agent-fact-list">{facts.length ? facts.map(fact => <EvidenceFact key={fact.id} fact={fact} />) : <p className="agent-muted">Evidence details are not available in this run.</p>}</div></details>}</div>;
}

function FlowStage({ stage, run, startingAgents }: { stage: AgentStep['key']; run?: AgentRun; startingAgents: boolean }) {
  const copy = STEP_COPY[stage];
  const step = getStep(run, stage);
  const status = getFlowStatus(run, stage, startingAgents);
  const active = status === 'running' || status === 'queued';
  const findings = step?.findings || [];
  const hasDetails = Boolean(step?.summary || findings.length > 0 || step?.error);
  const detailLabel = step?.summary ? 'View stage details' : findings.length ? `View ${findings.length} finding${findings.length === 1 ? '' : 's'}` : 'Stage details';
  return <li className={`workspace-flow-stage agent-step agent-step-${status} ${active ? 'is-active' : ''}`}>
    <div className="workspace-flow-stage-index" aria-hidden="true">{status === 'completed' ? <Check size={15} /> : status === 'running' ? <LoaderCircle size={15} className="agent-spin" /> : status === 'failed' ? <AlertCircle size={15} /> : <span>{copy.number}</span>}</div>
    <div className="workspace-flow-stage-body">
      <div className="workspace-flow-stage-heading"><span className="workspace-flow-stage-name">{copy.name}</span><span className="workspace-flow-stage-status">{flowStatusLabel(status)}</span></div>
      <p className="workspace-flow-stage-logic">{copy.description}</p>
      {hasDetails && <details className="agent-findings-disclosure workspace-flow-stage-evidence"><summary>{detailLabel}<ChevronRight size={13} /></summary><div className="workspace-flow-stage-details"><h4>{copy.title}</h4>{step?.summary && <p className="workspace-flow-stage-result">{step.summary}</p>}{step?.error && <p className="workspace-flow-stage-error"><AlertCircle size={13} />{step.error}</p>}{run && findings.length > 0 && <div className="agent-findings">{findings.map((finding, index) => <FindingEvidence key={`${stage}-${index}`} finding={finding} run={run} />)}</div>}<small>{step?.completedAt ? `Completed ${formatStamp(step.completedAt)}` : step?.startedAt ? `Started ${formatStamp(step.startedAt)}` : run && status === 'queued' ? 'Waiting to start' : 'Ready when the previous stage completes'}</small></div></details>}
    </div>
  </li>;
}

function FlowOverview({ run, startingAgents }: { run?: AgentRun; startingAgents: boolean }) {
  return <section className="workspace-flow-overview" aria-label="Automatic review stages">
    <ol className="workspace-flow-stage-list">{STEP_ORDER.map(stage => <FlowStage key={stage} stage={stage} run={run} startingAgents={startingAgents} />)}</ol>
  </section>;
}

function PreparationNextAction({ preparation, onOpenStorm }: { preparation?: AgentPreparation; onOpenStorm: () => void }) {
  if (!preparation) return <section className="workspace-flow-next-action is-waiting" aria-label="Next action"><div className="workspace-flow-next-icon"><ShieldCheck size={18} /></div><div className="workspace-flow-next-copy"><h3>Prepare a compatible risk snapshot</h3><p>The three agent stages use the selected portfolio, bulletin and risk result. Start in the storm workspace, then return here.</p></div><button className="agent-button agent-button-secondary" type="button" onClick={onOpenStorm}><History size={14} />Open storm workspace</button></section>;

  const copy = {
    portfolio: { title: 'Choose a portfolio', description: 'Select or load a portfolio before preparing a risk analysis.', action: 'Choose portfolio', icon: <History size={18} />, handler: preparation.onChooseInputs },
    storm: { title: 'Choose a storm', description: 'Select a storm to set the bulletin and event timeline.', action: 'Choose storm', icon: <History size={18} />, handler: preparation.onChooseInputs },
    rule: { title: 'Choose product rules', description: 'Select product rules before running a risk analysis.', action: 'Choose rules', icon: <History size={18} />, handler: preparation.onChooseInputs },
    running: { title: 'Risk analysis is running', description: 'Wait for the current risk result before starting the three agent stages.', action: '', icon: <LoaderCircle size={18} className="agent-spin" />, handler: undefined },
    failed: { title: 'Analysis needs attention', description: 'Retry the risk analysis to prepare a current snapshot for the agents.', action: 'Retry analysis', icon: <RefreshCw size={18} />, handler: preparation.onRunAnalysis },
    stale: { title: 'Update the risk analysis', description: 'The selected inputs changed after the last result. Create a current snapshot first.', action: 'Update risk analysis', icon: <RefreshCw size={18} />, handler: preparation.onRunAnalysis },
    missing: { title: 'Run a risk analysis', description: 'A current risk snapshot is required before the agent pipeline can start.', action: 'Run risk analysis', icon: <Play size={18} />, handler: preparation.onRunAnalysis },
  } as const;
  const state = copy[preparation.reason];
  return <section className={`workspace-flow-next-action is-${preparation.reason}`} aria-label="Next action"><div className="workspace-flow-next-icon">{state.icon}</div><div className="workspace-flow-next-copy"><h3>{state.title}</h3><p>{state.description}</p></div>{state.handler && <button className="agent-button agent-button-primary" type="button" onClick={state.handler} disabled={preparation.busy}>{preparation.reason === 'missing' ? <Play size={14} /> : preparation.reason === 'failed' || preparation.reason === 'stale' ? <RefreshCw size={14} /> : <History size={14} />}{state.action}</button>}</section>;
}

function merchantNameFromFact(fact: AgentRun['facts'][number]): string {
  const separator = fact.value.indexOf(';');
  if (separator > 0) return fact.value.slice(0, separator).trim();
  return fact.label.replace(/^Merchant\s+/i, '').trim() || fact.id.replace(/^merchant:/, '');
}

function merchantNameMap(run: AgentRun, snapshot: AgentSnapshot | null, compatible: boolean): Map<string, string> {
  if (compatible && snapshot) return new Map(snapshot.portfolio.merchants.map(merchant => [merchant.id, merchant.name]));
  return new Map(run.facts.filter(fact => fact.id.startsWith('merchant:')).map(fact => [fact.id.slice('merchant:'.length), merchantNameFromFact(fact)]));
}

function proposalLabel(kind: AgentProposal['kind']): string {
  return kind === 'communication' ? 'Communication draft' : kind === 'funding' ? 'Funding review' : kind === 'settlement' ? 'Settlement review' : 'Review task';
}

function ProposalCard({ proposal, run, snapshot, onDecision, onOpenPayouts, busy }: { proposal: AgentProposal; run: AgentRun; snapshot: AgentSnapshot | null; onDecision: (proposal: AgentProposal, decision: Decision) => void; onOpenPayouts: (runId?: string, proposalId?: string) => void; busy: boolean }) {
  const compatible = isAgentRunCompatible(run, snapshot);
  const reviewReady = compatible && run.status === 'completed' && run.review?.verdict === 'ready';
  const reviewNeedsAttention = compatible && run.status === 'completed' && run.review?.verdict === 'needs_review';
  const canAccept = proposal.status === 'proposed' && reviewReady;
  const canDismiss = proposal.status === 'proposed' && compatible && (run.status === 'completed' || run.status === 'failed');
  const names = merchantNameMap(run, snapshot, compatible);
  const merchants = proposal.merchantIds.map(id => names.get(id) || id);
  const facts = evidenceFacts(run, proposal.evidenceIds);
  const statusText = proposal.status === 'proposed' ? 'Proposed' : proposal.status === 'accepted' ? 'Accepted' : proposal.status === 'done' ? 'Done' : 'Dismissed';
  return <article className={`agent-proposal workspace-flow-draft-card agent-proposal-${proposal.status}`}>
    <span className={`workspace-flow-kind workspace-flow-kind-${proposal.kind}`} aria-label={proposalLabel(proposal.kind)}>{proposalLabel(proposal.kind)}</span>
    <div className="workspace-flow-task-content">
      <div className="agent-proposal-heading"><div><h4>{proposal.title}</h4><span className={`agent-proposal-status agent-proposal-status-${proposal.status}`}>{statusText}</span></div><div className="agent-proposal-actions">
        {proposal.status === 'proposed' && <><button className="agent-button agent-button-primary" type="button" disabled={!canAccept || busy} onClick={() => onDecision(proposal, 'accept')}>{busy ? <LoaderCircle size={14} className="agent-spin" /> : <Check size={14} />}Accept &amp; open task</button><button className="agent-button agent-button-quiet" type="button" disabled={!canDismiss || busy} onClick={() => onDecision(proposal, 'dismiss')}>Dismiss</button></>}
        {proposal.status === 'accepted' && <button className="agent-button agent-button-secondary" type="button" onClick={() => onOpenPayouts(run.id, proposal.id)}><ArrowRight size={14} />Open task</button>}
        {proposal.status === 'done' && <span className="agent-proposal-finished"><Check size={14} />Completed in Actions &amp; payouts</span>}
        {proposal.status === 'dismissed' && <span className="agent-proposal-finished">Dismissed by a human</span>}
      </div></div>
      <p className="workspace-flow-task-detail">{proposal.detail}</p>
      <div className="workspace-flow-draft-meta"><span>{merchants.length ? <><strong>{merchants.length === 1 ? 'Merchant' : 'Merchants'}</strong> {merchants.join(', ')}</> : <><strong>Scope</strong> Portfolio-level task</>}</span><span><strong>Evidence</strong> {proposal.evidenceIds.length ? `${facts.length} of ${proposal.evidenceIds.length} facts available` : 'No linked evidence'}</span></div>
      {proposal.evidenceIds.length > 0 && <details className="agent-evidence workspace-flow-draft-evidence"><summary><FileSearch size={13} />Inspect supporting facts<ChevronRight size={13} /></summary><div className="agent-fact-list">{facts.length ? facts.map(fact => <EvidenceFact key={fact.id} fact={fact} />) : <p className="agent-muted">Evidence details are not available in this run.</p>}</div></details>}
      {proposal.status === 'proposed' && !reviewReady && <p className="agent-proposal-note">{reviewNeedsAttention ? 'The AI check needs correction before a human can accept this task.' : run.status === 'completed' ? 'The review stage must be ready before this task can be accepted.' : 'This task becomes available after all three stages finish.'}</p>}
      {!compatible && <p className="agent-proposal-note agent-proposal-stale">This draft belongs to an older risk snapshot and is read-only here.</p>}
    </div>
  </article>;
}

function ReviewGate({ run, compatible, onOpenStorm }: { run: AgentRun; compatible: boolean; onOpenStorm: () => void }) {
  const ready = compatible && run.status === 'completed' && run.review?.verdict === 'ready';
  const needsReview = compatible && run.status === 'completed' && run.review?.verdict === 'needs_review';
  const historical = !compatible;
  const issues = run.review?.issues || [];
  return <section className={`workspace-flow-review-gate ${ready ? 'is-ready' : needsReview ? 'is-needs-review' : 'is-pending'}`} aria-label="Review gate">
    <div className="workspace-flow-review-heading"><div className="workspace-flow-review-leading">{ready ? <><Check size={15} /><span className="agent-review-ready-label">Review ready</span></> : <h3>{historical ? 'Historical check' : needsReview ? 'AI check needs correction' : 'AI check in progress'}</h3>}</div>{!ready && <span className="agent-review-chip is-pending">{historical ? 'Historical' : needsReview ? 'Needs review' : 'Review pending'}</span>}<details className="workspace-flow-review-details"><summary>{historical ? 'Why this run is read-only' : 'View review details'}<ChevronRight size={13} /></summary><div>{historical ? <p>Run the pipeline again for the selected inputs before recording a decision.</p> : <><p>{run.review?.summary || 'The Review stage will check the draft citations and input completeness.'}</p>{issues.length > 0 && <><strong>What to fix</strong><ul>{issues.map((issue, index) => <li key={`${issue}-${index}`}>{issue}</li>)}</ul></>}</>}</div></details></div>
    {!ready && <p className="workspace-flow-review-summary">{historical ? 'This result is read-only for the current inputs.' : needsReview ? 'Correct the input evidence before accepting a task.' : 'The AI check is still running.'}</p>}
    {needsReview && <button className="agent-button agent-button-secondary" type="button" onClick={onOpenStorm}><History size={14} />Open storm workspace to correct inputs</button>}
  </section>;
}

function NextActionPanel({ currentSnapshot, preparation, run, compatible, serviceReady, startingAgents, startError, onStartAgents, onOpenStorm, onRetry }: { currentSnapshot: AgentSnapshot | null; preparation?: AgentPreparation; run?: AgentRun; compatible: boolean; serviceReady: boolean; startingAgents: boolean; startError?: string; onStartAgents: () => void; onOpenStorm: () => void; onRetry: () => void }) {
  if (!currentSnapshot) return <PreparationNextAction preparation={preparation} onOpenStorm={onOpenStorm} />;
  if (startError) return <section className="workspace-flow-next-action is-failed" aria-label="Next action"><div className="workspace-flow-next-icon"><AlertCircle size={18} /></div><div className="workspace-flow-next-copy"><h3>Agents could not be started</h3><p>{startError}</p></div><button className="agent-button agent-button-secondary" type="button" onClick={onStartAgents} disabled={!serviceReady || startingAgents}><RefreshCw size={14} />Retry agents</button></section>;
  if (startingAgents || (run && isActiveRun(run))) {
    const activeStage = STEP_ORDER.find(stage => getFlowStatus(run, stage, startingAgents) === 'running') || STEP_ORDER.find(stage => getFlowStatus(run, stage, startingAgents) === 'queued') || 'intelligence';
    return <section className="workspace-flow-next-action is-running" aria-label="Next action" aria-live="polite"><div className="workspace-flow-next-icon"><LoaderCircle size={18} className="agent-spin" /></div><div className="workspace-flow-next-copy"><h3>{STEP_COPY[activeStage].name} is working</h3><p>All three stages run in sequence. The next update will appear here as the agents move from findings to draft tasks to review.</p></div></section>;
  }
  if (run && !compatible) return <section className="workspace-flow-next-action is-stale" aria-label="Next action"><div className="workspace-flow-next-icon"><AlertCircle size={18} /></div><div className="workspace-flow-next-copy"><h3>Run this pipeline again for the current inputs</h3><p>This result belongs to an older portfolio, bulletin, rule or model run. Its evidence remains available for context, but decisions are gated.</p></div><button className="agent-button agent-button-secondary" type="button" onClick={onStartAgents} disabled={!serviceReady || startingAgents}><RefreshCw size={14} />Run agents</button></section>;
  if (run?.status === 'failed') return <section className="workspace-flow-next-action is-failed" aria-label="Next action"><div className="workspace-flow-next-icon"><AlertCircle size={18} /></div><div className="workspace-flow-next-copy"><h3>Retry the agent run</h3><p>{run.error || 'The pipeline could not finish. Retry this run with the same current snapshot.'}</p></div><button className="agent-button agent-button-secondary" type="button" onClick={onRetry} disabled={startingAgents}><RotateCcw size={14} />Retry run</button></section>;
  if (run?.status === 'completed') return null;
  return <section className="workspace-flow-next-action is-waiting" aria-label="Next action"><div className="workspace-flow-next-icon"><Play size={18} /></div><div className="workspace-flow-next-copy"><h3>Run all three agent stages</h3><p>The current risk snapshot is ready. Intelligence, Operations and Review will run automatically before you choose any task.</p></div><button className="agent-button agent-button-primary" type="button" onClick={onStartAgents} disabled={!serviceReady || startingAgents}><Play size={14} />Run agents</button></section>;
}

function RunDetail({ run, snapshot, onDecision, decisionBusy, onOpenStorm, onOpenPayouts }: { run: AgentRun; snapshot: AgentSnapshot | null; onDecision: (proposal: AgentProposal, decision: Decision) => void; decisionBusy: boolean; onOpenStorm: () => void; onOpenPayouts: (runId?: string, proposalId?: string) => void }) {
  const compatible = isAgentRunCompatible(run, snapshot);
  const proposed = run.proposals.filter(proposal => proposal.status === 'proposed');
  const accepted = run.proposals.filter(proposal => proposal.status === 'accepted' || proposal.status === 'done');
  const awaitingFollowUp = run.proposals.some(proposal => proposal.status === 'accepted');
  const taskHeading = proposed.length > 0 ? 'Review proposed tasks' : awaitingFollowUp ? 'Accepted tasks' : accepted.length > 0 ? 'Completed tasks' : 'Reviewed tasks';
  const taskGuidance = proposed.length > 0 ? 'Inspect the evidence, then accept the work to carry into Actions & payouts.' : awaitingFollowUp ? 'Open a task to carry out its checks and record your follow-up.' : 'All decisions for this run are recorded. Evidence remains available below.';
  return <div className="agent-run-detail workspace-flow-run-detail">
    {!compatible && <div className="agent-stale-notice workspace-flow-stale-notice" role="status"><AlertCircle size={15} /><div><strong>This result is stale</strong><p>The selected portfolio, bulletin, rule or risk run changed after this result was created. Review it for context, then run the pipeline again.</p></div></div>}
    {run.error && <div className="workspace-flow-run-error" role="alert"><AlertCircle size={14} /><span>{run.error}</span></div>}
    <ReviewGate run={run} compatible={compatible} onOpenStorm={onOpenStorm} />
    <section className="agent-proposals workspace-flow-drafts" aria-labelledby="workspace-flow-drafts-title"><div className="agent-section-heading"><div><div className="workspace-flow-task-heading"><h3 id="workspace-flow-drafts-title" tabIndex={-1}>{taskHeading}</h3>{proposed.length > 0 && <span className="workspace-flow-pending-count">{proposed.length} pending</span>}</div><p>{taskGuidance}</p></div>{accepted[0] && <button className="agent-link-button" type="button" onClick={() => onOpenPayouts(run.id, accepted[0].id)}>Open Actions &amp; payouts <ExternalLink size={13} /></button>}</div>{run.proposals.length ? <div className="agent-proposal-list workspace-flow-draft-list">{run.proposals.map(proposal => <ProposalCard key={proposal.id} proposal={proposal} run={run} snapshot={snapshot} onDecision={onDecision} onOpenPayouts={onOpenPayouts} busy={decisionBusy} />)}</div> : <div className="agent-no-proposals workspace-flow-no-proposals"><Sparkles size={17} /><p>No draft tasks were produced for this snapshot.</p></div>}</section>
    <details className="workspace-flow-run-details"><summary>View run details</summary><dl><div><dt>Run status</dt><dd><span className={`agent-run-status agent-run-status-${run.status}`}>{statusLabel(run.status)}</span></dd></div><div><dt>Run ID</dt><dd>{run.id}</dd></div><div><dt>Risk snapshot</dt><dd>{formatStamp(runSnapshotTime(run))}</dd></div><div><dt>Input fingerprint</dt><dd>{run.inputHash}</dd></div><div><dt>Provider</dt><dd>{run.model} · {run.mode === 'live' ? 'Live' : 'Simulated'}</dd></div><div><dt>Started</dt><dd>{formatStamp(run.createdAt)}</dd></div></dl></details>
  </div>;
}

function RunHistoryPicker({ runs, selectedRun, selectedRunId, onSelect, snapshot, loading }: { runs: AgentRun[]; selectedRun?: AgentRun; selectedRunId: string; onSelect: (runId: string) => void; snapshot: AgentSnapshot | null; loading: boolean }) {
  return <label className="workspace-flow-history-picker"><span>History</span><select aria-label="Agent run history" value={selectedRunId || selectedRun?.id || ''} onChange={event => onSelect(event.target.value)} disabled={loading}>
    <option value="">{loading ? 'Loading runs…' : runs.length ? 'Select a run' : 'No runs yet'}</option>
    {runs.map(run => <option key={run.id} value={run.id}>{runName(run, snapshot)} · {formatStamp(run.updatedAt)}{!isAgentRunCompatible(run, snapshot) ? ' · Stale' : ''}</option>)}
  </select></label>;
}

export function AgentWorkspace({ snapshot, portfolioId, stormId, onOpenStorm, onOpenPayouts, onStartAgents, startingAgents, startError, requestedRunId, preparation }: AgentWorkspaceProps) {
  const queryClient = useQueryClient();
  const healthQuery = useQuery({ queryKey: agentHealthKey, queryFn: agentApi.health, retry: false, staleTime: 30_000 });
  const runsQueryEnabled = Boolean(portfolioId && stormId && healthQuery.isSuccess && healthQuery.data.configured);
  const runsQuery = useQuery({ queryKey: agentRunsKey(portfolioId, stormId), queryFn: () => agentApi.listRuns(portfolioId, stormId), enabled: runsQueryEnabled, retry: false, refetchInterval: query => { const runs = query.state.data as AgentRun[] | undefined; return runs?.some(isActiveRun) ? 1000 : 4000; } });
  const [selectedRunId, setSelectedRunId] = useState('');
  const appliedRequestedRunId = useRef('');
  const [localError, setLocalError] = useState('');
  const currentSnapshot = isRiskSnapshotReady(snapshot) ? snapshot : null;
  const runsLoading = runsQueryEnabled && runsQuery.isFetching && !runsQuery.data;
  const runs = useMemo(() => [...(runsQuery.data || [])].filter(run => run.portfolioId === portfolioId && run.stormId === stormId).sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()), [runsQuery.data, portfolioId, stormId]);

  useEffect(() => {
    if (!requestedRunId) {
      appliedRequestedRunId.current = '';
      return;
    }
    if (requestedRunId !== appliedRequestedRunId.current) {
      appliedRequestedRunId.current = requestedRunId;
      setSelectedRunId(requestedRunId);
    }
  }, [requestedRunId]);

  useEffect(() => {
    if (requestedRunId) return;
    if (!runs.length) {
      setSelectedRunId('');
      return;
    }
    if (!selectedRunId || !runs.some(run => run.id === selectedRunId)) setSelectedRunId(runs[0].id);
  }, [runs, selectedRunId, requestedRunId]);

  const selectedRun = runs.find(run => run.id === selectedRunId) || (requestedRunId ? undefined : runs[0]);
  const compatibleActiveRun = Boolean(currentSnapshot && runs.some(run => isAgentRunCompatible(run, currentSnapshot) && isActiveRun(run)));
  const decisionMutation = useMutation({
    mutationFn: ({ proposal, decision }: { proposal: AgentProposal; decision: Decision }) => {
      if (!selectedRun || !currentSnapshot || !isAgentRunCompatible(selectedRun, currentSnapshot)) throw new AgentApiError('This agent result is stale. Run the pipeline again before recording a decision.', 'http', 409);
      return agentApi.decideProposal(selectedRun.id, proposal.id, { requestId: createAgentRequestId(), expectedVersion: selectedRun.version, decision, inputHash: selectedRun.inputHash });
    },
    onSuccess: (run, variables) => {
      setLocalError('');
      setSelectedRunId(run.id);
      queryClient.setQueryData<AgentRun[]>(agentRunsKey(portfolioId, stormId), previous => [run, ...(previous || []).filter(item => item.id !== run.id)]);
      void queryClient.invalidateQueries({ queryKey: agentRunsKey(portfolioId, stormId) });
      if (variables.decision === 'accept') onOpenPayouts(run.id, variables.proposal.id);
    },
    onError: error => setLocalError(errorMessage(error)),
  });
  const retryMutation = useMutation({
    mutationFn: () => {
      if (!selectedRun || !currentSnapshot || !isAgentRunCompatible(selectedRun, currentSnapshot)) throw new AgentApiError('This agent result is stale. Run the pipeline again before retrying.', 'http', 409);
      return agentApi.retryRun(selectedRun.id, selectedRun.version, createAgentRequestId());
    },
    onSuccess: run => {
      setLocalError('');
      setSelectedRunId(run.id);
      queryClient.setQueryData<AgentRun[]>(agentRunsKey(portfolioId, stormId), previous => [run, ...(previous || []).filter(item => item.id !== run.id)]);
      void queryClient.invalidateQueries({ queryKey: agentRunsKey(portfolioId, stormId) });
    },
    onError: error => setLocalError(errorMessage(error)),
  });

  const retryService = () => {
    setLocalError('');
    void healthQuery.refetch().then(result => {
      if (result.data?.configured) void runsQuery.refetch();
    });
  };
  const unavailable = healthQuery.isError || (healthQuery.isSuccess && !healthQuery.data.configured) || runsQuery.isError;
  const providerLoading = healthQuery.isPending || healthQuery.isFetching;
  const serviceReady = healthQuery.isSuccess && healthQuery.data.configured;
  const showToolbarRerun = Boolean(currentSnapshot && selectedRun && isAgentRunCompatible(selectedRun, currentSnapshot) && !startingAgents && !compatibleActiveRun && selectedRun.status === 'completed');

  return <section className="agent-workspace workspace-flow" aria-labelledby="agent-workspace-title">
    <header className="agent-workspace-header workspace-flow-header"><div><div className="agent-title-line"><h2 id="agent-workspace-title">Response plan</h2></div><p>{currentSnapshot ? `${currentSnapshot.portfolio.name} · ${currentSnapshot.storm.name} · ${currentSnapshot.bulletin.label}` : 'Prepare a current risk snapshot to review operational tasks.'}</p></div><div className="workspace-flow-header-tools"><ProviderBadge health={healthQuery.data} loading={providerLoading} unavailable={unavailable} />{showToolbarRerun && <button className="agent-button agent-button-secondary" type="button" onClick={onStartAgents} disabled={!serviceReady || startingAgents || runsLoading}>{selectedRun?.status === 'failed' ? <RotateCcw size={14} /> : <RefreshCw size={14} />}{selectedRun?.status === 'failed' ? 'Retry run' : 'Run again'}</button>}<RunHistoryPicker runs={runs} selectedRun={selectedRun} selectedRunId={selectedRunId} onSelect={setSelectedRunId} snapshot={currentSnapshot} loading={runsLoading} /><button className="agent-icon-button" type="button" title="Refresh agent service" aria-label="Refresh agent service" onClick={retryService} disabled={healthQuery.isFetching || runsQuery.isFetching}><RefreshCw size={15} className={healthQuery.isFetching || runsQuery.isFetching ? 'agent-spin' : undefined} /></button></div></header>
    <ServiceAlert healthQuery={healthQuery} runsQuery={runsQuery} onRetry={retryService} />
    {localError && <div className="agent-inline-error" role="alert"><AlertCircle size={14} /><span>{localError}</span><button type="button" aria-label="Dismiss error" onClick={() => setLocalError('')}>Dismiss</button></div>}
    <FlowOverview run={selectedRun} startingAgents={startingAgents} />
    <NextActionPanel currentSnapshot={currentSnapshot} preparation={preparation} run={selectedRun} compatible={Boolean(selectedRun && isAgentRunCompatible(selectedRun, currentSnapshot))} serviceReady={serviceReady} startingAgents={startingAgents} startError={startError} onStartAgents={onStartAgents} onOpenStorm={onOpenStorm} onRetry={() => retryMutation.mutate()} />
    {!currentSnapshot && <p className="agent-history-mode-note">Historical runs are read-only until a current risk snapshot is available.</p>}
    {runsLoading && !unavailable && <div className="agent-loading workspace-flow-loading"><LoaderCircle size={18} className="agent-spin" /><span>{healthQuery.isPending ? 'Checking agent service…' : 'Loading agent history…'}</span></div>}
    {(!runsLoading || unavailable) && (selectedRun ? <RunDetail run={selectedRun} snapshot={currentSnapshot} onDecision={(proposal, decision) => decisionMutation.mutate({ proposal, decision })} decisionBusy={decisionMutation.isPending} onOpenStorm={onOpenStorm} onOpenPayouts={onOpenPayouts} /> : <div className="agent-no-run workspace-flow-no-run"><Sparkles size={21} /><h3>{currentSnapshot ? 'Start the three-stage pipeline' : 'No current risk snapshot'}</h3><p>{currentSnapshot ? 'The current risk result is ready. Run agents to extract intelligence, draft operations tasks and prepare the review gate.' : 'Historical runs remain available to inspect once they appear in the run history.'}</p></div>)}
  </section>;
}

export default AgentWorkspace;
