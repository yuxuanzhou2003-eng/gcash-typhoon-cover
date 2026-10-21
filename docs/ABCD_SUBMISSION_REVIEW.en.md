# CLIMADA Roles A, B, C and D Submission Review

Review date: 21 October 2026

## Remediation Status (completed after the review)

The findings below are retained as the review record for the originally submitted files. The integration lead has since corrected the code-level blockers and promoted the repaired modules into the canonical `modeling/` package.

- **A:** required data/rule versions and manifest boundaries, safe timestamp failures, daily-collection basis passthrough, and stronger hashes are implemented. Current result: 14 tests passed.
- **B:** the teammate's newer v0.3 package is now the base. Native CLIMADA wind is explicitly identified as `sustained_1min_10m` in m/s; conversions require status, factor, source and target metric; fixture metrics are explicit; dropped events, outside-circulation/model-radius statuses and provenance hashes are structured. Current result: 35 tests discovered, 33 passed and two real-CLIMADA tests skipped because CLIMADA is not installed locally.
- **C:** failed/future B runs and duplicate wind rows are non-computable; upstream provenance affects hashes; recurring historical coverage windows are explicit; approved B conversions are consumed without re-labeling values inside C; and the D-compatible payout table includes candidate and rule-config identity. Current result: 42 tests passed and one optional external-module test skipped.
- **D:** future rejection, fatal-result suppression, cross-year policy windows, version requirements, partial-portfolio warnings and the new B dropped-event contract are implemented. Current result: 92 tests and 629 subtests passed.
- **Integrated boundary:** `modeling.run_historical_model` now executes current A → B → C → D and returns one ModelRun-shaped result with stage hashes and full audit outputs. Its deterministic fixture contract passes. The numerical fixture path is accepted for product integration; a real hazard acceptance run still requires the pinned CLIMADA environment.

The original sensitivity artifacts were restored after a missing-CLIMADA attempt produced invalid zero outputs. The generator now stops before replacing artifacts when B fails and asks B to publish each approved sensitivity conversion inside its own hashed manifest.

## 1. Overall Decision

The four submissions are substantial and can continue as prototype components, but the current versions are **not accepted as an integrated A to B to C to D model package**.

| Role | Decision | Main reason |
|---|---|---|
| A — Company materials and input adapter | Conditional go for the synthetic prototype | The adapter is deterministic and well structured, but version enforcement, malformed timestamps, manifest completeness, the daily-collections field, and the track handoff to B require correction. |
| B — CLIMADA hazard and wind field | Go for fixture contract tests; no-go for a real hazard run | The fixture tests pass, but the real CLIMADA path has not been independently executed and its wind metric is mislabeled for non-one-minute input tracks. Its historical wind rows do not carry the metric C requires. |
| C — Trigger and modeled payout basis | Conditional go as a standalone experimental calculator; no-go for integration | The arithmetic, boundary behavior, and experimental labeling are strong. Current A and B cannot reproduce its sensitivity run, and C does not produce D's required payout-table contract. |
| D — Historical risk and pure-risk pricing | Conditional go for code-level handoff | The numerical core is strong, but future-data propagation, fatal validation behavior, wrapping coverage windows, and required provenance fields require correction. |

The immediate blocker is now the set of boundaries between the modules. C has been delivered, but current B rows fail C's wind-metric check, current A does not supply C's daily-collection basis, and C's result cannot be passed directly to D.

## 2. Verification Performed

- Role A: 9 of 9 tests passed.
- Role B: 19 of 19 tests passed.
- Role C: 33 of 33 tests passed when the submitted B module was added to its Python path. Without that path, 32 tests pass and the optional integration test is skipped.
- Role D: 88 of 88 tests passed.
- Role C's sensitivity exports contain 36 unique scenarios across four candidates, two coverage windows and three one-minute-to-ten-minute factors. No negative payout occurs, no modeled payout exceeds its gross benefit, the per-storm totals reconcile to the all-scenario totals, and the outer CSV files are byte-identical to the copies inside the code archive.
- Role C's submitted sensitivity script does not run against current A 0.2.0. It stops with 0 accepted and 1,200 rejected records because the synthetic locations omit `location_precision`, which current A requires.
- A direct current-B-to-C probe returned `wind_metric_mismatch`. B's `event_location_winds[]` rows do not contain `wind_metric`; C's candidate requires `sustained_10min_10m`.
- Passing C's result directly to D produced a fatal validation result: C returns `results[]`, while D requires `payout_table.meta` and `payout_table.rows[]` with different field names.
- A duplicate B row probe produced PHP 14,000 or PHP 42,000 depending only on row order. C flags the duplicate but still selects the first row and emits a payout.
- A B result marked `validation.status = fail` still produced a computable C payout. A B manifest marked `future_data_check = rejected` also still produced a computable C payout.
- Changing only A's and B's upstream hashes did not change C's `manifest.output_hash`, even though the recorded provenance changed.
- The supplied Word documents were rendered and visually inspected. They are readable and clearly label the candidates as experimental. `00_READ_ME_FIRST.docx` has an almost empty third page, and the recommendation document has an avoidable mostly blank final page.

The pre-generated C sensitivity results cannot be independently rerun in the current environment as submitted: the script fails first at the current A schema boundary, and the environment also lacks the CLIMADA dependency required for the real hazard step.

## 3. Required Fixes Before Integration Acceptance

### 3.1 Role A

1. Make `data_manifest`, `data_version`, and `rule_version` required. Missing values must produce a structured failure instead of bypassing version checks (`climada_input/adapter.py`, lines 85–92 and 166–168).
2. Convert malformed `recorded_at` values into a structured rejection. They currently raise an unhandled exception (`climada_input/adapter.py`, lines 49–55 and 94–100).
3. Complete the run manifest with `as_of`, validity period, code commit, dependency versions, configuration version, run ID, and any seed used. Include the manifest and `as_of` in the input and output fingerprints.
4. Correct the PAGASA source chronology. The submitted manifest records the bulletin as published at 11:00 PHT but retrieved at 09:00 PHT on the same date.
5. Normalize the track handoff for B. A emits `kt`, `averaging_period_min`, and `published_date`; B expects `kn`, `wind_averaging_minutes`, and `published_at`.
6. Add `daily_collection_basis_php` with its observation-window definition, or supply a separately versioned and traceable collections input that the orchestrator joins before C. Current A only supplies `coverage_amount_php`.
7. Replace or regenerate `fixtures/sample_exposure_input.json`, which still identifies the 0.1.0 schema while the submitted adapter is 0.2.0.
8. Decide how regional summaries work when PSGC is missing. In the current sample, 48 of 50 accepted locations become `unassigned` in D because their PSGC code is absent.

### 3.2 Role B

1. Fix the wind definition. CLIMADA's `TropCyclone.from_tracks` output is maximum one-minute sustained wind at 10 metres in m/s. The module currently labels the result using the input track's averaging period (`hazard_wind_b.py`, lines 646–680 and 847–860).
2. Add `wind_metric`, `wind_unit`, averaging period, height, original source metric, and applied conversion to every `event_location_winds[]` row. C consumes the event-by-location matrix, not B's single-storm `locations[]` view.
3. Add a normalization and agency-selection boundary between A and B. A's track file contains different agencies and averaging bases; B must not silently keep the first duplicate timestamp.
4. Add a pinned CLIMADA environment and run at least one real, frozen, offline fixture through `TropCyclone.from_tracks`.
5. Return missing central pressure and other dropped-event failures as structured event records with `reason_code`, rather than only as warning text (`hazard_wind_b.py`, lines 647–650).
6. Include the upstream data manifest in `input_hash` and the complete manifest in the version boundary. The current hazard version can remain unchanged when source provenance changes.
7. Validate track coordinates, include actual track and intensity values in `wind_field_id`, and add `scenario_id` inside the hashed storm-scenario result.
8. Implement the agreed distinction between `outside_storm_circulation` and `outside_model_radius`, or revise the C rule explicitly. Current B emits only the legacy `outside_storm_reach` status.

### 3.3 Role C

1. Add a C-to-D adapter or make C return D's contract directly:
   - `meta`: `rule_version`, `candidate_id` or rule-config hash, `hazard_version`, `payout_currency`, `wind_speed_unit`, and `min_reported_wind_mps`;
   - `rows`: `event_id`, `location_id`, `trigger_tier`, `modeled_payout_basis`, `data_status`, `wind_speed_mps`, and `rule_version`.
   C currently returns `results[].modeled_payout_basis_php`, `hazard_data_status`, and no `payout_table.meta` (`climada_trigger/engine.py`, lines 116–135 and 362–381).
2. Reject or mark the whole run non-computable when B reports `validation.status = fail` or `manifest.future_data_check = rejected`. These upstream controls are currently ignored (`engine.py`, lines 153–180).
3. Treat a duplicate `(event_id, exposure_id/location_id)` wind row as non-computable. Do not keep the first row and then calculate a payout (`engine.py`, lines 185–198 and 223–231).
4. Make the successful contract test use current A and B and produce a real C result. The existing integration test only proves that stale A 0.1.0 is refused through `version_conflict` (`tests/test_trigger.py`, lines 304–321).
5. Update the sensitivity generator for A 0.2.0, then rerun it with the submitted B version and a pinned CLIMADA environment. Keep the generated input snapshot and complete manifest in the package.
6. Record the one-minute-to-ten-minute conversion factor in the hazard manifest and recompute the hazard/config hash after conversion. The current helper changes wind values and labels without updating the B output hash (`analysis/run_sensitivity.py`, lines 107–114).
7. Include the rule config and upstream hashes inside C's output fingerprint. At present, a provenance-only change leaves C's output hash unchanged (`engine.py`, lines 376–381).
8. For storms that cross a policy boundary, use B's maximum wind inside the policy window or return non-computable until peak time is known. The baseline currently pays from the whole-event maximum under `event_overlaps_window` and only adds a warning flag (`engine.py`, lines 257–273).
9. Give promoted configurations a unique immutable rule version. All four experimental candidates may share a family version during comparison, but D and the audit trail must still retain `candidate_id` and `rule_config_hash` so two different payout schedules cannot look like the same rule.

### 3.4 Role D

1. Propagate B's `future_data_check` and rejected future rows. The current B-to-D adapter can convert a B result containing future-data rejection into a D result that says the future-data check passed (`climada_d/from_part_b.py`, lines 64–70 and 118–130).
2. Stop before producing numerical results whenever any fatal validation issue is added. Some fatal checks occur after the current early-failure gate, so D can return a failed status together with a precise-looking premium (`climada_d/historical_risk.py`, lines 750–815).
3. Correct empirical aggregation for coverage windows that wrap across calendar years. December and the following January can belong to one policy window (`historical_risk.py`, lines 264–269 and 333–378).
4. Require non-null `rule_version`, `hazard_version`, hazard-coverage version, and payout-row `data_status` (`historical_risk.py`, lines 501–547 and 686–698).
5. Complete the manifest requirements even when the caller supplies an empty manifest (`historical_risk.py`, lines 1153–1164).
6. A partial portfolio must not receive a clean `pass` status. Accepted results should state the excluded-record count and carry at least a warning.
7. Update the adapters and contract suite from vendored A 0.1.0 and B 0.1.0 to the submitted 0.2.0 boundaries.

## 4. Assessment of Role C's Product Choices

Role C followed the requested freedom appropriately. It did not pretend that the unanswered commercial terms were confirmed. The package supplies a baseline, three alternatives, exact-threshold boundary tests, gross and capped payout values, configurable half-up rounding, and a pending-rules template that suppresses numerical output.

The baseline uses the lower bounds of PAGASA Tropical Cyclone Wind Signals 2, 3 and 4 as candidate thresholds. The numerical ranges are consistent with PAGASA's current published ranges. However, PAGASA describes TCWS as a forecast warning about general wind strength over a province, city or municipality, with a warning lead time. CLIMADA produces modeled point wind at each store. The thresholds are therefore a reasonable experimental anchor, but the product should not tell users that a payout was triggered by an official PAGASA signal unless the runtime actually consumes and verifies that signal.

The recommended `14/28/42` benefit-day schedule and PHP 50,000 cap remain modeling choices. The sensitivity output shows that candidate selection materially changes reach and modeled payout. Product must approve the final thresholds, benefit schedule, cap, coverage window and wind-conversion policy before any candidate becomes a policy rule.

## 5. Documentation and Evidence Corrections

Role C's evidence register is the best structured of the four submissions: it separates company position, external fact, experimental choice, gap and decision owner. Keep that format for the consolidated register.

The Role C package should correct these presentation and evidence issues:

- replace references to planned B `v0.1.1` behavior with the actual accepted B version and contract;
- state that the submitted sensitivity results were generated with an older A boundary and are not reproducible with current A 0.2.0;
- distinguish PAGASA TCWS warning levels from modeled store-level point wind;
- retain the WMO conversion factor as pending until B supplies a sourced, approved transformation and records it in the run manifest; and
- repaginate both Word documents to remove almost-empty trailing pages.

The earlier Word legend for Role A remains stale: it reports two clean sample rows while the current adapter produces 50 accepted exposure rows and 10 rejections. It also contains search-result remnants, lacks a document title, and leaves most of page 2 empty.

## 6. Current Integration Contract

The next accepted demonstration must execute one frozen snapshot through these exact boundaries:

```text
A 0.2.x
  -> exposure + daily collection basis + immutable source manifest
B 0.2.x
  -> real CLIMADA event-by-location wind rows with explicit metric and coverage evidence
C candidate version
  -> experimental event-by-location payout_table matching D's schema
D corrected version
  -> historical frequency, expected payout, uncertainty, and pure-risk basis
```

The run must use one shared `as_of`, one immutable source manifest, compatible rule and hazard versions, and a single run ID. Its final report must retain excluded events and locations, pending parameters, input and output hashes, candidate identity, and validation status.

## 7. Acceptance Gate for the Resubmission

The resubmission is ready for product integration when all of the following are demonstrated:

- current A supplies C's daily-collection basis and current A and B consume each other's schemas;
- a real CLIMADA run completes from a frozen local track fixture;
- B reports one-minute sustained wind at 10 metres, or an approved and traceable conversion to another metric, on every event-by-location row;
- C consumes the submitted B result without manual relabeling and emits D's payout-table contract;
- A, B and C propagate future-data and fatal upstream status without returning payout numbers;
- D rejects fatal inputs without returning risk numbers;
- one current-version A to B to C to D contract test passes without vendored older modules or handwritten glue; and
- the evidence registers, manifests, README files and rendered documents describe the same versions, inputs and sample counts as the executable package.
