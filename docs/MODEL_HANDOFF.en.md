# CLIMADA Modeling Handoff Document — English Discussion Draft

Start here: [Project overview and team responsibilities](README.md).

## 1. Confirmed Boundaries and Division of Responsibilities

This handoff uses a single-platform architecture: the platform imports merchants and existing policies/coverage records; human operations staff calculate rates and risk in the back office. No merchant purchase frontend is built, and operational rate calculations do not directly issue policies. The Modeling team is responsible only for CLIMADA wind fields, historical risk, storm scenarios, and model validation, delivering reproducible pure-Python computational boundaries and result files. The user is responsible for the UI, backend rules, data orchestration, and agent integration.

The model layer does not create orders, decide final policies, send quotes to merchants, or execute payouts. Final policy payouts, coverage limits, eligibility, and effective-date rules are handled by the backend. The Modeling team calculates historical expected payouts and the pure risk premium basis using the shared payout rules; the backend handles fees, capital loadings, and pricing constraints based on that basis, and independently performs final settlement for in-force policies. The model and backend use the same payout-rule version; they must not add fees or apply limits twice. The proposal memorandum's content about GCash merchants, typhoon parametric risk, IBTrACS/CLIMADA/LitPop, and so on is background; where it conflicts with this handoff agreement, this agreement controls.

## 2. Data Contract

All inputs must include `as_of` (calculation cut-off), data version, and units. ISO 8601 time is recommended; use UTC internally, with Philippine time (PHT, UTC+8) optionally added for display. Coordinates are WGS84 decimal degrees; wind speed is in m/s, but “sustained wind/gust and averaging duration” remain pending confirmation; amounts are in PHP, while the minimum currency unit and rounding rules are pending confirmation; frequency is events/year, and probabilities use `[0,1]`.

| Input object | Required fields | Optional / notes |
|---|---|---|
| Model exposure input | `location_id`, `longitude`, `latitude`; historical expected payout calculation additionally requires a coverage window and coverage amount or unit coverage amount basis | Merchant-policy association is stored by the backend; when an existing coverage amount is available, daily average collections are not required; sensitive details do not enter model logs |
| Historical event/track | `event_id`, `observation_time`, track latitude/longitude, wind-speed metric, source, `published_at` | Wind radii/impact area, quality flags, observation or reanalysis label; every row must be traceable to a data version |
| Storm bulletin snapshot | `storm_id`, `bulletin_id`, `published_at`, path/intensity visible at that time | Forecast-cone geometry, hash of the bulletin source, retrieval time, parser version; a missing forecast cone need not block all calculations, but the limitation must be stated; a forecast cone cannot directly provide merchant trigger probabilities |
| Model configuration | CLIMADA/code version, grid or administrative-area version, threshold, coverage window, scenario seed | Impact function, frequency definition, correlation method, payout curve; undetermined parameters must be marked `pending confirmation` |

The main output fields are as follows. Missing or conflicting results must not be silently filled; return `status`, `reason_code`, the number of affected records, and record identifiers, and retain coverage.

| Output object | Required results | Use / boundary |
|---|---|---|
| Historical risk | `event_frequency_per_year`, `conditional_trigger_rate`, threshold exceedance distribution, `expected_payout_basis`, sample period, coverage | Provides the expected payout basis; it does not equal the final premium, policy coverage amount, or payout decision |
| Storm scenarios | `scenario_id`, shared wind-field/track version, peak wind speed at each exposure point, trigger indicators, scenario-weight status | Compute a weighted expected value only when external weights are available; without weights, it must be marked `unweighted` |
| Validation report | Metrics, observation period, model period, grouped results, basis risk, future-data checks, `pass/warn/fail` | For use by the backend and project acceptance; when thresholds are not confirmed, do not automatically claim pass |

Suggested missing-reason codes: `missing_coordinate`, `missing_hazard`, `unmapped_exposure`, `coverage_gap`, `version_conflict`, `future_data_rejected`, `invalid_unit`. If a merchant cannot be located or a key wind speed/timestamp is missing, return a non-computable result and do not output an estimate that looks precise.

## 3. Pure-Python Boundary (Proposed Interfaces, Not Yet Implemented)

The three functions organize a deterministic computational boundary. They do not implicitly connect to the network, call an LLM, or modify the business database. Callers pass frozen data; large-file I/O and model caching are managed through explicit adapters, which may use the file capabilities required by CLIMADA. With fixed inputs, configuration, and random seed, outputs and ordering should be stable; floating-point results use the agreed tolerance.

```python
def compute_historical_risk(
    exposure: list[dict],
    historical_events: list[dict],
    model_config: dict,
    *,
    as_of: str,
    data_manifest: dict,
) -> dict:
    """Returns historical frequency, threshold distribution, and expected_payout_basis."""


def compute_storm_scenarios(
    bulletin_snapshot: dict,
    exposure: list[dict],
    scenario_config: dict,
    *,
    as_of: str,
    data_manifest: dict,
) -> dict:
    """Generates scenarios and trigger indicators from a single bulletin snapshot or historical track."""


def validate_model(
    model_outputs: dict,
    observations: list[dict],
    validation_config: dict,
    *,
    as_of: str,
    data_manifest: dict,
) -> dict:
    """Returns traceable metrics, leakage checks, and pass/warn/fail."""
```

`compute_historical_risk` must record “frequency” and “probability” separately: frequency is the expected number of events per year (for example, `lambda`), while probability is the chance of a trigger given an event or given a coverage window. Annual probability may be derived only after the frequency model, observation window, and aggregation method have been confirmed. A `compute_storm_scenarios` scenario set has no inherent probability; without calibrated scenario weights, probability must not be derived from scenario count, forecast-cone area, or model random seed.

Impacts of the same storm on multiple merchants use the same track and spatial wind field, retain spatial correlation, and cannot be independently sampled per merchant. Whether a storm occurs/how many occur (frequency) and how losses at each location change conditional on occurrence (within-event correlation) are modeled separately; they must not be mixed into one “probability” field. A forecast cone may have coverage meaning defined by the publishing agency, but it cannot be directly equated to a complete probability distribution for local wind speed or payout. Scenario weights should state the construction method, source, calibration period, and version.

## 4. Timing, Versioning, and Future-Information Isolation

Each run generates a manifest containing at least the dataset name, publication/version number, file hash, validity period, `as_of`, code commit ID, environment dependencies, configuration version, random seed, and run ID. Historical validation should replay bulletins one by one: at a given time point, read only bulletins with `published_at <= as_of` and merchant/policy data that already existed then; do not use the final track, post-event measured intensity, later bulletins, or fields revised after the fact. Save the forecast cone as the geometry published at that time; do not backfill ex-post probabilities.

Each replay snapshot outputs `input_hash`, `output_hash`, and `future_data_check`. If a timestamp later than the cut-off is found, return `future_data_rejected` immediately and list the rejected records in the validation report. Freeze, retention, or trigger actions after a named storm are determined by backend rules; the model only provides the risk results computable at that point.

## 5. Testing and Acceptance

The minimum test set includes: field and unit validation, coordinate bounds, repeat-run consistency under a fixed seed, ensuring that the trigger set does not expand when the threshold increases, rejecting calculation and returning a reason code when key fields are missing, a shared wind field across merchants for the same storm, no probability output without scenario weights, rejection of future bulletins during replay, no implicit network access or modification of the business database, traceability of file and cache operations, and reflection of version changes in the manifest. Use small fixed fixtures covering normal, empty-set, duplicate-event, cross-time-zone, version-conflict, and outside-coverage-period records.

Acceptance deliverables must include: field descriptions, error codes, input/output examples for the three functions, a locked data/code manifest, unit tests, and one replayable report. The memorandum proposed stop conditions such as “basis risk exceeds one-half, annual premium for a region exceeds PHP 1,200, and model frequency differs from observations by more than 2x”; these are discussion-only candidate thresholds, currently all marked `pending confirmation`, and must not be hard-coded into tests or backend rules.

## 6. Phased Delivery

1. **Phase 0: Contract and skeleton.** Fix fields, units, time conventions, error codes, the manifest, and test fixtures; complete empty implementations/examples for the three interfaces.
2. **Phase 1: Historical risk.** Connect frozen historical tracks and exposure examples, output frequency, threshold distribution, and `expected_payout_basis`, and complete the missing-data report.
3. **Phase 2: Storm scenarios.** Support a single bulletin snapshot, historical-track perturbations, and a shared wind field; without weights, deliver an unweighted scenario set only.
4. **Phase 3: Validation and replay.** Replay designated historical events bulletin by bulletin, complete basis-risk, model-skill, coverage, and future-leakage checks, and produce an auditable report.
5. **Phase 4: Backend handoff.** Deliver a versioned result package and call instructions; the user integrates the UI, shared payout rules in the backend, and the agent; the modeling team does not expand into policy issuance, final payouts, or limit decisions.

Pending parameters are registered centrally and not hidden in code: wind-speed definition (sustained wind/gust), grid/administrative-area granularity, trigger thresholds and tiers, coverage window, payout curve, event catalogue and frequency estimation period, scenario weights, correlation method, PHP rounding, shared payout-rule version, and acceptance thresholds.
