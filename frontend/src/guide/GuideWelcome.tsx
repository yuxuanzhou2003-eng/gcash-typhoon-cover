import { CloudSun, Layers3, ShieldCheck } from 'lucide-react';

interface Props {
  onStart: () => void;
  onSkip: () => void;
}

const PHASES = [
  { tag: 'Prepare', icon: <Layers3 size={22} />, text: 'Load merchants → see how much cover a fixed PHP 1,200 premium buys in each place' },
  { tag: 'Monitor', icon: <CloudSun size={22} />, text: 'Follow a storm’s bulletins → run the risk analysis → let agents suggest follow-up' },
  { tag: 'Settle', icon: <ShieldCheck size={22} />, text: 'Create a payout from settlement evidence → approve it → simulate payment → trace it' },
];

export function GuideWelcome({ onStart, onSkip }: Props) {
  return (
    <div className="guide-layer welcome" role="dialog" aria-label="Welcome to the CLIMADA workspace">
      <div className="guide-welcome-backdrop" />
      <div className="guide-welcome-card">
        <span className="guide-welcome-kicker">Getting started</span>
        <h2>Welcome to merchant typhoon cover</h2>
        <p className="guide-welcome-lead">
          This workspace prices, monitors and pays parametric typhoon cover for small merchants. The whole flow has three phases:
        </p>
        <div className="guide-phases">
          {PHASES.map((phase, i) => (
            <div className="guide-phase" key={phase.tag}>
              <span className="guide-phase-icon">{phase.icon}</span>
              <div><strong>{phase.tag}</strong><span>{phase.text}</span></div>
              {i < PHASES.length - 1 && <span className="guide-phase-arrow">→</span>}
            </div>
          ))}
        </div>
        <p className="guide-welcome-note">
          The tour takes about five minutes. You will do each step yourself on synthetic demo data, page by page. Replay it any time from the ? in the top bar.
        </p>
        <div className="guide-welcome-actions">
          <button className="guide-skip-btn" type="button" onClick={onSkip}>Explore on my own</button>
          <button className="guide-start-btn" type="button" onClick={onStart}>Start the guided tour →</button>
        </div>
      </div>
    </div>
  );
}
