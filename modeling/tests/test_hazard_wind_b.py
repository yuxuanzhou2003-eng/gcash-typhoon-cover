"""
Contract tests for Part B (hazard_wind_b 0.4.0).
Run with:  python -m unittest test_hazard_wind_b.py -v

Two kinds of test:
  * Contract tests use the 'fixture' engine (pre-agreed numbers, no physics). They check B's
    fields, reason codes, leakage guard, hashing and determinism.
  * Real-engine tests (class TestRealClimada) run CLIMADA 6.1.0 on the frozen offline fixture in
    fixtures/. They are skipped only if CLIMADA is not installed (see requirements-b.txt).
Each test names the review item it covers (ABD_SUBMISSION_REVIEW section 3.2).
"""
import copy                                          # deep copies for mutation checks
import json                                          # reading the frozen fixtures
import os                                            # fixture paths
import unittest                                      # standard library test runner
from dataclasses import replace                      # modified copies of the frozen config

from modeling import hazard_wind_b as hb                            # the module under test

HERE = os.path.dirname(os.path.abspath(__file__))
AS_OF = "2024-10-25T00:00:00+00:00"                   # fixed cut-off used by contract tests

EXPOSURE = [
    {"location_id": "L1", "latitude": 14.60, "longitude": 121.00},
    {"location_id": "L2", "latitude": 12.50, "longitude": 124.00},
    {"location_id": "L3", "latitude": 7.10, "longitude": 125.60},
    {"location_id": "L4", "latitude": None, "longitude": 121.00},       # missing coordinate on purpose
    {"location_id": "L1", "latitude": 14.60, "longitude": 121.00},      # same shop, second policy
]


def _track(event_id="EVT1", published="2024-10-20T00:00:00+00:00", avg=1, **extra):
    """Two-point synthetic track in B's contract format (1-minute winds unless avg says otherwise)."""
    rows = [
        {"event_id": event_id, "observation_time": "2024-10-20T00:00:00+00:00", "latitude": 13.0,
         "longitude": 126.0, "max_sustained_wind": 80, "wind_unit": "kn", "wind_averaging_minutes": avg,
         "central_pressure_hpa": 960, "source": "synthetic_fixture", "published_at": published},
        {"event_id": event_id, "observation_time": "2024-10-20T06:00:00+00:00", "latitude": 14.0,
         "longitude": 123.0, "max_sustained_wind": 85, "wind_unit": "kn", "wind_averaging_minutes": avg,
         "central_pressure_hpa": 955, "source": "synthetic_fixture", "published_at": published},
    ]
    for r in rows:
        r.update(extra)
    return rows


CFG = hb.HazardConfig(engine="fixture", fixture_intensity={
    "EVT1|L1": 30.0,      # above 17.5 threshold -> ok
    "EVT1|L2": 12.0,      # below threshold, within reach -> below_reporting_threshold
    "EVT1|L3": 0.0,       # far from track -> outside_storm_reach
})
APPROVED_10MIN = {"10": {"factor_to_1min": 1 / 0.88, "status": "approved",
                         "source": "test-only approval record"}}


def run_b(tracks=None, cfg=CFG, exposure=EXPOSURE, manifest=None, **kw):
    return hb.compute_hazard_wind_field(exposure, tracks if tracks is not None else _track(), cfg,
                                        as_of=AS_OF, data_manifest=manifest or {"source": "test"}, **kw)


def codes(out):
    return [r["reason_code"] for r in out["rejected_track_rows"]]


class TestCoverageAndContract(unittest.TestCase):
    def test_every_location_returned_with_status(self):
        out = run_b()
        self.assertEqual(len(out["locations"]), len(EXPOSURE))           # nothing silently dropped
        by_id = {}
        for r in out["locations"]:
            by_id.setdefault(r["location_id"], []).append(r)
        self.assertEqual(by_id["L1"][0]["data_status"], hb.STATUS_OK)
        self.assertEqual([r["wind_speed_mps"] for r in by_id["L1"]], [30.0, 30.0])   # two policies, one wind
        self.assertEqual(by_id["L2"][0]["data_status"], hb.STATUS_BELOW_THRESHOLD)
        self.assertIsNone(by_id["L2"][0]["wind_speed_mps"])               # no fake precise zero
        self.assertEqual(by_id["L3"][0]["data_status"], hb.STATUS_OUTSIDE_REACH)
        self.assertEqual(by_id["L4"][0]["reason_code"], hb.REASON_MISSING_COORDINATE)

    def test_repeated_id_with_conflicting_coordinates_rejected(self):
        exp = EXPOSURE[:3] + [{"location_id": "L1", "latitude": 15.0, "longitude": 121.0}]
        last = run_b(exposure=exp)["locations"][-1]
        self.assertEqual(last["reason_code"], hb.REASON_DUPLICATE_LOCATION)
        self.assertIsNone(last["wind_speed_mps"])

    def test_neighbourhood_max_takes_the_highest_ring_wind(self):
        cfg = replace(CFG, centroid_granularity="exposure_neighbourhood_max", neighbourhood_bearings=4,
                      fixture_intensity={**CFG.fixture_intensity, "EVT1|L2@15km/090": 25.0, "EVT1|L1@15km/180": 20.0})
        out = run_b(cfg=cfg)
        by_id = {r["location_id"]: r for r in out["locations"]}
        self.assertEqual(by_id["L1"]["wind_speed_mps"], 30.0)               # the location itself is highest
        self.assertEqual(by_id["L2"]["wind_speed_mps"], 25.0)               # a ring point lifts it above threshold
        self.assertEqual(by_id["L2"]["data_status"], hb.STATUS_OK)
        self.assertEqual(len(out["locations"]), len(EXPOSURE))
        self.assertNotEqual(out["manifest"]["config_hash"], run_b()["manifest"]["config_hash"])

    def test_unknown_granularity_rejected(self):
        with self.assertRaises(NotImplementedError):
            run_b(cfg=replace(CFG, centroid_granularity="admin_areas"))

    def test_shared_wind_field(self):
        ids = {r["wind_field_id"] for r in run_b()["locations"] if r["wind_field_id"]}
        self.assertEqual(len(ids), 1)

    def test_future_bulletin_rejected(self):
        out = run_b(tracks=_track(published="2024-10-26T00:00:00+00:00"))
        self.assertIn(hb.REASON_FUTURE_DATA, codes(out))
        self.assertEqual(out["manifest"]["future_data_check"], "rejected")
        self.assertTrue(all(r["wind_speed_mps"] is None for r in out["locations"]))

    def test_invalid_unit_rejected(self):
        out = run_b(tracks=_track(wind_unit="mph"))
        self.assertIn(hb.REASON_INVALID_UNIT, codes(out))

    def test_empty_inputs(self):
        out = run_b(tracks=[], exposure=[])
        self.assertEqual(out["locations"], [])
        self.assertEqual(out["coverage"]["n_events_modelled"], 0)

    def test_inputs_not_mutated(self):
        exp, trk = copy.deepcopy(EXPOSURE), _track()
        before = (copy.deepcopy(exp), copy.deepcopy(trk))
        hb.compute_hazard_wind_field(exp, trk, CFG, as_of=AS_OF, data_manifest={})
        self.assertEqual((exp, trk), before)

    def test_fixture_never_passes(self):
        self.assertEqual(run_b()["validation"]["status"], "warn")

    def test_deterministic_repeat_run(self):
        a, b = run_b(), run_b()
        self.assertEqual(a["manifest"]["output_hash"], b["manifest"]["output_hash"])


class TestWindDefinition(unittest.TestCase):
    """Review 3.2 items 1 and 2."""

    def test_output_is_always_1min_sustained_at_10m(self):
        out = run_b()
        for r in out["locations"] + out["event_location_winds"]:
            self.assertEqual(r["wind_unit"], "m/s")
            self.assertEqual(r["wind_metric"], "max_sustained_wind")
            self.assertEqual(r["wind_metric_id"], "sustained_1min_10m")
            self.assertEqual(r["wind_averaging_minutes"], 1)
            self.assertEqual(r["averaging_period_min"], 1)
            self.assertEqual(r["wind_height_m"], 10)
            self.assertEqual(r["height_m"], 10)
        self.assertEqual(out["wind_definition"]["unit"], "m/s")
        self.assertEqual(out["manifest"]["wind_metric_id"], "sustained_1min_10m")
        self.assertTrue(out["validation"]["checks"]["output_is_1min_sustained_10m"])
        self.assertTrue(out["validation"]["checks"]["event_rows_have_explicit_wind_contract"])

    def test_10min_input_rejected_without_approved_conversion(self):
        out = run_b(tracks=_track(avg=10))                    # default config: conversion pending
        self.assertIn(hb.REASON_UNAPPROVED_CONVERSION, codes(out))
        self.assertEqual(out["coverage"]["n_events_modelled"], 0)
        self.assertTrue(all(r["wind_speed_mps"] is None for r in out["locations"]))

    def test_unstated_averaging_rejected(self):
        out = run_b(tracks=_track(avg=None))
        self.assertIn(hb.REASON_UNAPPROVED_CONVERSION, codes(out))

    def test_approved_input_conversion_is_applied_and_traced(self):
        cfg = replace(CFG, input_wind_conversions=APPROVED_10MIN)
        out = run_b(tracks=_track(avg=10), cfg=cfg)
        ev = out["events"][0]
        self.assertEqual(ev["source_track_metric"], "10-minute sustained")
        self.assertEqual(ev["input_conversion_applied"]["status"], "approved")
        self.assertAlmostEqual(ev["max_track_wind_1min_mps"], 85 * hb.KNOT_TO_MPS / 0.88, places=5)
        self.assertEqual(out["locations"][0]["wind_averaging_minutes"], 1)   # output still 1-minute

    def test_output_conversion_only_when_approved(self):
        out = run_b()
        self.assertTrue(all(r["wind_speed_mps_converted"] is None for r in out["locations"]))
        cfg = replace(CFG, output_wind_conversion={"target_metric": "10-minute sustained wind at 10 m",
                                                   "target_metric_id": "sustained_10min_10m",
                                                   "factor_from_1min": 0.88, "status": "approved",
                                                   "source": "test-only approval record"})
        converted = run_b(cfg=cfg)
        row = converted["locations"][0]
        self.assertAlmostEqual(row["wind_speed_mps_converted"], 30.0 * 0.88, places=6)
        self.assertEqual(row["converted_metric"], "10-minute sustained wind at 10 m")
        self.assertEqual(row["converted_metric_id"], "sustained_10min_10m")
        self.assertEqual(row["output_conversion"]["status"], "approved")
        self.assertEqual(converted["manifest"]["output_conversion"]["source"], "test-only approval record")

    def test_fixture_metric_is_explicitly_configured_not_inferred_from_track(self):
        explicit_fixture_metric = {
            "wind_metric": "test_fixture_wind",
            "wind_metric_id": "sustained_10min_10m",
            "wind_unit": "m/s",
            "wind_averaging_minutes": 10,
            "wind_height_m": 10,
            "basis": "test fixture declaration",
        }
        out = run_b(cfg=replace(CFG, fixture_output_definition=explicit_fixture_metric), tracks=_track(avg=1))
        self.assertEqual(out["event_location_winds"][0]["wind_metric_id"], "sustained_10min_10m")
        self.assertEqual(out["event_location_winds"][0]["averaging_period_min"], 10)
        with self.assertRaises(ValueError):
            run_b(cfg=replace(CFG, fixture_output_definition={}))

    def test_output_conversion_requires_complete_approved_metadata(self):
        with self.assertRaises(ValueError):
            run_b(cfg=replace(CFG, output_wind_conversion={"status": "approved", "factor_from_1min": 0.88}))

    def test_circulation_and_model_radius_are_separate_statuses(self):
        exposure = [
            {"location_id": "CIRC", "latitude": 13.0, "longitude": 124.0},
            {"location_id": "MODEL", "latitude": 7.1, "longitude": 125.6},
        ]
        out = run_b(exposure=exposure, tracks=_track(radius_oci_nm=10),
                    cfg=replace(CFG, fixture_intensity={}))
        rows = {row["location_id"]: row for row in out["event_location_winds"]}
        self.assertEqual(rows["CIRC"]["data_status"], hb.STATUS_OUTSIDE_CIRCULATION)
        self.assertIsNone(rows["CIRC"]["reason_code"])
        self.assertEqual(rows["MODEL"]["data_status"], hb.STATUS_OUTSIDE_CIRCULATION)
        wide = run_b(exposure=exposure, tracks=_track(radius_oci_nm=1000),
                     cfg=replace(CFG, fixture_intensity={}))
        rows = {row["location_id"]: row for row in wide["event_location_winds"]}
        self.assertEqual(rows["MODEL"]["data_status"], hb.STATUS_OUTSIDE_MODEL_RADIUS)
        self.assertEqual(rows["MODEL"]["reason_code"], hb.REASON_COVERAGE_GAP)


class TestAToBNormalisation(unittest.TestCase):
    """Review 3.2 item 3 (and 3.1 item 5): explicit A -> B boundary, one agency, no silent duplicates."""

    def a_rows(self):
        rows = _track()
        for r in rows:                                     # Part A 0.2.0 field names and unit spelling
            r["averaging_period_min"] = r.pop("wind_averaging_minutes")
            r["published_date"] = r.pop("published_at")
            r["wind_unit"] = "kt"
            r["agency"] = "usa"
        tokyo = copy.deepcopy(rows)
        for r in tokyo:
            r["agency"], r["averaging_period_min"] = "tokyo", 10
        return rows + tokyo

    def test_renames_and_selects_one_agency(self):
        norm = hb.normalize_track_records(self.a_rows(), agency="usa")
        rep = norm["normalization_report"]
        self.assertEqual(rep["n_output_rows"], 2)
        self.assertEqual(rep["dropped_by_agency"], {"tokyo": 2})
        self.assertEqual(rep["units_renamed"], {"kt": 4})
        out = run_b(tracks=norm["track_records"],
                    manifest={"track_normalization": rep})
        self.assertEqual(out["coverage"]["n_events_modelled"], 1)
        self.assertEqual(out["manifest"]["track_normalization"]["agency_selected"], "usa")

    def test_several_agencies_require_a_choice(self):
        with self.assertRaises(ValueError):
            hb.normalize_track_records(self.a_rows(), agency=None)

    def test_conflicting_alias_rejected(self):
        rows = self.a_rows()[:2]
        rows[0]["wind_averaging_minutes"] = 10              # disagrees with averaging_period_min = 1
        rep = hb.normalize_track_records(rows, agency="usa")["normalization_report"]
        self.assertEqual(rep["rejected_rows"][0]["reason_code"], hb.REASON_VERSION_CONFLICT)

    def test_duplicate_track_time_rejects_event_not_first_row(self):
        rows = _track() + [_track()[0]]                     # same observation_time twice
        out = run_b(tracks=rows)
        self.assertIn(hb.REASON_DUPLICATE_TRACK_TIME, codes(out))
        self.assertEqual(out["coverage"]["n_events_modelled"], 0)


class TestStructuredDropsAndHashing(unittest.TestCase):
    """Review 3.2 items 5, 6 and 7."""

    def test_missing_pressure_is_a_structured_drop(self):
        rows = _track(track_id="EVT1", member_index=0, members_generated=10, season_year=2024)
        rows[1]["central_pressure_hpa"] = None
        out = run_b(tracks=rows)
        drop = out["dropped_events"][0]
        self.assertEqual(drop["reason_codes"], [hb.REASON_MISSING_PRESSURE])
        self.assertEqual((drop["track_id"], drop["members_generated"], drop["season_year"]), ("EVT1", 10, 2024))
        # legacy text kept for Part D's adapter until it reads dropped_events[]
        self.assertTrue(any("missing central pressure" in w for w in out["validation"]["warnings"]))

    def test_too_few_points_is_a_structured_drop(self):
        out = run_b(tracks=_track()[:1])
        self.assertEqual(out["dropped_events"][0]["reason_codes"], [hb.REASON_TOO_FEW_POINTS])

    def test_upstream_manifest_changes_both_hashes(self):
        a = run_b(manifest={"source_file_sha256": "aaa"})
        b = run_b(manifest={"source_file_sha256": "bbb"})
        self.assertNotEqual(a["manifest"]["input_hash"], b["manifest"]["input_hash"])
        self.assertNotEqual(a["manifest"]["output_hash"], b["manifest"]["output_hash"])

    def test_config_change_changes_hazard_version(self):
        a = run_b()
        b = run_b(cfg=replace(CFG, config_version="b-config-draft-1"))
        self.assertNotEqual(a["manifest"]["config_hash"], b["manifest"]["config_hash"])
        self.assertNotEqual(a["manifest"]["output_hash"], b["manifest"]["output_hash"])

    def test_invalid_track_coordinate_rejected(self):
        rows = _track()
        rows[0]["latitude"] = 95.0
        self.assertIn(hb.REASON_INVALID_TRACK_COORD, codes(run_b(tracks=rows)))

    def test_wind_field_id_changes_with_track_values(self):
        rows = _track()
        rows[1]["central_pressure_hpa"] = 950                # same ids, different physics input
        a = run_b()["manifest"]["wind_field_id"]
        b = run_b(tracks=rows)["manifest"]["wind_field_id"]
        self.assertNotEqual(a, b)

    def test_event_hashes_bind_actual_track_intensity_and_provenance(self):
        base = run_b(manifest={"source_file_sha256": "base"})
        changed_track = _track(source="other_frozen_source")
        by_track = run_b(tracks=changed_track, manifest={"source_file_sha256": "base"})
        by_intensity = run_b(cfg=replace(CFG, fixture_intensity={"EVT1|L1": 31.0}),
                             manifest={"source_file_sha256": "base"})
        by_provenance = run_b(manifest={"source_file_sha256": "changed"})
        row = base["event_location_winds"][0]
        self.assertEqual(row["track_hash"], base["event_track_hashes"]["EVT1"])
        self.assertEqual(row["intensity_hash"], base["event_intensity_hashes"]["EVT1"])
        self.assertNotEqual(base["track_hash"], by_track["track_hash"])
        self.assertNotEqual(base["intensity_hash"], by_intensity["intensity_hash"])
        self.assertNotEqual(base["provenance_hash"], by_provenance["provenance_hash"])
        self.assertNotEqual(base["manifest"]["wind_field_id"], by_provenance["manifest"]["wind_field_id"])

    def test_scenario_id_is_inside_hashed_result(self):
        snap = {"storm_id": "S1", "bulletin_id": "B1", "published_at": "2024-10-20T00:00:00+00:00",
                "source": "synthetic_bulletin",
                "positions": [{"valid_time": r["observation_time"], "latitude": r["latitude"],
                               "longitude": r["longitude"], "max_sustained_wind": r["max_sustained_wind"],
                               "wind_unit": "kn", "wind_averaging_minutes": 1,
                               "central_pressure_hpa": r["central_pressure_hpa"]} for r in _track()]}
        cfg = replace(CFG, fixture_intensity={"S1#B1|L1": 25.0})
        a = hb.hazard_for_storm_scenarios(snap, EXPOSURE[:3], cfg, as_of=AS_OF, data_manifest={})
        b = hb.hazard_for_storm_scenarios(snap, EXPOSURE[:3], cfg, as_of=AS_OF, data_manifest={},
                                          scenario_id="S1#B1#alt")
        self.assertEqual(a["scenario_id"], "S1#B1#base")
        self.assertEqual(a["scenario_weight_status"], "unweighted")
        self.assertEqual(a["locations"][0]["wind_speed_mps"], 25.0)
        self.assertNotEqual(a["manifest"]["output_hash"], b["manifest"]["output_hash"])


class TestPartDSelection(unittest.TestCase):
    """Part D requests carried from 0.2.0."""

    def test_select_storms_drops_spur_and_applies_seasons(self):
        storms = [{"sid": "A", "season": 1979, "track_type": "main"},
                  {"sid": "B", "season": 1980, "track_type": "main"},
                  {"sid": "C", "season": 2024, "track_type": "main"},
                  {"sid": "D", "season": 2024, "track_type": "spur-main"},
                  {"sid": "E", "season": 2025, "track_type": "main"},
                  {"sid": "F", "season": 2025, "track_type": "PROVISIONAL"}]
        keep, rep = hb.select_storms(storms, hb.TrackSelectionConfig(provider="usa"))
        self.assertEqual(keep, {"B", "C"})
        self.assertEqual(rep["spur_dropped"]["sids"], ["D"])
        self.assertFalse(rep["conditional_season"]["included"])
        self.assertEqual(rep["seasons_used"], [1980, 2024])

    def test_conditional_season_included_when_all_main(self):
        storms = [{"sid": "B", "season": 2000, "track_type": "main"},
                  {"sid": "E", "season": 2025, "track_type": "main"},
                  {"sid": "G", "season": 2025, "track_type": "spur-main"}]
        keep, rep = hb.select_storms(storms, hb.TrackSelectionConfig(provider="usa"))
        self.assertEqual(keep, {"B", "E"})
        self.assertEqual(rep["seasons_used"], [2000, 2025])

    def test_provider_none_refused(self):
        with self.assertRaises(ValueError):
            hb.load_ibtracs_track_records(hb.TrackSelectionConfig(provider=None), retrieved_at=AS_OF)

    def test_events_carry_season_and_members(self):
        rows = _track("2001A", track_id="2001A", member_index=0, members_generated=10,
                      season_year=2001, track_type="main")
        out = run_b(tracks=rows + _track("2001A_gen3"), cfg=replace(CFG, fixture_intensity={}))
        ev = {e["event_id"]: e for e in out["events"]}
        self.assertEqual((ev["2001A"]["season_year"], ev["2001A"]["members_generated"]), (2001, 10))
        self.assertEqual((ev["2001A_gen3"]["track_id"], ev["2001A_gen3"]["member_index"]), ("2001A", 3))
        self.assertIsNone(ev["2001A_gen3"]["members_generated"])          # never guessed
        self.assertEqual(out["manifest"]["seasons_used"], [2001])


def _climada_installed():
    try:
        import climada  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_climada_installed(), "CLIMADA not installed: pip install -r requirements-b.txt")
class TestRealClimada(unittest.TestCase):
    """Review 3.2 item 4: a real CLIMADA run from a frozen, offline, local fixture."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "fixtures", "b_offline_track_fixture.json")) as fh:
            cls.fx = json.load(fh)
        with open(os.path.join(HERE, "fixtures", "b_offline_expected.json")) as fh:
            cls.expected = json.load(fh)

    def run_real(self, **cfg):
        return hb.compute_hazard_wind_field(self.fx["exposure"], self.fx["track_records"],
                                            hb.HazardConfig(engine="climada", **cfg),
                                            as_of=self.fx["as_of"], data_manifest={"fixture": "b_offline"})

    def test_real_run_matches_frozen_expected_values(self):
        out = self.run_real()
        self.assertEqual(out["validation"]["status"], "warn")              # pending parameters cap at warn
        self.assertEqual(out["validation"]["checks"]["network_attempts_blocked"], [])
        tol = self.expected["tolerance_mps"]
        for r in out["locations"]:
            exp = self.expected["locations"][r["location_id"]]
            self.assertEqual(r["data_status"], exp["data_status"])
            if exp["wind_speed_mps"] is None:
                self.assertIsNone(r["wind_speed_mps"])
            else:
                self.assertAlmostEqual(r["wind_speed_mps"], exp["wind_speed_mps"], delta=tol)
        self.assertEqual(self.run_real()["manifest"]["output_hash"], out["manifest"]["output_hash"])

    def test_neighbourhood_max_is_at_least_the_point_wind(self):
        point = {r["location_id"]: r["wind_speed_mps"] for r in self.run_real()["locations"]}
        ring = self.run_real(centroid_granularity="exposure_neighbourhood_max")
        self.assertEqual(len(ring["locations"]), len(self.fx["exposure"]))
        for r in ring["locations"]:
            if point[r["location_id"]] is not None:
                self.assertGreaterEqual(r["wind_speed_mps"], point[r["location_id"]])
        self.assertTrue(any(r["wind_speed_mps"] and point[r["location_id"]]
                            and r["wind_speed_mps"] > point[r["location_id"]] for r in ring["locations"]))

    def test_download_attempt_is_blocked_and_structured(self):
        out = self.run_real(ignore_distance_to_coast=False)   # CLIMADA would fetch a NASA file here
        if out["validation"]["checks"]["network_attempts_blocked"]:
            self.assertEqual(out["validation"]["status"], "fail")
            self.assertEqual(out["dropped_events"][0]["reason_codes"], [hb.REASON_NETWORK_BLOCKED])
        else:                                                  # data file already cached locally
            self.assertNotEqual(out["validation"]["status"], "fail")


if __name__ == "__main__":
    unittest.main(verbosity=2)
