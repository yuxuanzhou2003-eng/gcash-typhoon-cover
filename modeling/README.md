# CLIMADA numerical model package

This directory is the canonical integration of the four teammate modules. The product backend calls one public function, `run_historical_model`; the React application and AI agents do not call A, B, C or D separately.

```python
from modeling import run_historical_model

result = run_historical_model(
    snapshot,
    historical_tracks,
    hazard_config,
    rule_config,
    risk_config,
    as_of="2026-10-20T00:00:00Z",
    data_manifest=data_manifest,
)
```

The stages are:

1. `climada_input` (A) validates the frozen merchant, location and coverage snapshot.
2. `hazard_wind_b` (B) computes one event-by-location wind field. CLIMADA's native result is maximum 1-minute sustained wind at 10 m in m/s. A different trigger metric is available only through an explicitly approved conversion recorded by B.
3. `climada_trigger` (C) applies one versioned rule candidate and returns the trigger tier and modeled payout basis. Historical runs use an explicit recurring month-day coverage window.
4. `climada_d` (D) aggregates historical frequency, expected annual payout and pure-risk premium basis.

`result["stages"]` retains every detailed stage output for audit. The top-level fields expose the product-facing ModelRun: versions, hashes, location results, event-location results, risk summary and combined validation.

The fixture engine is for contract tests and demos. It produces synthetic values and is never evidence about a real storm. Install the pinned environment in `requirements-climada.txt` before using the real CLIMADA engine.

Run the package contract test from the repository root:

```sh
python -m pytest -q modeling/tests
```
