import { useState } from 'react';
import { Pill, SectionHeading, dateTime, money } from '../ui';
import planA from './planA.json';
import './pricing.css';

type Location = (typeof planA.runs)[number]['locations'][number];

const php = (value: number) => money(Math.round(value) * 100);
const percent = (value: number | null, digits = 1) => value === null ? '—' : `${(value * 100).toFixed(digits)}%`;
const LIMIT_LABEL: Record<string, string> = { risk: 'Local risk', per_event_cap: 'PHP 50,000 cap', '28_days_of_collections': '28 days of collections' };

function rateCell(location: Location) {
  if (location.rate === 0) return <span className="plan-muted">0% · no trigger in 24 years</span>;
  const ci = location.rateCi;
  return <>{percent(location.rate)}{ci && <small> {percent(ci.lower)}–{percent(ci.upper)}</small>}</>;
}

export default function PlanAPricing() {
  const [factor, setFactor] = useState(planA.runs[0].conversionFactor);
  const run = planA.runs.find(r => r.conversionFactor === factor) ?? planA.runs[0];
  const locations = [...run.locations].sort((a, b) => a.cover - b.cover);
  const { original, planA: plan } = planA.comparison;
  const first = planA.tiers[0];
  const last = planA.tiers[planA.tiers.length - 1];
  return <div className="pricing-layout plan-a">
    <section className="panel pricing-form">
      <span className="eyebrow">PLAN A · CLIMADA HISTORICAL PRICING</span>
      <h2>Every merchant pays {php(planA.fixedPurePremium)} a year</h2>
      <p className="muted">Cover is sized to each location's modelled typhoon risk, so the pure premium stays fixed and the payout amount varies.</p>
      <dl className="plan-terms">
        <div><dt>Wind index</dt><dd>Highest 10-min wind within 15 km</dd></div>
        <div><dt>{first.anchor.replace(' lower bound', '')}</dt><dd>≥ {first.thresholdMps.toFixed(1)} m/s → 50% of cover</dd></div>
        <div><dt>{last.anchor.replace(' lower bound', '')}</dt><dd>≥ {last.thresholdMps.toFixed(1)} m/s → 100% of cover</dd></div>
        <div><dt>In between</dt><dd>Rises in {planA.tiers.length - 1} one-day steps</dd></div>
        <div><dt>Frequency</dt><dd>At most one payout per year</dd></div>
        <div><dt>Cover limit</dt><dd>{php(planA.perEventCap)} or 28 days of collections</dd></div>
      </dl>
      <div className="plan-factor">
        <span>1-min to 10-min wind factor</span>
        <div className="plan-segments" role="group" aria-label="Wind conversion factor">
          {planA.runs.map(r => <button key={r.conversionFactor} type="button" aria-pressed={r.conversionFactor === factor} onClick={() => setFactor(r.conversionFactor)}>{r.conversionFactor.toFixed(2)}{r.conversionFactor === planA.runs[0].conversionFactor ? ' · main' : ''}</button>)}
        </div>
        <small>{run.conversionSource}</small>
      </div>
      <div className="plan-compare">
        <div><span>{original.label}</span><strong>{php(original.medianPer20k)}</strong><small>median premium per ₱20,000 of cover</small><small>{original.withinKillLine} / {original.n} merchants within ₱1,200</small></div>
        <div><span>{plan.label}</span><strong>{php(plan.medianCover)}</strong><small>median cover bought by ₱1,200</small><small>{plan.withinKillLine} / {plan.n} merchants within ₱1,200</small></div>
      </div>
      <div className="plan-source">
        <Pill tone="blue">Real CLIMADA run</Pill><Pill tone="amber">Synthetic merchants</Pill>
        <p>{planA.engine.name} on {planA.tracks.n_events} {planA.tracks.source} storms, seasons {planA.tracks.seasons[0]}–{planA.tracks.seasons[1]}. Tracks retrieved {dateTime(planA.tracks.retrieved_at)}. Merchant names and collections are invented; coordinates are real cities.</p>
        <small>{run.modelRunId} · input {run.inputHash.slice(7, 19)} · output {run.outputHash.slice(7, 19)} · validation {run.validationStatus}</small>
      </div>
    </section>
    <section className="panel">
      <SectionHeading eyebrow="REFERENCE PORTFOLIO" title="Cover bought by a fixed premium" right={<Pill tone="neutral">{php(run.portfolioCover)} total cover</Pill>} />
      <div className="table-scroll">
        <table className="plan-table">
          <thead><tr><th>Merchant location</th><th>Payout years</th><th>Risk rate on cover</th><th>Cover</th><th>Days of income</th><th>Limited by</th><th>Pure premium</th></tr></thead>
          <tbody>{locations.map(l => <tr key={l.id}>
            <td><strong>{l.city}</strong><small>{l.region}</small></td>
            <td>{percent(l.triggerProbability, 0)}</td>
            <td>{rateCell(l)}</td>
            <td><strong>{php(l.cover)}</strong></td>
            <td>{l.coverDays}</td>
            <td>{LIMIT_LABEL[l.coverLimitedBy] ?? l.coverLimitedBy}</td>
            <td>{php(l.purePremium)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      <p className="plan-notes">Pure premium excludes expenses, capital and margin. Rates come from 24 observed seasons; a 0% rate means no payout in that period, not zero risk. The wind index is the highest modelled wind at the shop or at 8 points 15 km away, which limits misses when a compact storm core passes nearby. CLIMADA's wind field omits terrain and surface roughness, which overstates inland winds. Ranges are 90% year-block bootstrap intervals.</p>
    </section>
  </div>;
}
