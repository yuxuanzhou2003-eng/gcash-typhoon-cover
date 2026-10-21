import { useEffect, useId, useRef } from 'react';
import { Check } from 'lucide-react';
import './workflow-trail.css';

const steps = [
  { title: 'Analyse risk', owner: 'You choose the inputs' },
  { title: 'Run agents', owner: 'AI explains and plans' },
  { title: 'Review drafts', owner: 'You decide what to accept' },
  { title: 'Complete tasks', owner: 'You carry out the checks' },
] as const;

type WorkflowStatus = 'complete' | 'current' | 'waiting';

const statusLabels: Record<WorkflowStatus, string> = {
  complete: 'Completed',
  current: 'In progress',
  waiting: 'Not started',
};

export default function WorkflowTrail({ current, complete, onSelect }: {
  current: number; complete: boolean; onSelect: (step: number) => void;
}) {
  const id = useId();
  const descriptionId = `${id}-description`;
  const listRef = useRef<HTMLOListElement>(null);
  const stepRefs = useRef<Array<HTMLLIElement | null>>([]);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;

    const revealCurrentStep = () => {
      const stepIndex = Math.max(0, Math.min(steps.length - 1, current));
      const step = stepRefs.current[stepIndex];
      if (!step) return;

      if (list.scrollWidth <= list.clientWidth) {
        list.scrollLeft = 0;
        return;
      }

      const listBounds = list.getBoundingClientRect();
      const stepBounds = step.getBoundingClientRect();
      const left = stepBounds.left - listBounds.left + list.scrollLeft;
      const right = left + stepBounds.width;
      const inset = 8;
      const visibleLeft = list.scrollLeft + inset;
      const visibleRight = list.scrollLeft + list.clientWidth - inset;

      if (left < visibleLeft) {
        list.scrollLeft = Math.max(0, left - inset);
      } else if (right > visibleRight) {
        list.scrollLeft = Math.min(list.scrollWidth - list.clientWidth, right - list.clientWidth + inset);
      }
    };

    revealCurrentStep();
    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver(revealCurrentStep);
    observer.observe(list);
    return () => observer.disconnect();
  }, [current, complete]);

  return <nav className="workflow-trail" aria-label="Risk to action workflow" aria-describedby={descriptionId}>
    <p className="workflow-sr-only" id={descriptionId}>Four stages from risk analysis to completed follow-up. Select a stage to open it.</p>
    <ol ref={listRef}>
      {steps.map((step, index) => {
        const status: WorkflowStatus = complete || index < current
          ? 'complete'
          : index === current
            ? 'current'
            : 'waiting';
        const statusLabel = statusLabels[status];
        const ownerId = `${id}-step-${index}`;

        return <li className={`workflow-step is-${status}`} key={step.title} ref={element => { stepRefs.current[index] = element; }}>
          <button
            type="button"
            className="workflow-step-button"
            onClick={() => onSelect(index)}
            aria-current={index === current ? 'step' : undefined}
            aria-label={`${index + 1}. ${step.title}, ${statusLabel}`}
            aria-describedby={ownerId}
          >
            <span className="workflow-node" aria-hidden="true">
              {status === 'complete' && <Check size={9} strokeWidth={3} />}
            </span>
            <span className="workflow-copy">
              <strong><span className="workflow-index" aria-hidden="true">{index + 1}. </span>{step.title}</strong>
              <span className="workflow-status">{statusLabel}</span>
              <span className="workflow-sr-only" id={ownerId}>{step.owner}</span>
            </span>
          </button>
        </li>;
      })}
    </ol>
  </nav>;
}
