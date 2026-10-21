import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, ArrowRight, Check, ChevronRight, Clock3, ExternalLink, FileSearch, LoaderCircle, RefreshCw, ShieldCheck } from 'lucide-react';
import type { AgentFact, AgentProposal, AgentRun, AgentSnapshot } from './contracts';
import { AgentApiError, agentApi, agentRunsKey, createAgentRequestId, isAgentRunCompatible, isRiskSnapshotReady } from './api';
import './task-flow.css';

export interface AgentTasksProps {
  portfolioId: string;
  stormId: string;
  snapshot: AgentSnapshot | null;
  onOpenAgents: (runId?: string) => void;
  focusTask?: { runId: string; proposalId: string };
}

interface TaskItem {
  run: AgentRun;
  proposal: AgentProposal;
  compatible: boolean;
}

type Acknowledgements = Record<string, boolean>;

function taskKey(runId: string, proposalId: string): string {
  return `${runId}:${proposalId}`;
}

function errorMessage(error: unknown): string {
  if (isConflictError(error)) {
    return 'This task changed while you were reviewing it. Open Agent review to refresh the run before trying again.';
  }
  return error instanceof Error ? error.message : 'We could not record this task. Refresh the page and try again.';
}

function isConflictError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : '';
  return (error instanceof AgentApiError && error.status === 409) || /stale|version|conflict/i.test(message);
}

function isActiveRun(run: AgentRun): boolean {
  return run.status === 'queued' || run.status === 'running';
}

function proposalLabel(kind: AgentProposal['kind']): string {
  return kind === 'communication'
    ? 'Communication draft'
    : kind === 'funding'
      ? 'Funding readiness'
      : kind === 'settlement'
        ? 'Settlement evidence'
        : 'Review task';
}

function nextStep(kind: AgentProposal['kind']): string {
  if (kind === 'communication') return 'Prepare the communication draft and check its recipients, wording and supporting evidence. Keep it as a draft; do not send it from this task.';
  if (kind === 'funding') return 'Compare funding readiness with the recorded risk and rule facts. Confirm the amount and approval path; this task never moves money.';
  if (kind === 'settlement') return 'Check the official settlement evidence, bulletin phase and linked model facts. Use the separate payout flow for any batch review or payment approval.';
  return 'Verify the affected locations, model result and linked evidence below. If you need broader analysis, open Agent review before marking this task complete.';
}

function merchantNameFromFact(fact: AgentFact | undefined): string | undefined {
  if (!fact) return undefined;
  const firstPart = fact.value.split(';')[0]?.trim();
  return firstPart || undefined;
}

function merchantNames(task: TaskItem, snapshot: AgentSnapshot | null): string[] {
  return task.proposal.merchantIds.map((merchantId) => {
    const fromSnapshot = task.compatible ? snapshot?.portfolio.merchants.find(merchant => merchant.id === merchantId)?.name : undefined;
    const fromFact = merchantNameFromFact(task.run.facts.find(fact => fact.id === `merchant:${merchantId}`));
    return fromSnapshot || fromFact || 'Affected merchant location';
  });
}

function EvidenceFacts({ proposal, run }: { proposal: AgentProposal; run: AgentRun }) {
  const facts = proposal.evidenceIds
    .map(id => run.facts.find(fact => fact.id === id))
    .filter((fact): fact is AgentFact => Boolean(fact));
  const missingCount = proposal.evidenceIds.length - facts.length;
  const sourceLink = (source: string) => /^(https?:\/\/|mailto:)/i.test(source);

  return (
    <details className="task-flow-evidence">
      <summary>
        <FileSearch size={14} />
        <span>{proposal.evidenceIds.length ? `View ${proposal.evidenceIds.length} evidence ${proposal.evidenceIds.length === 1 ? 'fact' : 'facts'}` : 'View evidence facts'}</span>
        <ChevronRight size={14} />
      </summary>
      <div className="task-flow-evidence-list">
        {facts.length ? facts.map(fact => (
          <div className="task-flow-fact" key={fact.id}>
            <div>
              <strong>{fact.label}</strong>
              <span>{fact.value}</span>
            </div>
            <small>
              {sourceLink(fact.source) ? <a href={fact.source} target="_blank" rel="noreferrer">{fact.source}<ExternalLink size={11} /></a> : fact.source || 'Source not supplied'}
            </small>
          </div>
        )) : <p className="task-flow-muted">The linked evidence fact is not available in this run.</p>}
        {missingCount > 0 && facts.length > 0 && <p className="task-flow-muted">{missingCount} linked fact{missingCount === 1 ? '' : 's'} could not be loaded.</p>}
      </div>
    </details>
  );
}

function StatusNote({ task }: { task: TaskItem }) {
  if (task.compatible) return null;
  return (
    <div className="task-flow-stale" role="status">
      <AlertCircle size={14} />
      <div>
        <strong>Review context only</strong>
        <span>This task belongs to an older or unavailable risk snapshot. Open Agent review to create a current run before completing it.</span>
      </div>
    </div>
  );
}

function MerchantScope({ names }: { names: string[] }) {
  if (!names.length) return <p className="task-flow-scope">Portfolio-level task</p>;
  return (
    <div className="task-flow-scope">
      <span className="task-flow-scope-label">Affected locations</span>
      <div className="task-flow-location-list">
        {names.map((name, index) => <span key={`${name}-${index}`}>{name}</span>)}
      </div>
    </div>
  );
}

function TaskCard({
  task,
  snapshot,
  acknowledged,
  busy,
  onAcknowledge,
  onComplete,
  onOpenAgents,
  taskRef,
}: {
  task: TaskItem;
  snapshot: AgentSnapshot | null;
  acknowledged: boolean;
  busy: boolean;
  onAcknowledge: (key: string, value: boolean) => void;
  onComplete: (task: TaskItem) => void;
  onOpenAgents: (runId?: string) => void;
  taskRef: (element: HTMLElement | null) => void;
}) {
  const { proposal, run } = task;
  const key = taskKey(run.id, proposal.id);
  const names = merchantNames(task, snapshot);
  const done = proposal.status === 'done';
  const canComplete = task.compatible && acknowledged && !busy;

  return (
    <article
      ref={taskRef}
      className={`task-flow-card task-flow-card-${proposal.status} ${task.compatible ? '' : 'is-stale'}`}
      data-task-key={key}
      tabIndex={-1}
      aria-labelledby={`${key}-title`}
    >
      <div className="task-flow-card-topline">
        <span className="task-flow-kind">{proposalLabel(proposal.kind)}</span>
        <span className={`task-flow-status task-flow-status-${proposal.status}`}>
          {done ? <Check size={13} /> : <Clock3 size={13} />}
          {done ? 'Done' : 'Accepted'}
        </span>
      </div>
      <h3 id={`${key}-title`}>{proposal.title}</h3>
      <p className="task-flow-detail">{proposal.detail}</p>
      <MerchantScope names={names} />
      <div className="task-flow-next-step">
        <span className="task-flow-next-label">{done ? 'Recorded checks' : 'What to do'}</span>
        <p>{nextStep(proposal.kind)}</p>
      </div>
      <EvidenceFacts proposal={proposal} run={run} />
      <StatusNote task={task} />
      {!task.compatible && <button className="task-flow-secondary task-flow-stale-action" type="button" onClick={() => onOpenAgents(run.id)}><ExternalLink size={13} />Open Agent review</button>}
      {done ? (
        <div className="task-flow-terminal">
          <span><Check size={14} />Completion recorded by a human</span>
          <button className="task-flow-link" type="button" onClick={() => onOpenAgents(run.id)}>Back to Agent review <ArrowRight size={13} /></button>
        </div>
      ) : (
        <div className="task-flow-complete-area">
          <label className={`task-flow-ack ${task.compatible ? '' : 'is-disabled'}`}>
            <input
              type="checkbox"
              checked={acknowledged}
              disabled={!task.compatible || busy}
              onChange={event => onAcknowledge(key, event.target.checked)}
            />
            <span>I checked this task’s evidence and completed the listed human checks.</span>
          </label>
          <p className="task-flow-ack-note">Mark complete records your confirmation; it does not send messages or release funds.</p>
          <button className="task-flow-complete" type="button" disabled={!canComplete} onClick={() => onComplete(task)}>
            {busy ? <LoaderCircle size={14} className="task-flow-spin" /> : <Check size={14} />}
            {busy ? 'Recording completion' : 'Mark complete'}
          </button>
        </div>
      )}
    </article>
  );
}

export function AgentTasks({ portfolioId, stormId, snapshot, onOpenAgents, focusTask }: AgentTasksProps) {
  const queryClient = useQueryClient();
  const [acknowledged, setAcknowledged] = useState<Acknowledgements>({});
  const [localError, setLocalError] = useState('');
  const taskRefs = useRef<Record<string, HTMLElement | null>>({});
  const focusedTaskKey = useRef('');
  const currentSnapshot = isRiskSnapshotReady(snapshot) ? snapshot : null;
  const previousSnapshotId = useRef('');
  const runsKey = agentRunsKey(portfolioId, stormId);
  const runsQuery = useQuery({
    queryKey: runsKey,
    queryFn: () => agentApi.listRuns(portfolioId, stormId),
    enabled: Boolean(portfolioId && stormId),
    retry: false,
    refetchInterval: query => {
      const runs = query.state.data as AgentRun[] | undefined;
      return runs?.some(isActiveRun) ? 1000 : 4000;
    },
  });

  const tasks = useMemo<TaskItem[]>(() => {
    const next = (runsQuery.data || [])
      .filter(run => run.portfolioId === portfolioId && run.stormId === stormId && run.status === 'completed')
      .flatMap(run => run.proposals
        .filter(proposal => proposal.status === 'accepted' || proposal.status === 'done')
        .map(proposal => ({ run, proposal, compatible: Boolean(currentSnapshot && isAgentRunCompatible(run, currentSnapshot)) })));
    return next.sort((a, b) => {
      const statusRank = (item: TaskItem) => item.proposal.status === 'accepted' ? 0 : 1;
      const statusDifference = statusRank(a) - statusRank(b);
      if (statusDifference !== 0) return statusDifference;
      if (a.compatible !== b.compatible) return a.compatible ? -1 : 1;
      return new Date(b.run.updatedAt).getTime() - new Date(a.run.updatedAt).getTime();
    });
  }, [currentSnapshot, portfolioId, runsQuery.data, stormId]);

  useEffect(() => {
    const snapshotId = currentSnapshot?.modelRun.id || '';
    if (previousSnapshotId.current && previousSnapshotId.current !== snapshotId) setAcknowledged({});
    previousSnapshotId.current = snapshotId;
  }, [currentSnapshot?.modelRun.id]);

  useEffect(() => {
    if (!focusTask) return;
    const key = taskKey(focusTask.runId, focusTask.proposalId);
    const element = taskRefs.current[key];
    if (!element || focusedTaskKey.current === key) return;
    focusedTaskKey.current = key;
    element.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
    element.focus({ preventScroll: true });
  }, [focusTask, tasks]);

  const completionMutation = useMutation({
    mutationFn: (task: TaskItem) => {
      if (!task.compatible || !currentSnapshot) {
        throw new AgentApiError('This task is out of date. Open Agent review before recording completion.', 'http', 409);
      }
      return agentApi.decideProposal(task.run.id, task.proposal.id, {
        requestId: createAgentRequestId(),
        expectedVersion: task.run.version,
        decision: 'complete',
        inputHash: task.run.inputHash,
      });
    },
    onSuccess: (updatedRun, task) => {
      setLocalError('');
      setAcknowledged(previous => {
        const next = { ...previous };
        delete next[taskKey(task.run.id, task.proposal.id)];
        return next;
      });
      // Seed the shared cache with the authoritative response before polling again.
      queryClient.setQueryData<AgentRun[]>(runsKey, previous => {
        if (!previous) return [updatedRun];
        return previous.map(run => run.id === updatedRun.id ? updatedRun : run);
      });
      void queryClient.invalidateQueries({ queryKey: runsKey });
    },
    onError: error => {
      setLocalError(errorMessage(error));
      if (isConflictError(error)) {
        setAcknowledged({});
        void queryClient.invalidateQueries({ queryKey: runsKey });
      }
    },
  });

  const acceptedCount = tasks.filter(task => task.proposal.status === 'accepted').length;
  const latestRunId = tasks[0]?.run.id;

  if (portfolioId && stormId && runsQuery.isPending && !runsQuery.data) {
    return <section className="task-flow" aria-labelledby="task-flow-title" aria-live="polite"><div className="task-flow-loading"><LoaderCircle size={17} className="task-flow-spin" /><span>Loading your accepted tasks…</span></div></section>;
  }

  if (runsQuery.isError) {
    return (
      <section className="task-flow" aria-labelledby="task-flow-title">
        <div className="task-flow-heading"><div><h2 id="task-flow-title">Your accepted tasks</h2></div><button className="task-flow-link" type="button" onClick={() => onOpenAgents(latestRunId)}>Open Agent review <ExternalLink size={13} /></button></div>
        <div className="task-flow-error" role="alert"><AlertCircle size={16} /><div><strong>We could not load accepted tasks.</strong><span>{errorMessage(runsQuery.error)}</span></div><button className="task-flow-secondary" type="button" onClick={() => void runsQuery.refetch()} disabled={runsQuery.isFetching}>{runsQuery.isFetching ? <LoaderCircle size={14} className="task-flow-spin" /> : <RefreshCw size={14} />}Retry</button></div>
      </section>
    );
  }

  return (
    <section className="task-flow" aria-labelledby="task-flow-title">
      <div className="task-flow-heading">
        <div>
          <h2 id="task-flow-title">Your accepted tasks</h2>
          <p>Review the evidence, complete the checks, then mark the task complete.</p>
        </div>
        <button className="task-flow-link" type="button" onClick={() => onOpenAgents(latestRunId)}>Open Agent review <ExternalLink size={13} /></button>
      </div>
      {localError && <div className="task-flow-inline-error" role="alert"><AlertCircle size={14} /><span>{localError}</span><button type="button" onClick={() => setLocalError('')}>Dismiss</button></div>}
      {!tasks.length ? (
        <div className="task-flow-empty">
          <span className="task-flow-empty-icon"><ShieldCheck size={22} /></span>
          <h3>No accepted tasks yet</h3>
          <p>Review the latest evidence-backed drafts in Agent review, then accept a task when you are ready to carry it through.</p>
          <button className="task-flow-secondary" type="button" onClick={() => onOpenAgents()}><ExternalLink size={14} />Open Agent review</button>
        </div>
      ) : (
        <>
          <p className="task-flow-count">{acceptedCount ? `${acceptedCount} task${acceptedCount === 1 ? '' : 's'} ready for your review` : 'Completed task history'}</p>
          <div className="task-flow-list">
            {tasks.map(task => {
              const key = taskKey(task.run.id, task.proposal.id);
              const taskBusy = completionMutation.isPending && completionMutation.variables ? taskKey(completionMutation.variables.run.id, completionMutation.variables.proposal.id) === key : false;
              return <TaskCard key={key} task={task} snapshot={snapshot} acknowledged={Boolean(acknowledged[key])} busy={taskBusy} onAcknowledge={(ackKey, value) => setAcknowledged(previous => ({ ...previous, [ackKey]: value }))} onComplete={taskToComplete => completionMutation.mutate(taskToComplete)} onOpenAgents={onOpenAgents} taskRef={element => { taskRefs.current[key] = element; }} />;
            })}
          </div>
        </>
      )}
    </section>
  );
}

export default AgentTasks;
