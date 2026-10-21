from modeling import run_historical_model
from modeling.hazard_wind_b import HazardConfig


AS_OF = "2026-10-20T00:00:00Z"
RULE_VERSION = "climada-test-rules-v1"


def snapshot():
    return {
        "snapshot_id": "pipeline-fixture-1",
        "data_version": "synthetic-portfolio-v1",
        "rule_version": RULE_VERSION,
        "merchants": [{
            "merchant_id": "M1", "merchant_name": "Fixture merchant",
            "recorded_at": "2026-01-01T00:00:00Z",
        }],
        "locations": [{
            "location_id": "L1", "merchant_id": "M1", "longitude": 125.0,
            "latitude": 11.2, "psgc_barangay_code": "0802604001",
            "crs": "EPSG:4326", "location_precision": "store_address_geocoded",
            "recorded_at": "2026-01-01T00:00:00Z",
        }],
        "coverages": [{
            "coverage_id": "C1", "merchant_id": "M1", "location_id": "L1",
            "status": "in_force", "coverage_start": "2026-06-01T00:00:00+08:00",
            "coverage_end": "2026-12-01T00:00:00+08:00",
            "coverage_amount": 25000, "daily_collection_basis_php": 500,
            "daily_collection_basis_definition": "synthetic daily settled collections",
            "daily_collection_basis_observation_window": "fixture",
            "daily_collection_basis_provenance": "synthetic contract fixture",
            "currency": "PHP", "rule_version": RULE_VERSION,
            "recorded_at": "2026-01-01T00:00:00Z",
        }],
    }


def tracks():
    rows = []
    for event_id, year in (("E1", 2001), ("E2", 2003)):
        for hour, lon in ((0, 126.0), (6, 124.0)):
            rows.append({
                "event_id": event_id, "track_id": event_id, "member_index": 0,
                "members_generated": 1, "season_year": year, "name": event_id,
                "observation_time": f"{year}-09-15T{hour:02d}:00:00Z",
                "latitude": 11.2, "longitude": lon, "max_sustained_wind": 80,
                "wind_unit": "kn", "wind_averaging_minutes": 1,
                "central_pressure_hpa": 950, "basin": "WP",
                "source": "synthetic pipeline fixture",
                "published_at": f"{year}-10-01T00:00:00Z",
                "data_version": "synthetic-tracks-v1",
            })
    return rows


def rule_config():
    return {
        "rule_version": RULE_VERSION, "candidate_id": "climada-test-candidate-v1",
        "rule_status": "test_fixture", "wind_unit": "m/s",
        "wind_metric": "sustained_10min_10m", "threshold_boundary": "inclusive",
        "coverage_window_rule": "event_overlaps_window",
        "coverage_window_mode": "recurring_month_day",
        "coverage_window_timezone_offset_hours": 8,
        "outside_reach_treatment": "no_trigger",
        "rounding": {"unit_php": 1, "mode": "half_up"},
        "tiers": [
            {"tier": 1, "threshold_mps": 17.0, "benefit_days": 14},
            {"tier": 2, "threshold_mps": 25.0, "benefit_days": 28},
            {"tier": 3, "threshold_mps": 33.0, "benefit_days": 42},
        ],
        "per_event_cap_php": 50000,
    }


def risk_config():
    confirmed = "confirmed"
    return {
        "observation_period": {"value": {"start_year": 2001, "end_year": 2004},
                               "status": confirmed, "source": "fixture"},
        "aggregation_basis": {"value": "per_event", "status": confirmed, "source": "fixture"},
        "frequency_method": {"value": "poisson", "status": confirmed, "source": "fixture"},
        "bootstrap_replicates": 0,
        "simulation_years": 100,
    }


def hazard_config():
    return HazardConfig(
        config_version="pipeline-fixture-v1", engine="fixture", intensity_threshold_mps=10.0,
        fixture_intensity={"E1|L1": 40.0, "E2|L1": 20.0},
        output_wind_conversion={
            "target_metric": "10-minute sustained wind at 10 m",
            "target_metric_id": "sustained_10min_10m",
            "factor_from_1min": 0.88, "status": "approved",
            "source": "synthetic contract-test conversion; not a product decision",
        },
    )


def manifest():
    return {
        "data_version": "synthetic-portfolio-v1",
        "rule_version": RULE_VERSION,
        "source_owner": "contract tests",
        "provenance": "synthetic fixtures only",
    }


def test_current_a_b_c_d_contract_is_replayable_and_product_facing():
    kwargs = dict(snapshot=snapshot(), historical_tracks=tracks(),
                  hazard_config=hazard_config(), rule_config=rule_config(),
                  risk_config=risk_config(), as_of=AS_OF, data_manifest=manifest())
    first = run_historical_model(**kwargs)
    second = run_historical_model(**kwargs)

    assert first["model_run_id"] == second["model_run_id"]
    assert first["output_hash"] == second["output_hash"]
    assert all(first["stage_hashes"].values())
    assert first["stages"]["input"]["validation"]["status"] == "pass"
    assert first["stages"]["hazard"]["validation"]["status"] != "fail"
    assert first["stages"]["trigger"]["validation"]["status"] == "warn"
    assert all(row["computable"] for row in first["event_location_results"])
    assert {row["wind_speed_mps"] for row in first["event_location_results"]} == {35.2, 17.6}
    assert first["risk_summary"]["expected_payout_basis"] > 0
    assert first["risk_summary"]["pure_risk_premium_basis"] == first["risk_summary"]["expected_payout_basis"]
    assert first["validation"]["status"] == "warn"  # fixture/test rule is intentionally non-production
