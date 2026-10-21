import type { Bulletin, Merchant, Portfolio, Rule, Storm } from '../contracts';

/**
 * The fixtures deliberately describe a synthetic replay.  They are useful for
 * exercising the workbench without implying that any of the weather values are
 * historical observations.
 */
export const SYNTHETIC_SOURCE = 'Synthetic fixture for UI replay; not historical weather data';

const coverageStart = '2026-01-01T00:00:00.000Z';
const coverageEnd = '2026-12-31T23:59:59.000Z';

const demoMerchantsOne: Merchant[] = [
  {
    id: 'merchant-tacloban-bakery',
    name: 'Tacloban Bakery',
    region: 'Tacloban, Eastern Visayas',
    lat: 11.2443,
    lng: 125.0039,
    sumInsured: 25_000_000,
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-cebu-market',
    name: 'Cebu Market Cooperative',
    region: 'Cebu City, Central Visayas',
    lat: 10.3157,
    lng: 123.8854,
    sumInsured: 40_000_000,
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-davao-logistics',
    name: 'Davao Logistics Hub',
    region: 'Davao City, Davao Region',
    lat: 7.1907,
    lng: 125.4553,
    sumInsured: 32_000_000,
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-manila-pharmacy',
    name: 'Manila Community Pharmacy',
    region: 'Manila, Metro Manila',
    lat: 14.5995,
    lng: 120.9842,
    sumInsured: 18_000_000,
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
    sumInsured: 28_000_000,
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-legazpi-hardware',
    name: 'Legazpi Hardware Supply',
    region: 'Legazpi, Bicol Region',
    lat: 13.1391,
    lng: 123.7438,
    sumInsured: 22_000_000,
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-cdo-wholesale',
    name: 'Cagayan de Oro Wholesale',
    region: 'Cagayan de Oro, Northern Mindanao',
    lat: 8.4542,
    lng: 124.6319,
    sumInsured: 36_000_000,
    coverageStart,
    coverageEnd,
  },
  {
    id: 'merchant-baguio-grocer',
    name: 'Baguio Mountain Grocer',
    region: 'Baguio, Cordillera Administrative Region',
    lat: 16.4023,
    lng: 120.596,
    sumInsured: 20_000_000,
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

export const demoRules: Rule[] = [
  {
    id: 'rule-synthetic-tiered',
    name: 'Synthetic tiered wind cover',
    version: 'demo-1.0',
    tiers: [
      { wind: 90, fraction: 0.1 },
      { wind: 120, fraction: 0.25 },
      { wind: 150, fraction: 0.5 },
    ],
    expenseRate: 0.1,
    annualEventRate: 0.08,
    currency: 'PHP',
    source: SYNTHETIC_SOURCE,
  },
  {
    id: 'rule-synthetic-balanced',
    name: 'Synthetic balanced wind cover',
    version: 'demo-1.0',
    tiers: [
      { wind: 100, fraction: 0.08 },
      { wind: 135, fraction: 0.2 },
      { wind: 165, fraction: 0.4 },
    ],
    expenseRate: 0.12,
    annualEventRate: 0.06,
    currency: 'PHP',
    source: SYNTHETIC_SOURCE,
  },
];

const replayBulletins: Bulletin[] = [
  {
    id: 'rai-replay-forecast-1',
    issuedAt: '2026-01-10T00:00:00.000Z',
    label: 'Forecast 1 · synthetic watch',
    wind: 95,
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
    wind: 130,
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
    wind: 160,
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
    wind: 160,
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
    name: 'Synthetic imported portfolio cover',
    version: 'import-1.0',
    tiers: [
      { wind: 85, fraction: 0.1 },
      { wind: 125, fraction: 0.2 },
      { wind: 155, fraction: 0.35 },
    ],
    expenseRate: 0.1,
    annualEventRate: 0.07,
    currency: 'PHP',
    source: 'Synthetic import catalog; not a final policy or historical weather source',
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
        wind: 90,
        center: { lat: 12.0, lng: 123.0 },
        track: [{ lat: 12.0, lng: 123.0 }, { lat: 12.5, lng: 122.6 }],
        phase: 'forecast',
        source: 'Synthetic import catalog; not historical weather data',
      },
      {
        id: 'import-replay-forecast-2',
        issuedAt: '2026-02-11T00:00:00.000Z',
        label: 'Forecast 2 · synthetic import',
        wind: 125,
        center: { lat: 12.3, lng: 122.7 },
        track: [{ lat: 12.3, lng: 122.7 }, { lat: 12.8, lng: 122.2 }],
        phase: 'forecast',
        source: 'Synthetic import catalog; not historical weather data',
      },
      {
        id: 'import-replay-forecast-3',
        issuedAt: '2026-02-12T00:00:00.000Z',
        label: 'Forecast 3 · synthetic import',
        wind: 155,
        center: { lat: 12.6, lng: 122.3 },
        track: [{ lat: 12.6, lng: 122.3 }, { lat: 13.1, lng: 121.8 }],
        phase: 'forecast',
        source: 'Synthetic import catalog; not historical weather data',
      },
      {
        id: 'import-replay-settlement',
        issuedAt: '2026-02-15T00:00:00.000Z',
        label: 'Settlement · synthetic import',
        wind: 155,
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
