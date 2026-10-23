import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import type { GuidePlacement, GuideStep } from './guideContent';

export type GuideExit = 'next' | 'prev' | 'done' | 'skip';

interface Props {
  steps: GuideStep[];
  // Label of the next page in the chain; null means the last step closes the tour.
  nextLabel: string | null;
  // Label of the previous page, offered as "Back" on the first step.
  prevLabel?: string | null;
  // A modal dialog sits above the tour; hide until it closes, keeping the current step.
  paused?: boolean;
  onClose: (dir: GuideExit) => void;
}

const PAD = 8;
const TOOLTIP_W = 340;
const TOOLTIP_H = 190;
const POLL_MS = 400;

export function GuideTour({ steps, nextLabel, prevLabel = null, paused = false, onClose }: Props) {
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const scrolledFor = useRef(-1);

  const current = steps[step];
  const isLast = step === steps.length - 1;

  // Targets can appear after an async command (an approval button after submit), so the
  // tour keeps looking and scrolls to a target once, the first time it is found.
  const measure = useCallback(() => {
    const target = current?.selector ? document.querySelector(current.selector) : null;
    if (!target) { setRect(null); return; }
    if (scrolledFor.current !== step) {
      scrolledFor.current = step;
      target.scrollIntoView({ block: 'center', behavior: 'auto' });
    }
    requestAnimationFrame(() => setRect(target.getBoundingClientRect()));
  }, [current, step]);

  useLayoutEffect(() => { if (!paused) measure(); }, [measure, paused]);

  useEffect(() => {
    if (paused) return;
    const handler = () => measure();
    const poll = window.setInterval(handler, POLL_MS);
    window.addEventListener('resize', handler);
    window.addEventListener('scroll', handler, true);
    return () => {
      window.clearInterval(poll);
      window.removeEventListener('resize', handler);
      window.removeEventListener('scroll', handler, true);
    };
  }, [measure, paused]);

  // Action steps advance when the user operates the highlighted control. Disabled
  // buttons fire no click, which keeps the order honest; selects advance on change.
  useEffect(() => {
    if (!current?.advance || !current.selector) return;
    const selector = current.selector;
    const onEvent = (event: Event) => {
      if (event.target instanceof Element && event.target.closest(selector)) setStep(s => Math.min(s + 1, steps.length - 1));
    };
    document.addEventListener(current.advance, onEvent, true);
    return () => document.removeEventListener(current.advance!, onEvent, true);
  }, [current, steps.length]);

  useEffect(() => {
    if (paused) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose('skip');
      else if (e.key === 'ArrowRight' && !current?.advance) advance();
      else if (e.key === 'ArrowLeft') back();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, isLast, nextLabel, prevLabel, paused]);

  if (!current || paused) return null;

  function advance() {
    if (isLast) onClose(nextLabel ? 'next' : 'done');
    else setStep(s => s + 1);
  }
  function back() {
    if (step > 0) setStep(s => s - 1);
    else if (prevLabel) onClose('prev');
  }

  const centered = !rect;
  const placement = current.placement ?? 'bottom';
  const tooltipStyle: CSSProperties = centered
    ? { top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: TOOLTIP_W }
    : positionTooltip(rect as DOMRect, placement);
  const finalLabel = isLast ? (nextLabel ? `Next: ${nextLabel} →` : 'Done ✓') : 'Next';
  const waiting = current.advance && current.selector && !rect;

  return (
    <div className="guide-layer" role="dialog" aria-label="Guided tour">
      <svg className="guide-mask" aria-hidden="true">
        <defs>
          <mask id="guide-hole">
            <rect x="0" y="0" width="100%" height="100%" fill="white" />
            {rect && <rect x={rect.left - PAD} y={rect.top - PAD} width={rect.width + PAD * 2} height={rect.height + PAD * 2} rx="14" fill="black" />}
          </mask>
        </defs>
        <rect x="0" y="0" width="100%" height="100%" fill="rgba(20,26,38,0.55)" mask="url(#guide-hole)" />
      </svg>

      {rect && <div className="guide-ring" style={{ top: rect.top - PAD, left: rect.left - PAD, width: rect.width + PAD * 2, height: rect.height + PAD * 2 }} />}

      <div className={`guide-tip${centered ? ' centered' : ''}`} style={tooltipStyle}>
        <button className="guide-skip" type="button" onClick={() => onClose('skip')}>Skip tour</button>
        <span className="guide-count">{step + 1} / {steps.length}</span>
        <h3>{current.title}</h3>
        <p>{current.body}</p>
        {current.advance && !waiting && <p className="guide-hint">{current.advance === 'change' ? '↑ Make the highlighted choice to continue.' : '↑ Click the highlighted control to continue.'}</p>}
        {waiting && <p className="guide-hint">Waiting for this control to appear…</p>}

        <div className="guide-foot">
          <div className="guide-dots" aria-hidden="true">
            {steps.map((_, i) => <span key={i} className={i === step ? 'on' : i < step ? 'done' : ''} />)}
          </div>
          <div className="guide-btns">
            {(step > 0 || prevLabel) && <button className="guide-prev" type="button" onClick={back}>{step > 0 ? 'Back' : `← ${prevLabel}`}</button>}
            {current.advance
              ? <button className="guide-pass" type="button" onClick={advance}>Skip step</button>
              : <button className="guide-next" type="button" onClick={advance}>{finalLabel}</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

function positionTooltip(rect: DOMRect, preferred: GuideStep['placement']): CSSProperties {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const width = Math.min(TOOLTIP_W, vw - 24);
  const gap = PAD + 10;
  let pos: GuidePlacement | 'inside' = preferred ?? 'bottom';

  if (pos === 'center') return { top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width };
  if (pos === 'bottom' && rect.bottom + gap + TOOLTIP_H > vh) pos = 'top';
  if (pos === 'top' && rect.top - gap - TOOLTIP_H < 0) pos = rect.bottom + gap + TOOLTIP_H <= vh ? 'bottom' : 'inside';
  if (pos === 'right' && rect.right + gap + width > vw) pos = 'left';
  if (pos === 'left' && rect.left - gap - width < 0) pos = 'bottom';

  const clampLeft = Math.min(Math.max(12, rect.left + rect.width / 2 - width / 2), vw - width - 12);
  const clampTop = Math.min(Math.max(12, rect.top + rect.height / 2 - TOOLTIP_H / 2), vh - TOOLTIP_H - 12);

  switch (pos) {
    case 'top': return { top: Math.max(12, rect.top - gap - TOOLTIP_H), left: clampLeft, width };
    case 'right': return { top: clampTop, left: rect.right + gap, width };
    case 'left': return { top: clampTop, left: Math.max(12, rect.left - gap - width), width };
    // A target taller than the viewport: keep the card on screen, over the target.
    case 'inside': return { bottom: 16, left: clampLeft, width };
    case 'bottom':
    default: return { top: rect.bottom + gap, left: clampLeft, width };
  }
}
