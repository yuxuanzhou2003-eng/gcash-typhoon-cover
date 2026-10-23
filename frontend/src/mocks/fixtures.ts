import type { Bulletin, Merchant, Portfolio, Rule, Storm } from '../contracts';
import planA from '../pricing/planA.json';

/**
 * The fixtures deliberately describe a synthetic replay.  They are useful for
 * exercising the workbench without implying that any of the weather values are
 * historical observations.
 */
export const SYNTHETIC_SOURCE = 'Synthetic fixture for UI replay; not historical weather data';

const planACover = Object.fromEntries(planA.runs[0].locations.map((location) => [location.city, Math.round(location.cover * 100)]));
const planATiers = planA.tiers.map((tier) => ({ wind: tier.thresholdMps, fraction: tier.benefitDays / 28 }));

const coverageStart = '2026-01-01T00:00:00.000Z';
const coverageEnd = '2026-12-31T23:59:59.000Z';

const demoMerchantsOne: Merchant[] = [
  {
    id: 'merchant-tacloban-bakery',
    name: 'Tacloban Bakery',
    region: 'Tacloban, Eastern Visayas',
    lat: 11.2443,
    lng: 125.0039,
    sumInsured: planACover['Tacloban'],
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-cebu-market',
    name: 'Cebu Market Cooperative',
    region: 'Cebu City, Central Visayas',
    lat: 10.3157,
    lng: 123.8854,
    sumInsured: planACover['Cebu City'],
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-davao-logistics',
    name: 'Davao Logistics Hub',
    region: 'Davao City, Davao Region',
    lat: 7.1907,
    lng: 125.4553,
    sumInsured: planACover['Davao City'],
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-manila-pharmacy',
    name: 'Manila Community Pharmacy',
    region: 'Manila, Metro Manila',
    lat: 14.5995,
    lng: 120.9842,
    sumInsured: planACover['Manila'],
    coverageStart,
    coverageEnd,
  },
];

const demoMerchantsTwo: Merchant[] = [
  {
    id: 'merchant-iloilo-traders',
    name: 'Iloilo River Traders',
    region: 'Iloilo City, Western Visayas',
    lat: 10.7202,
    lng: 122.5621,
    sumInsured: planACover['Iloilo City'],
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-legazpi-hardware',
    name: 'Legazpi Hardware Supply',
    region: 'Legazpi, Bicol Region',
    lat: 13.1391,
    lng: 123.7438,
    sumInsured: planACover['Legazpi'],
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-cdo-wholesale',
    name: 'Cagayan de Oro Wholesale',
    region: 'Cagayan de Oro, Northern Mindanao',
    lat: 8.4542,
    lng: 124.6319,
    sumInsured: planACover['Cagayan de Oro'],
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-baguio-grocer',
    name: 'Baguio Mountain Grocer',
    region: 'Baguio, Cordillera Administrative Region',
    lat: 16.4023,
    lng: 120.596,
    sumInsured: planACover['Baguio'],
    coverageStart,
    coverageEnd,
  },
];

export const demoPortfolios: Portfolio[] = [
  {
    id: 'portfolio-core-visayas',
    name: 'Core Visayas merchants',
    version: 1,
    source: 'demo',
    createdAt: '2026-01-01T00:00:00.000Z',
    merchants: demoMerchantsOne,
  },
  {
    id: 'portfolio-island-partners',
    name: 'Island partner merchants',
    version: 1,
    source: 'demo',
    createdAt: '2026-01-01T00:00:00.000Z',
    merchants: demoMerchantsTwo,
  },
];

export const PLAN_A_SOURCE = `Plan A terms from the CLIMADA historical run (${planA.ruleVersion}); merchant cover is the PHP 1,200 solved cover at the 0.88 wind factor; demo local wind is a point estimate, not the 15 km index`;

export const demoRules: Rule[] = [
  {
    id: 'rule-plan-a',
    name: 'Plan A · 15 km wind index',
    version: planA.ruleVersion,
    tiers: planATiers,
    expenseRate: 0,
    annualEventRate: 0,
    currency: 'PHP',
    source: PLAN_A_SOURCE,
  },
  {
    id: 'rule-original-design',
    name: 'Original design (comparison)',
    version: 'climada-trigger-candidate-v0.1-baseline',
    tiers: [
      { wind: 17.2222, fraction: 1 / 3 },
      { wind: 24.7222, fraction: 2 / 3 },
      { wind: 32.7778, fraction: 1 },
    ],
    expenseRate: 0,
    annualEventRate: 0,
    currency: 'PHP',
    source: 'Original 14 / 28 / 42 day design expressed as a share of cover; fails the PHP 1,200 affordability line',
  },
];

const replayBulletins: Bulletin[] = [
  {
    id: 'rai-replay-forecast-1',
    issuedAt: '2026-01-10T00:00:00.000Z',
    label: 'Forecast 1 · synthetic watch',
    wind: 28,
    center: { lat: 11.1, lng: 124.8 },
    track: [
      { lat: 11.1, lng: 124.8 },
      { lat: 11.5, lng: 124.5 },
    ],
    phase: 'forecast',
    source: SYNTHETIC_SOURCE,
  },
  {
    id: 'rai-replay-forecast-2',
    issuedAt: '2026-01-11T00:00:00.000Z',
    label: 'Forecast 2 · synthetic watch',
    wind: 42,
    center: { lat: 11.4, lng: 124.6 },
    track: [
      { lat: 11.4, lng: 124.6 },
      { lat: 11.8, lng: 124.2 },
    ],
    phase: 'forecast',
    source: SYNTHETIC_SOURCE,
  },
  {
    id: 'rai-replay-forecast-3',
    issuedAt: '2026-01-12T00:00:00.000Z',
    label: 'Forecast 3 · synthetic watch',
    wind: 58,
    center: { lat: 11.8, lng: 124.3 },
    track: [
      { lat: 11.8, lng: 124.3 },
      { lat: 12.2, lng: 123.8 },
    ],
    phase: 'forecast',
    source: SYNTHETIC_SOURCE,
  },
  {
    id: 'rai-replay-settlement',
    issuedAt: '2026-01-15T00:00:00.000Z',
    label: 'Settlement · synthetic replay',
    wind: 58,
    center: { lat: 11.4, lng: 124.5 },
    track: [
      { lat: 11.4, lng: 124.5 },
      { lat: 11.9, lng: 124.0 },
    ],
    phase: 'settlement',
    source: SYNTHETIC_SOURCE,
  },
];

export const demoStorms: Storm[] = [
  {
    id: 'storm-rai-replay',
    name: 'Rai replay',
    description: 'A single synthetic stress path for testing the workbench; not a historical event.',
    bulletins: replayBulletins,
  },
];

export const importRules: Rule[] = [
  {
    id: 'rule-import-default',
    name: 'Plan A · imported portfolio',
    version: planA.ruleVersion,
    tiers: planATiers,
    expenseRate: 0,
    annualEventRate: 0,
    currency: 'PHP',
    source: `${PLAN_A_SOURCE}; imported sumInsured is read as the Plan A cover`,
  },
];

export const importStorms: Storm[] = [
  {
    id: 'storm-import-replay',
    name: 'Imported portfolio replay',
    description: 'A synthetic stress path attached to imported portfolios for local testing.',
    bulletins: [
      {
        id: 'import-replay-forecast-1',
        issuedAt: '2026-02-10T00:00:00.000Z',
        label: 'Forecast 1 · synthetic import',
        wind: 30,
        center: { lat: 12.0, lng: 123.0 },
        track: [{ lat: 12.0, lng: 123.0 }, { lat: 12.5, lng: 122.6 }],
        phase: 'forecast',
        source: 'Synthetic import catalog; not historical weather data',
      },
      {
        id: 'import-replay-forecast-2',
        issuedAt: '2026-02-11T00:00:00.000Z',
        label: 'Forecast 2 · synthetic import',
        wind: 42,
        center: { lat: 12.3, lng: 122.7 },
        track: [{ lat: 12.3, lng: 122.7 }, { lat: 12.8, lng: 122.2 }],
        phase: 'forecast',
        source: 'Synthetic import catalog; not historical weather data',
      },
      {
        id: 'import-replay-forecast-3',
        issuedAt: '2026-02-12T00:00:00.000Z',
        label: 'Forecast 3 · synthetic import',
        wind: 55,
        center: { lat: 12.6, lng: 122.3 },
        track: [{ lat: 12.6, lng: 122.3 }, { lat: 13.1, lng: 121.8 }],
        phase: 'forecast',
        source: 'Synthetic import catalog; not historical weather data',
      },
      {
        id: 'import-replay-settlement',
        issuedAt: '2026-02-15T00:00:00.000Z',
        label: 'Settlement · synthetic import',
        wind: 55,
        center: { lat: 12.4, lng: 122.5 },
        track: [{ lat: 12.4, lng: 122.5 }, { lat: 12.9, lng: 122.0 }],
        phase: 'settlement',
        source: 'Synthetic import catalog; not historical weather data',
      },
    ],
  },
];

export interface DemoDataset {
  portfolios: Portfolio[];
  rules: Rule[];
  storms: Storm[];
}

export const demoDataset: DemoDataset = {
  portfolios: demoPortfolios,
  rules: demoRules,
  storms: demoStorms,
};

export const importedCatalog: Pick<DemoDataset, 'rules' | 'storms'> = {
  rules: importRules,
  storms: importStorms,
};
