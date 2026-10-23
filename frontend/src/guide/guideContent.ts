// Guided-tour content, chained page by page in the order of the workflow. Each page's tour
// starts on the first visit; action steps advance when the user operates the real control,
// so finishing the tour leaves a complete demo run from analysis to simulated payment.
import type { Page } from '../contracts';

export type GuidePlacement = 'top' | 'bottom' | 'left' | 'right' | 'center';

export interface GuideStep {
  // CSS selector to spotlight. Empty (or a missing element) shows a centered card.
  selector: string;
  title: string;
  body: string;
  placement?: GuidePlacement;
  // Advance when the highlighted control is clicked (buttons) or changed (selects).
  advance?: 'click' | 'change';
}

export const GUIDE_ORDER: Page[] = ['overview', 'portfolio', 'pricing', 'storm', 'agents', 'payouts', 'evidence'];

export const PAGE_LABELS: Record<Page, string> = {
  overview: 'Overview',
  portfolio: 'Portfolio',
  pricing: 'Pricing lab',
  storm: 'Storm workspace',
  agents: 'Agent workspace',
  payouts: 'Actions & payouts',
  evidence: 'Evidence',
};

export function nextPageOf(page: Page): Page | null {
  const i = GUIDE_ORDER.indexOf(page);
  return i >= 0 && i < GUIDE_ORDER.length - 1 ? GUIDE_ORDER[i + 1] : null;
}

export function prevPageOf(page: Page): Page | null {
  const i = GUIDE_ORDER.indexOf(page);
  return i > 0 ? GUIDE_ORDER[i - 1] : null;
}

export const GUIDE_STEPS: Record<Page, GuideStep[]> = {
  overview: [
    {
      selector: '',
      title: '1 · Overview',
      body: 'This workspace runs typhoon cover for small merchants: each merchant pays a fixed premium and is paid automatically when the modelled wind near the shop crosses a threshold. We will walk the whole flow once, from loading data to a simulated payout.',
      placement: 'center',
    },
    {
      selector: "[data-guide='demo']",
      title: 'Load the demo data',
      body: 'The workspace starts empty and never loads data by itself. Click Demo data, read the window that opens, then click “Load demo dataset”. It adds two synthetic merchant portfolios and one storm replay.',
      placement: 'bottom',
      advance: 'click',
    },
    {
      selector: '.context-bar',
      title: 'Three choices drive every page',
      body: 'Portfolio is the set of merchants, Storm is the event you follow, and Product rules are the payout terms. Every map, figure and payout below uses these three selections.',
      placement: 'bottom',
    },
    {
      selector: 'select[aria-label="Product rules"]',
      title: 'Choose Plan A',
      body: 'Pick “Plan A · 15 km wind index”. The other rule is the original Signal 2 / 3 / 4 design, kept only for comparison.',
      placement: 'bottom',
      advance: 'change',
    },
    {
      selector: "[data-guide='run-analysis']",
      title: 'Run your first analysis',
      body: 'This estimates the wind at every merchant for the current storm bulletin and works out who would be paid. It takes a moment; you can keep reading while it runs.',
      placement: 'bottom',
      advance: 'click',
    },
    {
      selector: '.map-panel',
      title: 'Where the storm meets your merchants',
      body: 'Each dot is a merchant. Orange means the trigger is reached, blue means below it. Click a dot to see its modelled wind and payout.',
      placement: 'left',
    },
    {
      selector: '.impact-panel',
      title: 'What it means in money',
      body: 'Plan A cover is the most each merchant can be paid this year. Scenario payout is what this bulletin would pay if it were final. A forecast bulletin never pays by itself.',
      placement: 'left',
    },
    {
      selector: '.workflow-trail',
      title: 'Your progress',
      body: 'These four stages run from risk analysis to completed follow-up. The current stage is highlighted, and you can click a stage to jump to it.',
      placement: 'bottom',
    },
    {
      selector: '',
      title: 'Next: the merchants',
      body: 'That is the overview. Next, look at the portfolio to see which merchants are covered and for how much.',
      placement: 'center',
    },
  ],
  portfolio: [
    {
      selector: '',
      title: '2 · Portfolio',
      body: 'A portfolio is a list of merchant policies: where each shop is, how much cover it has and when the cover runs.',
      placement: 'center',
    },
    {
      selector: '.library-grid',
      title: 'Switch between portfolios',
      body: 'Each card is one portfolio. Portfolios are kept separate, so switching never mixes their analyses or payouts.',
      placement: 'bottom',
    },
    {
      selector: "[data-guide='register']",
      title: 'The policy register',
      body: 'Plan A cover comes from the Pricing lab: a fixed PHP 1,200 premium buys more cover where typhoons are rare and less where they are frequent. Click a merchant name to select it on the map.',
      placement: 'top',
    },
    {
      selector: '.add-card',
      title: 'Bring your own merchants',
      body: 'Import a JSON file with each merchant’s location, cover and dates. The file is checked before anything is added.',
      placement: 'bottom',
    },
    {
      selector: '',
      title: 'Next: where the cover comes from',
      body: 'Head to the Pricing lab to see how a fixed premium becomes a different cover for each place.',
      placement: 'center',
    },
  ],
  pricing: [
    {
      selector: '',
      title: '3 · Pricing lab',
      body: 'These numbers come from a real CLIMADA run on 413 historical typhoons from 2001 to 2024. Every merchant pays the same PHP 1,200 a year; what changes from place to place is how much cover that buys.',
      placement: 'center',
    },
    {
      selector: '.plan-terms',
      title: 'The payout terms',
      body: 'The trigger uses the highest modelled wind within 15 km of the shop. At the PAGASA Signal 4 lower bound it pays half the cover, rising step by step to the full cover at Signal 5. At most one payout per year.',
      placement: 'right',
    },
    {
      selector: '.plan-factor',
      title: 'Test the key assumption',
      body: 'CLIMADA produces 1-minute winds and the thresholds use 10-minute winds. Switch the conversion factor to see how much the covers move.',
      placement: 'right',
    },
    {
      selector: '.plan-a .table-scroll',
      title: 'Cover by location',
      body: 'Sorted from least to most cover. “Payout years” is how often the trigger was reached historically; “Limited by” shows whether risk, the PHP 50,000 cap or 28 days of income set the cover.',
      placement: 'left',
    },
    {
      selector: '',
      title: 'Next: follow a storm',
      body: 'Now see the terms work on a storm. Head to the Storm workspace.',
      placement: 'center',
    },
  ],
  storm: [
    {
      selector: '',
      title: '4 · Storm workspace',
      body: 'A storm arrives as a series of bulletins. Forecast bulletins are for warning and preparing; only the settlement bulletin can create a payout.',
      placement: 'center',
    },
    {
      selector: '.bulletin-timeline',
      title: 'The bulletin timeline',
      body: 'Three forecasts, then the settlement snapshot. The highlighted stop is the bulletin you are looking at.',
      placement: 'bottom',
    },
    {
      selector: "[data-guide='next-bulletin']",
      title: 'Move to the next forecast',
      body: 'Click Next bulletin. The map keeps the earlier analysis until you run a new one, so you always know which bulletin a number belongs to.',
      placement: 'bottom',
      advance: 'click',
    },
    {
      selector: "[data-guide='next-bulletin']",
      title: 'One more forecast',
      body: 'Click Next bulletin again to reach the last forecast.',
      placement: 'bottom',
      advance: 'click',
    },
    {
      selector: "[data-guide='next-bulletin']",
      title: 'Reach settlement',
      body: 'Click once more to move to the settlement bulletin, the snapshot payouts are based on.',
      placement: 'bottom',
      advance: 'click',
    },
    {
      selector: "[data-guide='run-analysis']",
      title: 'Analyse the settlement bulletin',
      body: 'Run the analysis on the settlement snapshot. This result is the one a payout can use.',
      placement: 'bottom',
      advance: 'click',
    },
    {
      selector: '.impact-panel',
      title: 'Who reaches the trigger',
      body: 'Each paid merchant receives a share of its cover: half at Signal 4, rising to the full cover at Signal 5.',
      placement: 'left',
    },
    {
      selector: "[data-guide='create-batch']",
      title: 'Create the payout draft',
      body: 'When the analysis finishes, this button turns on. Click it to turn the result into a payout batch. Nothing is paid yet; a person still has to approve it.',
      placement: 'top',
      advance: 'click',
    },
    {
      selector: '',
      title: 'Next: the AI agents',
      body: 'Before paying, agents can explain the result and suggest follow-up tasks. Head to the Agent workspace.',
      placement: 'center',
    },
  ],
  agents: [
    {
      selector: '',
      title: '5 · Agent workspace',
      body: 'Three AI agents read the analysis: one explains the risk, one drafts follow-up tasks, one checks the evidence. They never produce winds, payouts or approvals; those come only from the model and from people.',
      placement: 'center',
    },
    {
      selector: '.workspace-flow-next-action',
      title: 'Start the agents',
      body: 'Run agents uses the current risk result. The agents need the agent service; if it is not running, this page says so and the rest of the workflow still works.',
      placement: 'bottom',
    },
    {
      selector: '.workspace-flow-overview',
      title: 'Follow the three stages',
      body: 'Each stage shows its status and the evidence behind its findings. Drafted tasks appear below for you to accept or dismiss.',
      placement: 'top',
    },
    {
      selector: '',
      title: 'Next: approve and pay',
      body: 'Head to Actions & payouts to approve the payout batch you created.',
      placement: 'center',
    },
  ],
  payouts: [
    {
      selector: '',
      title: '6 · Actions & payouts',
      body: 'This is where money moves, always in two steps: an operator submits a batch, a different person approves it, and only then is it paid.',
      placement: 'center',
    },
    {
      selector: '.batch-card',
      title: 'The payout batch',
      body: 'One row per paid merchant. The amount is the merchant’s cover times its payout share, minus anything already paid this year.',
      placement: 'top',
    },
    {
      selector: "[data-guide='submit-batch']",
      title: 'Submit for approval',
      body: 'Click Submit for approval. The amounts are reserved against each merchant’s yearly cover.',
      placement: 'top',
      advance: 'click',
    },
    {
      selector: 'select[aria-label="Demo role"]',
      title: 'Switch to the approver',
      body: 'Choose Approver here. In the demo one person plays both roles; in production the approver would be a different, authorised person.',
      placement: 'bottom',
      advance: 'change',
    },
    {
      selector: "[data-guide='approve-batch']",
      title: 'Approve the batch',
      body: 'Check the rows and evidence, then click Approve batch.',
      placement: 'top',
      advance: 'click',
    },
    {
      selector: "[data-guide='pay-batch']",
      title: 'Simulate the payment',
      body: 'Click Simulate payment to record a simulated transfer to each merchant’s wallet. The outcome menu next to it can also simulate a failed or unknown payment, to try retry and reconciliation.',
      placement: 'top',
      advance: 'click',
    },
    {
      selector: '',
      title: 'Last stop: the evidence',
      body: 'Every step you just took was recorded. Head to Evidence to trace it.',
      placement: 'center',
    },
  ],
  evidence: [
    {
      selector: '',
      title: '7 · Evidence',
      body: 'Everything that changed money or decisions is recorded here, so anyone can check what happened, when and on which data.',
      placement: 'center',
    },
    {
      selector: '.evidence-grid > section:first-child',
      title: 'The activity ledger',
      body: 'Imports, analyses, submissions, approvals and payments, newest first. Export it as JSON for review.',
      placement: 'right',
    },
    {
      selector: '.evidence-grid > section:last-child',
      title: 'Model runs',
      body: 'Each analysis keeps its inputs, rule version and a fingerprint of the input, so a result can be traced back and reproduced.',
      placement: 'left',
    },
    {
      selector: '',
      title: 'You have run the whole flow',
      body: 'From a portfolio to a traced payout. Replay this tour any time from the ? in the top bar, or reset the workspace to start clean.',
      placement: 'center',
    },
  ],
};

// ---- localStorage gating --------------------------------------------------
// Bump the version to show the tour again to every visitor after a change.
const NS = 'climada:guide_v1_';
const WELCOME_KEY = `${NS}welcome`;
const SKIP_KEY = `${NS}skipped`;
const pageKey = (page: Page) => `${NS}page_${page}`;

function read(key: string) { try { return localStorage.getItem(key) === '1'; } catch { return false; } }
function write(key: string) { try { localStorage.setItem(key, '1'); } catch { /* The tour still works without storage. */ } }

export const guideStore = {
  welcomeSeen: () => read(WELCOME_KEY),
  markWelcomeSeen: () => write(WELCOME_KEY),
  skipped: () => read(SKIP_KEY),
  setSkipped: () => write(SKIP_KEY),
  pageSeen: (page: Page) => read(pageKey(page)),
  markPageSeen: (page: Page) => write(pageKey(page)),
  reset: () => {
    try { Object.keys(localStorage).filter(k => k.startsWith(NS)).forEach(k => localStorage.removeItem(k)); } catch { /* Nothing to clear. */ }
  },
};
