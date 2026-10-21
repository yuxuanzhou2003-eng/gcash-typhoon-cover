"""
CLIMADA model package - Part B: Tropical-cyclone hazard and wind field
=====================================================================

Owner          : Part B (hazard and wind field)
Pipeline place : A (input adapter) -> **B (this module)** -> C (trigger/payout) -> D (historical risk)
Contract source: docs/MODEL_HANDOFF.en.md, sections 2, 3, 4, 5 and 6

What this module DOES
---------------------
1. Accepts the frozen exposure list produced by Part A (location_id, longitude, latitude).
2. Accepts frozen tropical-cyclone track records (historical best tracks, or the positions
   visible in one storm bulletin) and rejects anything published after `as_of`.
3. Builds ONE CLIMADA `TropCyclone` hazard over ONE shared set of centroids, so every
   merchant hit by the same storm is read from the same spatial wind field
   (spatial correlation is preserved - MODEL_HANDOFF section 4).
4. Returns, per location and per event, the wind intensity in m/s plus a coverage flag
   (`data_status`) and a reason code when the value is not computable.
5. Writes a manifest (versions, hashes, config, pending parameters) and runs B's own checks.

What this module does NOT do (explicit boundary - MODEL_HANDOFF section 2)
--------------------------------------------------------------------------
- It does not decide eligibility, trigger tiers, payouts or premiums (that is C and D).
- It does not convert wind to a PAGASA signal number or to any payout tier.
- It does not open network connections on its own. The only function that downloads data
  (`load_ibtracs_track_records`) must be called explicitly by the orchestrator.
- It does not invent values for parameters that no current source supports. Those are
  registered in PENDING_PARAMETERS with status `pending_confirmation`.

Verified CLIMADA facts used here (retrieved 19 Oct 2026, see EVIDENCE_REGISTER below)
-------------------------------------------------------------------------------------
- Latest CLIMADA core release: 6.1.0 (2025-09-30). `latest` docs are 6.1.1-dev.
- `TropCyclone.from_tracks(tracks, centroids, model="H08", model_kwargs=None,
   ignore_distance_to_coast=False, store_windfields=False, metric="equirect",
   intensity_thres=17.5, max_latitude=61, max_dist_inland_km=1000,
   max_dist_eye_km=300, max_memory_gb=8)`.
- Available wind-profile models: "H1980", "H08" (default), "H10", "ER11".
- `Centroids.from_lat_lon` is deprecated since 5.0.0 -> we use `Centroids(lat=..., lon=...)`.
- `TropCyclone.set_from_tracks` was removed in 6.1.0 -> we only use `from_tracks`.
- CLIMADA >= 6.0.0 reads IBTrACS v4.1 (v04r01).
"""

# ---------------------------------------------------------------------------------------
# 0. Standard-library imports (always available, no network side effects)
# ---------------------------------------------------------------------------------------
from __future__ import annotations          # allows modern type hints on older Python versions

import copy                                  # deep copies so we never mutate caller data
import hashlib                               # SHA-256 hashes for input_hash / output_hash
import json                                  # canonical JSON serialisation for hashing
import math                                  # haversine distance and finite-number checks
import platform                              # python version recorded in the manifest
import socket                                # used ONLY to block network access during the engine run
from contextlib import contextmanager        # context manager for the no-network guard
from dataclasses import asdict, dataclass, field   # typed, hashable configuration object
from datetime import datetime, timezone      # ISO-8601 parsing and UTC normalisation
from typing import Any, Optional             # type hints for readability

import numpy as np                           # vectorised arrays (CLIMADA also depends on numpy)

# ---------------------------------------------------------------------------------------
# 1. Module identity and fixed vocabularies
# ---------------------------------------------------------------------------------------
MODULE_NAME = "climada_model.hazard_wind_b"  # name written into every output and manifest
MODULE_VERSION = "0.3.0"                     # bump on ANY change that can alter outputs
# 0.1.1  repeated location_id with the same coordinates shares one wind value (Part C request)
# 0.2.0  IBTrACS season selection, spur removal, single agency, optional synthetic members,
#        season_year / track_id / member_index / members_generated in events[] (Part D request)
# 0.3.0  integration review fixes (ABD_SUBMISSION_REVIEW, 21 Oct 2026, section 3.2):
#        output is always 1-minute sustained wind at 10 m; non-1-minute input needs an APPROVED
#        conversion or the event is rejected; A->B normalisation and agency selection boundary;
#        duplicate track times reject the event; structured event drops; upstream manifest in
#        input_hash; full manifest inside output_hash; wind_field_id hashes real track/intensity
#        values; scenario_id and run_id inside the hashed result; offline by default.
PENDING = "pending_confirmation"             # the single agreed marker for unsupported values

# Unit conversion constants. These are exact definitions, not modelling assumptions.
KNOT_TO_MPS = 1852.0 / 3600.0                # 1 international knot = 1852 m per hour
KMH_TO_MPS = 1000.0 / 3600.0                 # 1 km/h = 1000 m per 3600 s
EARTH_RADIUS_KM = 6371.0                     # mean Earth radius used for haversine distances

# Location-level coverage flags returned in locations[].data_status.
# They separate "the storm really produced low wind here" from "we could not compute".
STATUS_OK = "ok"                                       # CLIMADA returned a wind value >= reporting threshold
STATUS_BELOW_THRESHOLD = "below_reporting_threshold"   # inside storm reach, but CLIMADA stores 0 below intensity_thres
# A known storm-circulation boundary is a computed zero outside the storm;
# a location beyond CLIMADA's configured computation radius is a coverage gap.
STATUS_OUTSIDE_CIRCULATION = "outside_storm_circulation"
STATUS_OUTSIDE_MODEL_RADIUS = "outside_model_radius"
STATUS_OUTSIDE_REACH = STATUS_OUTSIDE_MODEL_RADIUS       # legacy import; new rows use the explicit status
STATUS_NOT_COMPUTABLE = "not_computable"               # no value can be given; see reason_code

# Reason codes agreed in MODEL_HANDOFF section 3 (plus two B-specific codes, marked).
REASON_MISSING_COORDINATE = "missing_coordinate"
REASON_MISSING_HAZARD = "missing_hazard"
REASON_UNMAPPED_EXPOSURE = "unmapped_exposure"
REASON_COVERAGE_GAP = "coverage_gap"
REASON_VERSION_CONFLICT = "version_conflict"
REASON_FUTURE_DATA = "future_data_rejected"
REASON_INVALID_UNIT = "invalid_unit"
REASON_DUPLICATE_LOCATION = "duplicate_location_id"    # B-specific: same id appears twice in A's input
REASON_ENGINE_UNAVAILABLE = "hazard_engine_unavailable" # B-specific: CLIMADA could not be imported
REASON_MISSING_PRESSURE = "missing_central_pressure"   # B-specific: Holland profiles need central pressure
REASON_DUPLICATE_TRACK_TIME = "duplicate_track_time"   # B-specific: two positions at one time (e.g. two agencies)
REASON_INVALID_TRACK_COORD = "invalid_track_coordinate" # B-specific: track position outside valid range
REASON_UNAPPROVED_CONVERSION = "unapproved_wind_conversion"  # B-specific: non-1-minute input, no approved factor
REASON_TOO_FEW_POINTS = "too_few_track_points"         # B-specific: fewer than 2 valid positions
REASON_NETWORK_BLOCKED = "network_access_blocked"      # B-specific: engine tried to download data mid-run

# What B's wind numbers ARE. Verified in the installed CLIMADA 6.1.0 source:
# trop_cyclone.py describes the intensity as "1-minute sustained winds at 10 meters above ground".
# The output definition is fixed by the engine, NOT by the averaging period of the input track.
WIND_OUTPUT_DEFINITION = {
    "wind_metric": "max_sustained_wind",
    "wind_metric_id": "sustained_1min_10m",
    "wind_unit": "m/s",
    "wind_averaging_minutes": 1,
    "averaging_period_min": 1,
    "wind_height_m": 10,
    "height_m": 10,
    "unit": "m/s",
    "basis": "CLIMADA 6.1.0 TropCyclone.from_tracks (docstring: 1-minute sustained winds at 10 m above ground)",
}
OUTPUT_WIND_UNIT = "m/s"

# Wind units B accepts on incoming track records, mapped to a factor that converts to m/s.
ACCEPTED_WIND_UNITS = {"kn": KNOT_TO_MPS, "m/s": 1.0, "km/h": KMH_TO_MPS}

# Wind-profile models that the current CLIMADA API documents for TropCyclone.from_tracks.
CLIMADA_WIND_MODELS = ("H1980", "H08", "H10", "ER11")


# ---------------------------------------------------------------------------------------
# 2. Pending-parameter register (MODEL_HANDOFF section 7: "registered centrally, not hidden")
# ---------------------------------------------------------------------------------------
# Every entry below is a decision that B cannot make alone. Values that are CLIMADA library
# defaults are labelled as such - a library default is NOT a confirmed product parameter.
PENDING_PARAMETERS: dict[str, dict[str, Any]] = {
    "wind_metric": {
        "status": PENDING,
        "b_output_basis": (
            "CONFIRMED: B always outputs 1-minute sustained wind at 10 m (CLIMADA 6.1.0 source). "
            "Input tracks must therefore be 1-minute; other averaging periods need an approved "
            "input conversion (HazardConfig.input_wind_conversions) or the event is rejected."
        ),
        "still_pending": "the product's own trigger metric and any approved output conversion for Part C",
        "candidate_conversion": (
            "CLIMADA itself uses 0.88 to convert 1-minute to 10-minute sustained winds "
            "(STORM_1MIN_WIND_FACTOR, citing Bloemendaal et al. 2020). Candidate only; not applied."
        ),
        "question": "Does the product measure 1-minute sustained, 10-minute sustained, or gust wind?",
        "why_it_matters": (
            "IBTrACS documents that USA agencies report 1-minute winds while RSMC Tokyo (JMA) "
            "reports 10-minute winds; PAGASA public bulletins use 10-minute averages. The same "
            "storm therefore has different numeric intensities depending on the metric."
        ),
        "decision_owner": "Product owner + Part C (trigger rules)",
    },
    "wind_averaging_conversion_factor": {
        "status": PENDING,
        "question": "If track source and product metric differ, which conversion factor is approved?",
        "why_it_matters": "No factor is applied by B until one is confirmed from an approved source.",
        "decision_owner": "Part B proposes with source, product owner approves",
    },
    "wind_profile_model": {
        "status": PENDING,
        "current_value": "H08",
        "value_origin": "CLIMADA library default (TropCyclone.from_tracks model='H08')",
        "decision_owner": "Part B (with validation evidence)",
    },
    "intensity_threshold_mps": {
        "status": PENDING,
        "current_value": 17.5,
        "value_origin": "CLIMADA library default intensity_thres=17.5 m/s",
        "why_it_matters": (
            "CLIMADA stores 0 for winds below this value. PAGASA TCWS No.1 starts at 10.8 m/s, so "
            "if Part C needs tiers below 17.5 m/s this threshold must be lowered before C can work."
        ),
        "decision_owner": "Part C states the lowest wind value it needs; Part B sets threshold",
    },
    "ignore_distance_to_coast": {
        "status": "set_by_part_b",
        "current_value": True,
        "reason": (
            "With False, CLIMADA 6.1.0 downloads a NASA distance-to-coast raster at run time "
            "(observed in B's first real run), breaking the offline and no-network rule. The check "
            "only skips centroids more than max_dist_inland_km (1000 km) from the coast; no "
            "Philippine location is that far inland, so Philippine results are unchanged."
        ),
        "decision_owner": "Part B",
    },
    "max_dist_eye_km": {
        "status": PENDING,
        "current_value": 300.0,
        "value_origin": "CLIMADA library default max_dist_eye_km=300",
        "decision_owner": "Part B (with validation evidence)",
    },
    "track_time_step_h": {
        "status": PENDING,
        "current_value": 1.0,
        "value_origin": "CLIMADA TCTracks.equal_timestep default time_step_h=1",
        "decision_owner": "Part B",
    },
    "centroid_granularity": {
        "status": PENDING,
        "current_value": "exposure_points",
        "question": "Exact merchant points, a regular grid, or administrative areas?",
        "decision_owner": "Product owner + Part A (location precision) + Part C (rule geography)",
    },
    "ibtracs_provider": {
        "status": PENDING,
        "proposed_value": "usa",
        "rule_from_part_d": "one agency for all storms, never provider=None (Part D, 2026-10-21)",
        "why_usa": "USA agencies report 1-minute winds, so CLIMADA's rescaling factor is 1.0 (no conversion)",
        "question": "Part D to confirm 'usa' after seeing how many storms it drops",
        "decision_owner": "Part B proposes, Part D confirms for historical consistency",
    },
    "historical_period": {
        "status": "set_by_part_d",
        "value": "IBTrACS SEASON 1980-2024; season 2025 only if all its storms are TRACK_TYPE main",
        "source": "Part D message, 2026-10-21",
        "decision_owner": "Part D",
    },
    "synthetic_members": {
        "status": PENDING,
        "current_value": "off (historical tracks only)",
        "option": "CLIMADA calc_perturbed_trajectories, default nb_synth_tracks=9 (10 members per storm)",
        "decision_owner": "Part D (requested behind a config flag, historical-only first)",
    },
}


# ---------------------------------------------------------------------------------------
# 3. Evidence register rows contributed by Part B (shared register schema, section 2.1)
# ---------------------------------------------------------------------------------------
# Only sources actually consulted are listed. `retrieved_at` is the date they were checked.
EVIDENCE_REGISTER_B: list[dict[str, Any]] = [
    {
        "topic": "Definition of B's output wind",
        "current_company_position": PENDING,
        "source_title": "climada/hazard/trop_cyclone/trop_cyclone.py and trop_cyclone_windfields.py (installed source)",
        "source_owner": "ETH Zurich / CLIMADA contributors",
        "source_url_or_file": "pip package climada==6.1.0",
        "publication_or_effective_date": "2025-09-30",
        "retrieved_at": "2026-10-21",
        "version": "6.1.0",
        "applies_to_this_project": True,
        "status": "confirmed_external_fact",
        "gap": "Product trigger metric and any output conversion still need approval.",
        "decision_owner": "Part B (definition), product owner (conversion)",
    },
    {
        "topic": "CLIMADA release in use",
        "current_company_position": PENDING,
        "source_title": "CLIMADA Changelog (6.1.0 release, 2025-09-30)",
        "source_owner": "ETH Zurich / CLIMADA contributors",
        "source_url_or_file": "https://climada-python.readthedocs.io/en/latest/misc/CHANGELOG.html",
        "publication_or_effective_date": "2025-09-30",
        "retrieved_at": "2026-10-19",
        "version": "6.1.0",
        "applies_to_this_project": True,
        "status": "confirmed_external_fact",
        "gap": "Team must pin the exact installed version in the environment lock file.",
        "decision_owner": "Part B",
    },
    {
        "topic": "TropCyclone.from_tracks signature and wind models",
        "current_company_position": PENDING,
        "source_title": "climada.hazard.trop_cyclone package (API reference)",
        "source_owner": "ETH Zurich / CLIMADA contributors",
        "source_url_or_file": "https://climada-python.readthedocs.io/en/latest/api/climada/climada.hazard.trop_cyclone.html",
        "publication_or_effective_date": "living documentation",
        "retrieved_at": "2026-10-19",
        "version": "latest (6.1.1-dev)",
        "applies_to_this_project": True,
        "status": "confirmed_external_fact",
        "gap": "Choice among H1980/H08/H10/ER11 not yet validated for the Philippines.",
        "decision_owner": "Part B",
    },
    {
        "topic": "IBTrACS wind averaging periods per agency",
        "current_company_position": PENDING,
        "source_title": "IBTrACS v04r01 column documentation",
        "source_owner": "NOAA NCEI",
        "source_url_or_file": "https://www.ncei.noaa.gov/sites/default/files/2025-09/IBTrACS_v04r01_column_documentation.pdf",
        "publication_or_effective_date": "2025-09",
        "retrieved_at": "2026-10-19",
        "version": "v04r01",
        "applies_to_this_project": True,
        "status": "confirmed_external_fact",
        "gap": "Product wind metric (1-min / 10-min / gust) not yet decided.",
        "decision_owner": "Product owner",
    },
    {
        "topic": "Fallback environmental pressure and 1-to-10-minute factor in CLIMADA",
        "current_company_position": PENDING,
        "source_title": "climada.hazard.tc_tracks source (BASIN_ENV_PRESSURE, STORM_1MIN_WIND_FACTOR)",
        "source_owner": "ETH Zurich / CLIMADA contributors",
        "source_url_or_file": "https://climada-python.readthedocs.io/en/latest/_modules/climada/hazard/tc_tracks.html",
        "publication_or_effective_date": "CLIMADA 6.1.0 source",
        "retrieved_at": "2026-10-21",
        "version": "6.1.0",
        "applies_to_this_project": True,
        "status": "confirmed_external_fact",
        "gap": ("WP basin default is 1005 hPa (used by B when a track lacks it; the fixture wind moved "
                "about 3% versus the 1010 hPa global default). 0.88 is a candidate factor only."),
        "decision_owner": "Part B (pressure); product owner + Part C (factor)",
    },
    {
        "topic": "Reproducibility of CLIMADA synthetic tracks",
        "current_company_position": PENDING,
        "source_title": "climada.hazard.tc_tracks_synth source (_apply_decay_coeffs)",
        "source_owner": "ETH Zurich / CLIMADA contributors",
        "source_url_or_file": "https://climada-python.readthedocs.io/en/latest/_modules/climada/hazard/tc_tracks_synth.html",
        "publication_or_effective_date": "CLIMADA 6.1.0 source",
        "retrieved_at": "2026-10-21",
        "version": "6.1.0",
        "applies_to_this_project": True,
        "status": "confirmed_external_fact",
        "gap": "Landfall decay uses an unseeded generator: freeze synthetic track records once and reuse.",
        "decision_owner": "Part B",
    },
    {
        "topic": "PAGASA Tropical Cyclone Wind Signal wind ranges",
        "current_company_position": PENDING,
        "source_title": "DOST-PAGASA press release: Modified TCWS system",
        "source_owner": "DOST-PAGASA",
        "source_url_or_file": "https://bagong.pagasa.dost.gov.ph/press-release/108?page=12",
        "publication_or_effective_date": "2022-03-23",
        "retrieved_at": "2026-10-19",
        "version": "Modified TCWS (2022)",
        "applies_to_this_project": "context_only",
        "status": "context_only_not_a_product_rule",
        "gap": "Whether product tiers follow TCWS is Part C's decision, not B's.",
        "decision_owner": "Part C",
    },
]


# ---------------------------------------------------------------------------------------
# 4. Configuration object
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)          # frozen = immutable, so a config cannot change mid-run
class HazardConfig:
    """All knobs that can change B's numbers. Its hash goes into the manifest."""

    config_version: str = "b-config-draft-0"         # version label agreed with the integration lead
    engine: str = "climada"                          # "climada" for real runs; "fixture" ONLY for contract tests
    wind_model: str = "H08"                          # CLIMADA default; see PENDING_PARAMETERS["wind_profile_model"]
    intensity_threshold_mps: float = 17.5            # CLIMADA default; see PENDING_PARAMETERS
    max_dist_eye_km: float = 300.0                   # CLIMADA default; see PENDING_PARAMETERS
    max_dist_inland_km: float = 1000.0               # CLIMADA default for inland decay cut-off
    ignore_distance_to_coast: bool = True            # True keeps runs offline; see PENDING_PARAMETERS
    track_time_step_h: float = 1.0                   # interpolation step for tracks (CLIMADA default 1 h)
    centroid_granularity: str = "exposure_points"    # only mode implemented; others are pending
    # INPUT conversion to 1-minute, keyed by averaging minutes (as a string). Applied ONLY when
    # status == "approved". A pending entry is recorded but never applied; such events are rejected.
    input_wind_conversions: dict = field(default_factory=lambda: {
        "10": {"factor_to_1min": round(1 / 0.88, 6), "status": PENDING,
               "source": "CLIMADA tc_tracks.STORM_1MIN_WIND_FACTOR = 0.88 (Bloemendaal et al. 2020); candidate only"},
    })
    # OUTPUT conversion for Part C (e.g. to compare with PAGASA 10-minute thresholds). Reported as
    # wind_speed_mps_converted ONLY when status == "approved"; otherwise that field stays null.
    output_wind_conversion: dict = field(default_factory=lambda: {
        "target_metric": "10-minute sustained wind at 10 m", "target_metric_id": "sustained_10min_10m",
        "factor_from_1min": 0.88, "status": PENDING,
        "source": "CLIMADA tc_tracks.STORM_1MIN_WIND_FACTOR = 0.88 (Bloemendaal et al. 2020); candidate only",
    })
    primary_event_id: Optional[str] = None           # which event fills locations[] (single-storm view)
    coordinate_bounds: tuple = (-90.0, 90.0, -180.0, 180.0)  # WGS84 validity bounds (lat_min, lat_max, lon_min, lon_max)
    fixture_intensity: dict = field(default_factory=dict)   # {"event_id|location_id": m/s} - test engine only
    # Fixture values have no physics.  Their metric must therefore be declared in the
    # config, never inferred from the source-track averaging period.
    fixture_output_definition: dict = field(default_factory=lambda: {
        **WIND_OUTPUT_DEFINITION,
        "basis": "fixture configuration: explicit contract-test output definition",
    })


# ---------------------------------------------------------------------------------------
# 5. Small, pure helper functions
# ---------------------------------------------------------------------------------------
def _canonical_hash(obj: Any) -> str:
    """Return a SHA-256 hex digest of a JSON-serialisable object with stable key order."""
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)  # sorted keys => same bytes every run
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()       # prefix makes the algorithm explicit


def _parse_utc(ts: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string to an aware UTC datetime; return None if missing or invalid."""
    if ts is None or ts == "":                    # missing timestamps are reported, never guessed
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))  # accept the common trailing 'Z'
    except ValueError:                            # malformed text -> treat as missing
        return None
    if dt.tzinfo is None:                         # naive timestamps are ambiguous across time zones
        return None                               # (handoff requires explicit time zones; PHT is UTC+8)
    return dt.astimezone(timezone.utc)            # normalise to UTC for all comparisons


def _is_finite_number(x: Any) -> bool:
    """True if x is a real, finite number (rejects None, NaN, inf, strings)."""
    return isinstance(x, (int, float, np.floating, np.integer)) and math.isfinite(float(x))


def _resolve_output_definition(config: HazardConfig) -> dict[str, Any]:
    """Return the declared output metric; fixtures may never infer it from track input."""
    if config.engine != "fixture":
        return dict(WIND_OUTPUT_DEFINITION)

    definition = copy.deepcopy(config.fixture_output_definition)
    required = ("wind_metric", "wind_metric_id", "wind_unit", "wind_averaging_minutes", "wind_height_m")
    missing = [key for key in required if definition.get(key) in (None, "", PENDING)]
    if missing:
        raise ValueError("fixture_output_definition must explicitly declare " + ", ".join(missing))
    if definition["wind_unit"] != OUTPUT_WIND_UNIT:
        raise ValueError("fixture_output_definition.wind_unit must be 'm/s'")
    if not _is_finite_number(definition["wind_averaging_minutes"]) or float(definition["wind_averaging_minutes"]) <= 0:
        raise ValueError("fixture_output_definition.wind_averaging_minutes must be positive")
    if not _is_finite_number(definition["wind_height_m"]) or float(definition["wind_height_m"]) <= 0:
        raise ValueError("fixture_output_definition.wind_height_m must be positive")
    definition["wind_averaging_minutes"] = int(definition["wind_averaging_minutes"])
    definition["wind_height_m"] = int(definition["wind_height_m"])
    definition["averaging_period_min"] = definition["wind_averaging_minutes"]
    definition["height_m"] = definition["wind_height_m"]
    definition["unit"] = OUTPUT_WIND_UNIT
    definition.setdefault("basis", "fixture configuration: explicit output definition")
    return definition


def _output_conversion_metadata(config: HazardConfig) -> dict[str, Any]:
    """Normalize optional 1-minute output conversion metadata without applying it by default."""
    raw = copy.deepcopy(config.output_wind_conversion or {})
    metadata = {
        "status": raw.get("status", "not_requested"),
        "factor_from_1min": raw.get("factor_from_1min"),
        "target_metric": raw.get("target_metric"),
        "target_metric_id": raw.get("target_metric_id"),
        "source": raw.get("source"),
    }
    if metadata["status"] == "approved":
        if not _is_finite_number(metadata["factor_from_1min"]) or float(metadata["factor_from_1min"]) <= 0:
            raise ValueError("approved output conversion requires a positive factor_from_1min")
        if metadata["target_metric"] in (None, "") or metadata["target_metric_id"] in (None, "") \
                or metadata["source"] in (None, ""):
            raise ValueError("approved output conversion requires target_metric, target_metric_id, and source")
        metadata["factor_from_1min"] = float(metadata["factor_from_1min"])
    return metadata


def _haversine_km(lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    """Great-circle distance in km between arrays of points (broadcasts like numpy)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)                    # latitudes in radians
    dphi = p2 - p1                                                  # latitude difference
    dlmb = np.radians(lon2) - np.radians(lon1)                      # longitude difference
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2  # haversine formula
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))         # clip guards rounding errors


def _climada_version() -> str:
    """Return the installed CLIMADA version string, or a clear marker if not installed."""
    try:
        from importlib.metadata import version     # standard way to read an installed package version
        return version("climada")
    except Exception:                               # package missing or metadata unreadable
        return "not_installed"


# ---------------------------------------------------------------------------------------
# 6. Input validation: exposure from Part A
# ---------------------------------------------------------------------------------------
def validate_exposure(
    exposure: list[dict], config: HazardConfig
) -> tuple[list[dict], list[dict], list[str], list[tuple]]:
    """
    Split Part A's exposure rows into (unique_valid_locations, rejected_rows, warnings, row_map).

    Part A lists a shop once PER POLICY, so the same location_id can legitimately appear
    several times. B computes the wind once per unique location and maps every input row
    back to it (row_map), so all policies at one shop get the same wind value.
    A repeated location_id is rejected only if its coordinates DIFFER from the first row,
    because then we cannot know which position is correct.

    row_map has one entry per input row, in input order:
        ("loc", location_id)          -> row uses the wind of that unique location
        ("rejected", index_in_rejected) -> row could not be computed

    B needs only location_id, latitude, longitude. Coverage amounts and policy links stay with
    A/C/backend and are deliberately ignored here so sensitive data never enters B's logs.
    """
    lat_min, lat_max, lon_min, lon_max = config.coordinate_bounds   # unpack WGS84 bounds
    valid, rejected, warnings = [], [], []                          # three output buckets
    row_map: list[tuple] = []                                       # input row -> unique location or rejection
    first_coords: dict[str, tuple] = {}                             # location_id -> coords of first accepted row

    def _reject(entry: dict) -> None:                               # record a rejection and map the row to it
        rejected.append(entry)
        row_map.append(("rejected", len(rejected) - 1))

    for i, row in enumerate(exposure):                              # iterate in caller order (keeps output stable)
        loc_id = row.get("location_id")                             # unique key agreed with Part A
        lat, lon = row.get("latitude"), row.get("longitude")        # WGS84 decimal degrees

        if loc_id is None or str(loc_id).strip() == "":             # a row we cannot even identify
            _reject({"input_row": i, "location_id": None, "reason_code": REASON_UNMAPPED_EXPOSURE})
            continue
        loc_id = str(loc_id)                                        # normalise id type to string

        if not (_is_finite_number(lat) and _is_finite_number(lon)):  # missing or non-numeric coordinates
            _reject({"input_row": i, "location_id": loc_id, "reason_code": REASON_MISSING_COORDINATE})
            continue

        if not (lat_min <= float(lat) <= lat_max and lon_min <= float(lon) <= lon_max):  # out of WGS84 range
            _reject({"input_row": i, "location_id": loc_id, "reason_code": REASON_MISSING_COORDINATE})
            warnings.append(f"location {loc_id}: coordinate outside WGS84 bounds")
            continue

        coords = (float(lat), float(lon))
        if loc_id in first_coords:                                  # same shop listed again (e.g. second policy)
            if first_coords[loc_id] == coords:                      # same place -> reuse the one computed wind
                row_map.append(("loc", loc_id))
            else:                                                   # same id, different place -> conflict
                _reject({"input_row": i, "location_id": loc_id, "reason_code": REASON_DUPLICATE_LOCATION,
                         "detail": "same location_id with different coordinates"})
                warnings.append(f"location {loc_id}: repeated with conflicting coordinates")
            continue

        first_coords[loc_id] = coords                               # first time we see this shop
        valid.append({"location_id": loc_id, "latitude": coords[0], "longitude": coords[1]})  # minimal copy
        row_map.append(("loc", loc_id))

    if not valid:                                                   # nothing computable at all
        warnings.append("no valid exposure locations after validation")
    return valid, rejected, warnings, row_map


# ---------------------------------------------------------------------------------------
# 7. Input validation: track records (historical best tracks or bulletin positions)
# ---------------------------------------------------------------------------------------
# Expected record (one row per track position), following MODEL_HANDOFF section 3:
#   event_id, observation_time, latitude, longitude, max_sustained_wind, wind_unit,
#   wind_averaging_minutes, central_pressure_hpa, source, published_at
# Optional: radius_max_wind_nm, radius_oci_nm, environmental_pressure_hpa, name, basin,
#           quality_flag, data_version.
TRACK_REQUIRED_FIELDS = (
    "event_id", "observation_time", "latitude", "longitude",
    "max_sustained_wind", "wind_unit", "source", "published_at",
)


# Field names Part A (0.2.x) is known to emit, mapped to B's contract (review section 3.1 item 5).
TRACK_FIELD_ALIASES = {
    "averaging_period_min": "wind_averaging_minutes",
    "published_date": "published_at",
}
# Unit spellings mapped to B's accepted units. Only spellings, never a numeric conversion.
WIND_UNIT_ALIASES = {"kt": "kn", "kts": "kn", "knot": "kn", "knots": "kn",
                     "mps": "m/s", "m s-1": "m/s", "kph": "km/h", "kmh": "km/h"}
# Field names that may carry the reporting agency of a track row.
AGENCY_FIELDS = ("agency", "provider", "source_agency")


def normalize_track_records(raw_rows: list[dict], *, agency: Optional[str]) -> dict:
    """
    The A -> B boundary (review section 3.2 item 3). Pure, deterministic, no network.

    1. Renames A's field names to B's contract (TRACK_FIELD_ALIASES). If a row carries BOTH the
       alias and the B name with different values, the row is rejected, never silently resolved.
    2. Normalises unit SPELLINGS only (kt -> kn). No numeric conversion happens here.
    3. Keeps ONE agency. If the rows carry an agency field, `agency` must be given; rows from
       other agencies are dropped and counted. Rows with no agency field are rejected when an
       agency is required, because B cannot tell which agency produced them.
    Duplicate timestamps that remain after this step are NOT resolved here: B's validation
    rejects that event with `duplicate_track_time` rather than keeping the first row.

    Returns {"track_records": [...], "normalization_report": {...}}. Pass the report to
    compute_hazard_wind_field(data_manifest={"track_normalization": report, ...}).
    """
    rows = copy.deepcopy(raw_rows)                                 # never mutate A's output
    report: dict[str, Any] = {"n_input_rows": len(rows), "agency_selected": agency,
                              "fields_renamed": {}, "units_renamed": {}, "rejected_rows": [],
                              "dropped_by_agency": {}}
    has_agency = any(any(f in r for f in AGENCY_FIELDS) for r in rows)
    if has_agency and not agency:
        raise ValueError("track rows carry several agencies; pass agency='...' to select exactly one")

    kept = []
    for i, row in enumerate(rows):
        bad = False
        for alias, canonical in TRACK_FIELD_ALIASES.items():      # 1. field names
            if alias in row:
                if canonical in row and row[canonical] != row[alias]:
                    report["rejected_rows"].append({"row": i, "event_id": row.get("event_id"),
                                                    "reason_code": REASON_VERSION_CONFLICT,
                                                    "detail": f"{alias} and {canonical} disagree"})
                    bad = True
                    break
                row[canonical] = row.pop(alias)
                report["fields_renamed"][alias] = report["fields_renamed"].get(alias, 0) + 1
        if bad:
            continue
        unit = str(row.get("wind_unit", "")).strip()               # 2. unit spelling
        if unit.lower() in WIND_UNIT_ALIASES:
            row["wind_unit"] = WIND_UNIT_ALIASES[unit.lower()]
            report["units_renamed"][unit] = report["units_renamed"].get(unit, 0) + 1
        if agency:                                                 # 3. one agency only
            row_agency = next((row[f] for f in AGENCY_FIELDS if row.get(f) not in (None, "")), None)
            if row_agency is None:
                report["rejected_rows"].append({"row": i, "event_id": row.get("event_id"),
                                                "reason_code": REASON_MISSING_HAZARD,
                                                "detail": "agency not stated on row"})
                continue
            if str(row_agency).strip().lower() != agency.strip().lower():
                key = str(row_agency)
                report["dropped_by_agency"][key] = report["dropped_by_agency"].get(key, 0) + 1
                continue
        kept.append(row)
    report["n_output_rows"] = len(kept)
    return {"track_records": kept, "normalization_report": report}


def validate_track_records(
    track_records: list[dict], config: HazardConfig, *, as_of: str
) -> tuple[dict[str, list[dict]], list[dict], list[str]]:
    """
    Return (events, rejected, warnings), where `events` maps event_id -> sorted rows.

    Row-level rejections carry "row"; event-level rejections carry "scope": "event". Every
    rejection has an event_id (when known) and a reason_code, so Parts D and the orchestrator
    can count dropped storms without parsing warning text (review section 3.2 item 5).

    Rules: future data rejected; units and coordinates validated; every row must state its
    wind averaging period; non-1-minute winds are converted ONLY with an approved factor;
    an event with a missing central pressure or a duplicated timestamp is rejected whole.
    """
    as_of_dt = _parse_utc(as_of)                                   # the calculation cut-off
    if as_of_dt is None:                                           # without a cut-off we cannot guard leakage
        raise ValueError("as_of must be an ISO-8601 timestamp with time zone")

    events: dict[str, list[dict]] = {}                             # accepted rows grouped by event
    rejected: list[dict] = []                                      # refusals, with reasons
    warnings: list[str] = []                                       # human-readable notes

    def reject_row(i, row, code, detail=None):
        entry = {"row": i, "event_id": None if row.get("event_id") in (None, "") else str(row["event_id"]),
                 "reason_code": code}
        if detail:
            entry["detail"] = detail
        rejected.append(entry)

    for i, row in enumerate(track_records):                        # i = row index for traceability
        missing = [f for f in TRACK_REQUIRED_FIELDS if row.get(f) in (None, "")]  # required-field check
        if missing:
            reject_row(i, row, REASON_MISSING_HAZARD, f"missing fields: {missing}")
            continue

        obs_dt = _parse_utc(row["observation_time"])               # when the position was valid
        pub_dt = _parse_utc(row["published_at"])                   # when the information became public
        if obs_dt is None or pub_dt is None:                       # unparseable, date-only or naive timestamps
            reject_row(i, row, REASON_MISSING_HAZARD, "timestamp missing time zone or unparseable")
            continue
        if pub_dt > as_of_dt or obs_dt > as_of_dt:                 # information from after the cut-off
            reject_row(i, row, REASON_FUTURE_DATA)
            continue
        # NOTE: bulletin forecast positions have a valid time after publication. The replay
        # rule for forecasts is still open with the integration lead.

        unit = str(row["wind_unit"]).strip()                       # e.g. "kn", "m/s", "km/h"
        if unit not in ACCEPTED_WIND_UNITS:                        # unknown unit -> refuse, never guess
            reject_row(i, row, REASON_INVALID_UNIT, f"unit {unit!r}; normalise spellings first")
            continue

        lat, lon, wind = row["latitude"], row["longitude"], row["max_sustained_wind"]
        if not all(_is_finite_number(v) for v in (lat, lon, wind)) or float(wind) < 0:
            reject_row(i, row, REASON_MISSING_HAZARD, "non-numeric or negative position/wind")
            continue
        if not (-90.0 <= float(lat) <= 90.0 and -180.0 <= float(lon) < 360.0):  # CLIMADA allows lon > 180 near the antimeridian
            reject_row(i, row, REASON_INVALID_TRACK_COORD, f"lat={lat}, lon={lon}")
            continue

        avg = row.get("wind_averaging_minutes")                    # averaging period of the INPUT wind
        if avg in (None, ""):                                      # unknown basis -> cannot feed CLIMADA safely
            reject_row(i, row, REASON_UNAPPROVED_CONVERSION, "wind_averaging_minutes not stated")
            continue
        try:
            avg = int(avg)
        except (TypeError, ValueError):
            reject_row(i, row, REASON_UNAPPROVED_CONVERSION, f"wind_averaging_minutes={avg!r}")
            continue
        source_wind_mps = float(wind) * ACCEPTED_WIND_UNITS[unit]  # exact unit conversion to m/s
        conversion = None
        if avg != 1:                                               # CLIMADA expects 1-minute track winds
            rule = (config.input_wind_conversions or {}).get(str(avg))
            if not rule or rule.get("status") != "approved":
                reject_row(i, row, REASON_UNAPPROVED_CONVERSION,
                           f"{avg}-minute input wind and no approved conversion to 1-minute")
                continue
            conversion = {"from_minutes": avg, "factor_to_1min": float(rule["factor_to_1min"]),
                          "source": rule.get("source"), "status": "approved"}

        clean = dict(row)                                          # shallow copy; caller's row is untouched
        clean["_obs_dt"] = obs_dt                                  # cache parsed time for sorting
        clean["_source_wind_mps"] = source_wind_mps
        clean["_wind_mps"] = source_wind_mps * (conversion["factor_to_1min"] if conversion else 1.0)  # 1-minute basis
        clean["_conversion"] = conversion
        clean["_source_metric"] = f"{avg}-minute sustained"
        events.setdefault(str(row["event_id"]), []).append(clean)  # group by event

    for eid in sorted(events):                                     # event-level checks, deterministic order
        rows = sorted(events[eid], key=lambda r: r["_obs_dt"])     # chronological order is required by CLIMADA
        times = [r["_obs_dt"] for r in rows]
        if len(set(times)) != len(times):                          # two positions at one time -> ambiguous
            rejected.append({"scope": "event", "event_id": eid, "reason_code": REASON_DUPLICATE_TRACK_TIME,
                             "detail": "same observation_time appears twice; select one agency first"})
            del events[eid]
            continue
        pres = [r.get("central_pressure_hpa") for r in rows]
        if not all(_is_finite_number(p) and float(p) > 0 for p in pres):
            rejected.append({"scope": "event", "event_id": eid, "reason_code": REASON_MISSING_PRESSURE,
                             "detail": "Holland wind profiles need central pressure at every position"})
            # Text kept for Part D's adapter (from_part_b.B_DROPPED_WARNING), which parses it.
            warnings.append(f"event {eid}: missing central pressure; event cannot be modelled")
            del events[eid]
            continue
        if len(rows) < 2:                                          # a single point cannot form a track
            rejected.append({"scope": "event", "event_id": eid, "reason_code": REASON_TOO_FEW_POINTS,
                             "detail": "fewer than 2 valid track points"})
            del events[eid]
            continue
        events[eid] = rows
    return events, rejected, warnings


# ---------------------------------------------------------------------------------------
# 8. Explicit data adapter: IBTrACS through CLIMADA (the ONLY function that may download)
# ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TrackSelectionConfig:
    """How the historical event catalogue is chosen from IBTrACS (Part D's rules, 2026-10-21)."""

    provider: Optional[str] = None             # ONE agency, e.g. "usa". None is refused (Part D rule)
    basin: str = "WP"                          # storms with at least one position in the Western Pacific
    season_start: int = 1980                   # first IBTrACS SEASON kept (Part D)
    season_end: int = 2024                     # last IBTrACS SEASON always kept (Part D)
    conditional_season: Optional[int] = 2025   # kept only if ALL its non-spur storms are TRACK_TYPE "main"
    drop_spur_tracks: bool = True              # CLIMADA does not filter spurs; they double-count (Part D)
    rescale_windspeeds: bool = True            # CLIMADA default: rescale agency winds to 1-minute sustained
    time_step_h: float = 1.0                   # equal_timestep before perturbing (CLIMADA requires equal steps)
    nb_synth_tracks: int = 0                   # 0 = historical only; 9 = CLIMADA default ensemble size
    synth_seed: Optional[int] = None           # None = CLIMADA's configured default seed
    count_agency_drops: bool = True            # also load provider=None once, only to COUNT what one agency drops
    storm_ids: Optional[tuple] = None          # optional IBTrACS SIDs, e.g. ("2013306N07162",) to freeze one storm


def select_storms(storms: list[dict], sel: TrackSelectionConfig) -> tuple[set[str], dict]:
    """
    Pure selection logic (testable without CLIMADA).

    `storms` = [{"sid": str, "season": int, "track_type": str}, ...] for every storm loaded.
    Returns (sids_to_keep, report). Order of rules: drop spurs -> season window ->
    conditional season (all-or-nothing, so Part D's observation period stays a whole season).
    """
    report: dict[str, Any] = {"n_loaded": len(storms)}
    spur = sorted(s["sid"] for s in storms if "spur" in str(s["track_type"]).lower())
    report["spur_dropped"] = {"count": len(spur), "sids": spur} if sel.drop_spur_tracks else {"count": 0, "sids": []}
    pool = [s for s in storms if not (sel.drop_spur_tracks and s["sid"] in set(spur))]

    keep = {s["sid"] for s in pool if sel.season_start <= int(s["season"]) <= sel.season_end}
    outside = [s["sid"] for s in pool
               if not (sel.season_start <= int(s["season"]) <= sel.season_end)
               and int(s["season"]) != sel.conditional_season]
    report["outside_season_window"] = len(outside)

    if sel.conditional_season is not None:
        cond = [s for s in pool if int(s["season"]) == sel.conditional_season]
        not_main = sorted(s["sid"] for s in cond if str(s["track_type"]).strip().lower() != "main")
        include = bool(cond) and not not_main               # every storm of that season must be "main"
        if include:
            keep |= {s["sid"] for s in cond}
        report["conditional_season"] = {
            "season": sel.conditional_season, "n_storms": len(cond), "included": include,
            "not_main_sids": not_main,
            "reason": ("all storms are TRACK_TYPE main" if include else
                       "no storms found" if not cond else
                       f"{len(not_main)} storm(s) not TRACK_TYPE main (e.g. provisional)"),
        }
    seasons = sorted({int(s["season"]) for s in pool if s["sid"] in keep})
    report["seasons_used"] = seasons
    report["n_kept"] = len(keep)
    return keep, report


def load_ibtracs_track_records(sel: TrackSelectionConfig, *, retrieved_at: str) -> dict:
    """
    Read IBTrACS through CLIMADA (downloads the file on first use), apply Part D's selection
    rules, optionally add CLIMADA synthetic members, and flatten everything into B's frozen
    track-record contract. Must be called EXPLICITLY by the orchestrator, never implicitly.

    Returns {"track_records": [...], "selection_report": {...}}. Pass the report into
    compute_hazard_wind_field(data_manifest={"track_selection": report, ...}) so it is
    carried in B's manifest.

    Reproducibility note (CLIMADA 6.1 source, tc_tracks_synth._apply_decay_coeffs): the
    landfall-decay step draws from np.random.default_rng() WITHOUT a seed, so regenerating
    synthetic members does not give bit-identical tracks even with a fixed seed. Generate once,
    save the returned records, and reuse that frozen file; B's input_hash then pins the set.
    """
    if not isinstance(sel.provider, str) or not sel.provider:
        raise ValueError("TrackSelectionConfig.provider must be ONE agency string, e.g. 'usa' (Part D rule)")
    from climada.hazard import TCTracks                             # imported lazily: heavy dependency

    sid_years = (sel.season_start - 1, (sel.conditional_season or sel.season_end) + 1)  # SID year can differ from SEASON
    common = dict(basin=sel.basin, year_range=sid_years, rescale_windspeeds=sel.rescale_windspeeds,
                  storm_id=list(sel.storm_ids) if sel.storm_ids else None,
                  additional_variables=["season", "track_type"])   # non-agency IBTrACS variables -> track attrs
    tracks = TCTracks.from_ibtracs_netcdf(provider=sel.provider, **common)

    def _meta(trs):                                                 # per-storm metadata for select_storms
        return [{"sid": str(d.attrs["sid"]), "season": int(d.attrs["season"]),
                 "track_type": str(d.attrs["track_type"])} for d in trs.data]

    keep, report = select_storms(_meta(tracks), sel)

    if sel.count_agency_drops:                                      # how many storms does ONE agency lose?
        mixed = TCTracks.from_ibtracs_netcdf(provider=None, **common)   # used ONLY for counting, never modelled
        keep_mixed, _ = select_storms(_meta(mixed), sel)
        lost = sorted(keep_mixed - keep)
        report["single_agency_drop"] = {
            "provider": sel.provider, "n_storms_with_all_agencies": len(keep_mixed),
            "n_storms_with_this_agency": len(keep), "n_dropped": len(lost), "dropped_sids": lost,
        }

    tracks.data = [d for d in tracks.data if str(d.attrs["sid"]) in keep]
    tracks.equal_timestep(time_step_h=sel.time_step_h)              # required before perturbing
    members = sel.nb_synth_tracks + 1                               # members GENERATED per storm, incl. the real one
    if sel.nb_synth_tracks > 0:
        kwargs = {"nb_synth_tracks": sel.nb_synth_tracks}
        if sel.synth_seed is not None:
            kwargs["seed"] = sel.synth_seed
        tracks.calc_perturbed_trajectories(**kwargs)                # CLIMADA names copies "<sid>_gen<k>"
    report["members_generated_per_storm"] = members
    report["synthetic_generation_bit_reproducible"] = sel.nb_synth_tracks == 0

    records: list[dict] = []
    for ds in tracks.data:                                          # one xarray.Dataset per (real or synthetic) track
        sid = str(ds.attrs["sid"])
        track_id, _, gen = sid.partition("_gen")                    # "<sid>_gen3" -> ("<sid>", "3")
        unit = ds.attrs.get("max_sustained_wind_unit", "kn")
        for t in range(ds.sizes["time"]):
            records.append({
                "event_id": sid,
                "track_id": track_id,                               # the real storm this member belongs to
                "member_index": int(gen) if gen else 0,             # 0 = real track
                "members_generated": members,                       # counts members B may later drop (Part D)
                "season_year": int(ds.attrs["season"]),             # IBTrACS SEASON (copied to synthetic members)
                "track_type": str(ds.attrs["track_type"]),
                "name": str(ds.attrs.get("name", "")),
                "observation_time": np.datetime_as_string(ds.time.values[t], unit="s") + "+00:00",  # IBTrACS times are UTC
                "latitude": float(ds.lat.values[t]),
                "longitude": float(ds.lon.values[t]),
                "max_sustained_wind": float(ds.max_sustained_wind.values[t]),
                "wind_unit": unit,
                "wind_averaging_minutes": 1 if sel.rescale_windspeeds else None,   # CLIMADA rescales to 1-min
                "central_pressure_hpa": float(ds.central_pressure.values[t]),
                "environmental_pressure_hpa": float(ds.environmental_pressure.values[t]),
                "radius_max_wind_nm": float(ds.radius_max_wind.values[t]),
                "radius_oci_nm": float(ds.radius_oci.values[t]),
                "basin": str(ds.basin.values[t]) if "basin" in ds else sel.basin,
                "source": (f"IBTrACS v04r01 via CLIMADA {_climada_version()} provider={sel.provider}"
                           + (" synthetic(calc_perturbed_trajectories)" if gen else "")),
                # Best tracks are reanalysed after the event: their effective publication time is the
                # retrieval time, which prevents them being used in real-time bulletin replays.
                "published_at": retrieved_at,
                "data_version": "IBTrACS v04r01",
            })
    report["n_events"] = len(tracks.data)
    return {"track_records": records, "selection_report": report}


# ---------------------------------------------------------------------------------------
# 9. Convert validated rows into CLIMADA TCTracks (real engine only)
# ---------------------------------------------------------------------------------------
def _rows_to_climada_tracks(events: dict[str, list[dict]], config: HazardConfig):
    """Build a climada.hazard.TCTracks object from validated rows; returns (tracks, warnings)."""
    import xarray as xr                                             # CLIMADA track format is xarray-based
    from climada.hazard import TCTracks
    from climada.hazard import tc_tracks as tct                     # module with estimate_* helpers

    warnings: list[str] = []
    datasets = []
    for id_no, (eid, rows) in enumerate(sorted(events.items())):    # sorted => deterministic event order
        n = len(rows)
        times = np.array([np.datetime64(r["_obs_dt"].replace(tzinfo=None), "s") for r in rows])  # naive UTC times
        vmax_kn = np.array([r["_wind_mps"] / KNOT_TO_MPS for r in rows])                          # CLIMADA works in knots
        pres = np.array([float(r["central_pressure_hpa"]) for r in rows])   # hPa == mb; validated upstream
        # Missing environmental pressure: use CLIMADA's basin default (WP = 1005 hPa), the same
        # fallback CLIMADA's own IBTrACS reader applies, instead of the global 1010 hPa.
        basin_penv = [tct.BASIN_ENV_PRESSURE.get(str(r.get("basin", "")), tct.DEF_ENV_PRESSURE) for r in rows]
        penv = np.array([float(r.get("environmental_pressure_hpa") or bp) for r, bp in zip(rows, basin_penv)])
        if any(r.get("environmental_pressure_hpa") in (None, "") for r in rows):
            warnings.append(f"event {eid}: environmental pressure filled with CLIMADA basin default "
                            f"{sorted(set(basin_penv))} hPa (library default, pending confirmation)")
        rmw = np.array([float(r.get("radius_max_wind_nm") or 0.0) for r in rows])   # 0 => let CLIMADA estimate
        roci = np.array([float(r.get("radius_oci_nm") or 0.0) for r in rows])
        if (rmw <= 0).any() or (roci <= 0).any():
            rmw = tct.estimate_rmw(rmw, pres)                        # CLIMADA statistical estimate from pressure
            roci = tct.estimate_roci(roci, pres)
            warnings.append(f"event {eid}: radius_max_wind / radius_oci estimated by CLIMADA from pressure")
        dt_h = np.diff(times).astype("timedelta64[s]").astype(float) / 3600.0       # hours between points
        time_step = np.concatenate([[dt_h[0]], dt_h])               # CLIMADA convention: step to previous point

        ds = xr.Dataset(                                             # CLIMADA TCTracks dataset layout
            {
                "time_step": ("time", time_step),
                "radius_max_wind": ("time", rmw),
                "radius_oci": ("time", roci),
                "max_sustained_wind": ("time", vmax_kn),
                "central_pressure": ("time", pres),
                "environmental_pressure": ("time", penv),
                "basin": ("time", np.array([str(r.get("basin", "WP")) for r in rows], dtype="<U2")),
            },
            coords={
                "time": times,
                "lat": ("time", np.array([r["latitude"] for r in rows])),
                "lon": ("time", np.array([r["longitude"] for r in rows])),
            },
            attrs={
                "max_sustained_wind_unit": "kn",
                "central_pressure_unit": "mb",
                "name": str(rows[0].get("name") or eid),
                "sid": eid,
                "orig_event_flag": True,                             # historical/bulletin track, not synthetic
                "data_provider": str(rows[0]["source"]),
                "id_no": float(id_no),
                "category": int(tct.set_category(vmax_kn, wind_unit="kn")),  # Saffir-Simpson label used by CLIMADA
            },
        )
        datasets.append(ds)

    tracks = TCTracks(data=datasets)                                 # container CLIMADA expects
    if datasets:
        tracks.equal_timestep(time_step_h=config.track_time_step_h)  # interpolate to a uniform step
    return tracks, warnings


# ---------------------------------------------------------------------------------------
# 10. Hazard engines
# ---------------------------------------------------------------------------------------
class NetworkBlockedError(RuntimeError):
    """Raised when code inside the hazard engine tries to open a network connection."""


@contextmanager
def _no_network():
    """
    Block outbound network access while the engine runs and record every attempt.
    CLIMADA silently downloads data in some settings (e.g. ignore_distance_to_coast=False
    fetches a NASA distance-to-coast file). The handoff requires B's calculation to be offline,
    so any attempt becomes a structured failure instead of a hidden internet dependency.
    Both name lookup (getaddrinfo) and connect are blocked, because downloads fail at lookup first.
    Yields a list that collects the addresses that were attempted.
    """
    attempts: list[str] = []
    orig_connect, orig_lookup = socket.socket.connect, socket.getaddrinfo

    def refuse_connect(self, address, *args, **kwargs):              # replaces socket.connect temporarily
        attempts.append(str(address))
        raise NetworkBlockedError(f"network access attempted during hazard calculation: {address}")

    def refuse_lookup(host, *args, **kwargs):                        # replaces DNS lookup temporarily
        attempts.append(str(host))
        raise NetworkBlockedError(f"network access attempted during hazard calculation: {host}")

    socket.socket.connect, socket.getaddrinfo = refuse_connect, refuse_lookup
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.getaddrinfo = orig_connect, orig_lookup   # always restore


def _run_climada_engine(events, locations, config):
    """
    Real engine: ONE TropCyclone over ONE Centroids object -> shared wind field.
    Returns (intensity[n_events, n_locations] in m/s, event_ids, warnings, engine_meta).
    """
    from climada.hazard import Centroids, TropCyclone

    tracks, warnings = _rows_to_climada_tracks(events, config)      # validated rows -> TCTracks
    if not tracks.data:                                              # every event was unusable
        return np.zeros((0, len(locations))), [], warnings, {}

    centroids = Centroids(                                           # point centroids at merchant locations
        lat=np.array([l["latitude"] for l in locations]),
        lon=np.array([l["longitude"] for l in locations]),
    )
    haz = TropCyclone.from_tracks(                                   # documented CLIMADA call
        tracks, centroids,
        model=config.wind_model,
        ignore_distance_to_coast=config.ignore_distance_to_coast,
        intensity_thres=config.intensity_threshold_mps,
        max_dist_inland_km=config.max_dist_inland_km,
        max_dist_eye_km=config.max_dist_eye_km,
        store_windfields=False,                                      # we only need peak intensity per event
    )
    intensity = haz.intensity.toarray()                              # sparse (events x centroids) -> dense
    event_ids = [str(ds.attrs["sid"]) for ds in tracks.data]         # same order as hazard events
    meta = {"hazard_units": getattr(haz, "units", "m/s"),             # CLIMADA records the unit on the hazard
            "n_centroids": int(haz.intensity.shape[1])}
    return intensity, event_ids, warnings, meta


def _run_fixture_engine(events, locations, config):
    """
    CONTRACT-TEST engine only. Reads pre-agreed numbers from config.fixture_intensity.
    It contains no physics and its output must never be presented as model evidence.
    """
    event_ids = sorted(events)                                       # same deterministic ordering as real engine
    intensity = np.zeros((len(event_ids), len(locations)))
    for e, eid in enumerate(event_ids):
        for j, loc in enumerate(locations):
            v = config.fixture_intensity.get(f"{eid}|{loc['location_id']}", 0.0)
            intensity[e, j] = v if v >= config.intensity_threshold_mps else 0.0   # mimic CLIMADA thresholding
    meta = {"hazard_units": "m/s", "n_centroids": len(locations)}
    return intensity, event_ids, ["fixture engine used: results are test data, not model evidence"], meta


# ---------------------------------------------------------------------------------------
# 11. Public, product-facing function of Part B
# ---------------------------------------------------------------------------------------
def _dependency_versions() -> dict:
    """Installed versions of every package that can change B's numbers (review 3.1 item 3 / 3.2 item 6)."""
    from importlib.metadata import version, PackageNotFoundError
    out = {"python": platform.python_version()}
    for pkg in ("climada", "numpy", "scipy", "xarray", "pandas", "GDAL"):
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = "not_installed"
    return out


def _module_sha256() -> Optional[str]:
    """SHA-256 of this source file: identifies the exact code even without a git commit."""
    try:
        with open(__file__, "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def _event_track_hashes(events: dict[str, list[dict]]) -> dict[str, str]:
    """Hash each accepted, normalized track together with its source provenance."""
    hashes = {}
    for event_id in sorted(events):
        rows = []
        for row in events[event_id]:
            clean = {key: value for key, value in row.items() if key != "_obs_dt"}
            clean["_obs_dt"] = row["_obs_dt"].isoformat()
            rows.append(clean)
        hashes[event_id] = _canonical_hash({"event_id": event_id, "track_rows": rows})
    return hashes


def _event_intensity_hashes(event_ids: list[str], intensity: np.ndarray) -> dict[str, str]:
    """Hash the actual modeled intensity vector for each event at reporting precision."""
    return {
        event_id: _canonical_hash({
            "event_id": event_id,
            "intensity_mps": np.round(np.asarray(intensity[index], dtype=float), 6).tolist(),
        })
        for index, event_id in enumerate(event_ids)
    }


def _wind_field_id(config, locations, events, event_ids, intensity, *, event_track_hashes, event_intensity_hashes,
                   provenance_hash) -> Optional[str]:
    """
    Fingerprint of the ACTUAL wind field (review section 3.2 item 7): configuration, centroid
    coordinates, every track value fed to the engine, and the resulting intensity matrix.
    Changing any track position, wind or pressure therefore changes the id.
    """
    if not event_ids:
        return None
    track_values = {eid: [[r["_obs_dt"].isoformat(), float(r["latitude"]), float(r["longitude"]),
                           round(r["_wind_mps"], 6), float(r["central_pressure_hpa"]),
                           r.get("environmental_pressure_hpa"), r.get("radius_max_wind_nm"),
                           r.get("radius_oci_nm")] for r in events[eid]] for eid in event_ids}
    return _canonical_hash({
        "engine": config.engine, "config": asdict(config),
        "centroids": [[l["location_id"], l["latitude"], l["longitude"]] for l in locations],
        "tracks": track_values,
        "intensity_mps": np.round(intensity, 6).tolist(),
        "event_track_hashes": event_track_hashes,
        "event_intensity_hashes": event_intensity_hashes,
        "provenance_hash": provenance_hash,
    })


def compute_hazard_wind_field(
    exposure: list[dict],
    track_records: list[dict],
    config: HazardConfig,
    *,
    as_of: str,
    data_manifest: dict,
    run_id: Optional[str] = None,
    scenario_id: Optional[str] = None,
    scenario_weight_status: Optional[str] = None,
) -> dict:
    """
    Deterministic boundary for Part B. No network access, no database writes.

    Returns a dict containing:
      locations[]            -> one row per exposure row for the primary event (fills ModelRun.locations)
      event_location_winds[] -> every (event, location) pair, for Parts C and D
      events[], dropped_events[], rejected_track_rows[] -> what was modelled and what was not, and why
      wind_definition        -> what the numbers are: 1-minute sustained wind at 10 m, m/s
      coverage, validation, manifest -> counts, checks and full provenance

    `run_id` and `scenario_id` are inside the hashed result, so two scenarios or two runs can
    never share a hazard_version (manifest.output_hash) by accident.
    """
    exposure = copy.deepcopy(exposure)                               # never mutate caller data
    track_records = copy.deepcopy(track_records)
    data_manifest = copy.deepcopy(data_manifest) if data_manifest is not None else {}

    # ---- 11.1 hash of exactly what we received, INCLUDING upstream provenance ------------
    input_hash = _canonical_hash({"exposure": exposure, "tracks": track_records, "config": asdict(config),
                                  "as_of": as_of, "upstream_data_manifest": data_manifest,
                                  "run_id": run_id, "scenario_id": scenario_id,
                                  "module_version": MODULE_VERSION})

    # ---- 11.2 validation of inputs ------------------------------------------------------
    if config.wind_model not in CLIMADA_WIND_MODELS:                 # protect against typos in config
        raise ValueError(f"wind_model must be one of {CLIMADA_WIND_MODELS}")
    if config.centroid_granularity != "exposure_points":             # other modes not implemented yet
        raise NotImplementedError("only centroid_granularity='exposure_points' is implemented (others pending)")
    output_definition = _resolve_output_definition(config)
    out_conv = _output_conversion_metadata(config)
    out_conv_approved = out_conv["status"] == "approved"
    if out_conv_approved and output_definition["wind_metric_id"] != "sustained_1min_10m":
        raise ValueError("output conversion from 1-minute wind requires output metric sustained_1min_10m")
    provenance = {
        "upstream_data_manifest": data_manifest,
        "as_of": as_of,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "module_version": MODULE_VERSION,
    }
    provenance_hash = _canonical_hash(provenance)

    locations, rejected_locations, warn_exp, row_map = validate_exposure(exposure, config)
    events, rejected_tracks, warn_trk = validate_track_records(track_records, config, as_of=as_of)
    warnings = warn_exp + warn_trk                                   # accumulate all warnings
    event_track_hashes = _event_track_hashes(events)
    track_hash = _canonical_hash({"event_track_hashes": event_track_hashes})

    # ---- 11.3 run the engine ------------------------------------------------------------
    engine_error = None
    network_attempts: list[str] = []                                 # filled by the no-network guard
    intensity, event_ids, meta = np.zeros((0, len(locations))), [], {}
    if locations and events:                                         # only run if there is something to compute
        try:
            if config.engine == "climada":
                with _no_network() as network_attempts:              # offline guarantee (handoff section 5)
                    intensity, event_ids, warn_eng, meta = _run_climada_engine(events, locations, config)
            elif config.engine == "fixture":
                intensity, event_ids, warn_eng, meta = _run_fixture_engine(events, locations, config)
            else:
                raise ValueError(f"unknown engine {config.engine!r}")
            warnings += warn_eng
        except ImportError as exc:                                   # CLIMADA (or xarray) not installed
            engine_error = f"{REASON_ENGINE_UNAVAILABLE}: {exc}"
            intensity, event_ids, meta = np.zeros((0, len(locations))), [], {}
        except Exception as exc:                                     # network attempt or any engine failure
            code = REASON_NETWORK_BLOCKED if network_attempts else REASON_MISSING_HAZARD
            if network_attempts:
                exc = NetworkBlockedError(f"engine tried to reach {sorted(set(network_attempts))}; "
                                          "keep ignore_distance_to_coast=True or pre-install the data file")
            engine_error = f"{code}: {type(exc).__name__}: {str(exc)[:300]}"
            intensity, event_ids, meta = np.zeros((0, len(locations))), [], {}
    event_intensity_hashes = _event_intensity_hashes(event_ids, intensity)
    intensity_hash = _canonical_hash({"event_intensity_hashes": event_intensity_hashes})
    wind_field_id = _wind_field_id(
        config, locations, events, event_ids, intensity,
        event_track_hashes=event_track_hashes,
        event_intensity_hashes=event_intensity_hashes,
        provenance_hash=provenance_hash,
    )

    # Events that were offered but not modelled, as structured records (review 3.2 item 5).
    offered = {str(r.get("event_id")) for r in track_records if r.get("event_id") not in (None, "")}
    dropped_events = []
    for eid in sorted(offered - set(event_ids)):
        codes = sorted({r["reason_code"] for r in rejected_tracks if r.get("event_id") == eid})
        if not codes and engine_error:                               # engine failed: every event is dropped
            codes = [engine_error.split(":")[0]]
        first = next(r for r in track_records if str(r.get("event_id")) == eid)
        base, _, gen = eid.partition("_gen")
        dropped_events.append({
            "event_id": eid,
            "track_id": str(first.get("track_id") or base),
            "member_index": first.get("member_index", int(gen) if gen.isdigit() else 0),
            "members_generated": first.get("members_generated"),
            "season_year": first.get("season_year"),
            "reason_codes": codes or [REASON_MISSING_HAZARD],
        })

    # ---- 11.4 distance from each location to each event's track (coverage diagnostics) --
    dist_km = np.full((len(event_ids), len(locations)), np.inf)     # default: infinitely far
    if locations:
        lat_l = np.array([l["latitude"] for l in locations])         # location arrays for broadcasting
        lon_l = np.array([l["longitude"] for l in locations])
        for e, eid in enumerate(event_ids):
            rows = events[eid]
            lat_t = np.array([r["latitude"] for r in rows])[:, None]     # column vector of track points
            lon_t = np.array([r["longitude"] for r in rows])[:, None]
            dist_km[e] = _haversine_km(lat_t, lon_t, lat_l[None, :], lon_l[None, :]).min(axis=0)  # nearest track point
        # NOTE: distance uses the raw (not interpolated) track points, so it is a diagnostic,
        # not an input to the wind value. CLIMADA computes the wind on the interpolated track.

    # A supplied outer closed-isobar radius is the only defensible circulation boundary.
    # When it is absent we keep the distinction unknown and report only model-radius gaps.
    circulation_radius_km: dict[str, Optional[float]] = {}
    for eid in event_ids:
        radii = []
        for row in events[eid]:
            if _is_finite_number(row.get("radius_oci_km")) and float(row["radius_oci_km"]) > 0:
                radii.append(float(row["radius_oci_km"]))
            elif _is_finite_number(row.get("radius_oci_nm")) and float(row["radius_oci_nm"]) > 0:
                radii.append(float(row["radius_oci_nm"]) * 1.852)
        circulation_radius_km[eid] = max(radii) if radii else None

    # ---- 11.5 wind definition and optional approved output conversion --------------------
    def wind_fields(wind: Optional[float]) -> dict:
        """The explicit wind-definition fields attached to every reported wind value."""
        return {
            "wind_unit": output_definition["wind_unit"],
            "wind_metric": output_definition["wind_metric"],
            "wind_metric_id": output_definition["wind_metric_id"],
            "wind_averaging_minutes": output_definition["wind_averaging_minutes"],
            "averaging_period_min": output_definition["averaging_period_min"],
            "wind_height_m": output_definition["wind_height_m"],
            "height_m": output_definition["height_m"],
            "wind_speed_mps_converted": (round(wind * float(out_conv["factor_from_1min"]), 6)
                                         if out_conv_approved and wind is not None else None),
            "converted_metric": out_conv.get("target_metric") if out_conv_approved else None,
            "converted_metric_id": out_conv.get("target_metric_id") if out_conv_approved else None,
            "output_conversion_status": out_conv["status"],
            "output_conversion": copy.deepcopy(out_conv),
        }

    def classify(e: int, j: int) -> tuple[str, Optional[float], Optional[str]]:
        v = float(intensity[e, j])                                   # CLIMADA intensity in m/s
        if not math.isfinite(v) or v < 0:                            # corrupt numerical output
            return STATUS_NOT_COMPUTABLE, None, REASON_MISSING_HAZARD
        if v > 0:                                                    # a real modelled wind value
            return STATUS_OK, round(v, 6), None                      # 6 dp keeps hashes stable across platforms
        if dist_km[e, j] > config.max_dist_eye_km:                   # beyond CLIMADA's computation radius
            return STATUS_OUTSIDE_MODEL_RADIUS, None, REASON_COVERAGE_GAP
        circulation = circulation_radius_km.get(event_ids[e])
        if circulation is not None and dist_km[e, j] > circulation:
            return STATUS_OUTSIDE_CIRCULATION, None, None
        return STATUS_BELOW_THRESHOLD, None, None                    # computed, but below reporting threshold

    event_location_winds = []                                        # full matrix for Parts C and D
    for e, eid in enumerate(event_ids):
        for j, loc in enumerate(locations):
            status, wind, reason = classify(e, j)
            event_location_winds.append({
                "event_id": eid,
                "location_id": loc["location_id"],
                "wind_speed_mps": wind,                              # None when not a reportable number
                **wind_fields(wind),
                "data_status": status,
                "reason_code": reason,
                "distance_to_track_km": round(float(dist_km[e, j]), 3) if math.isfinite(dist_km[e, j]) else None,
                "storm_circulation_radius_km": circulation_radius_km.get(eid),
                "wind_field_id": wind_field_id,
                "track_hash": event_track_hashes.get(eid),
                "intensity_hash": event_intensity_hashes.get(eid),
                "provenance_hash": provenance_hash,
            })

    # ---- 11.6 single-storm view that fills ModelRun.locations[] --------------------------
    primary = config.primary_event_id or (event_ids[0] if len(event_ids) == 1 else None)
    if len(event_ids) > 1 and config.primary_event_id is None:
        warnings.append("multiple events and no primary_event_id: locations[] reports not_computable; "
                        "use event_location_winds[] for historical work")

    def source_info(eid: Optional[str]) -> dict:
        """Original track metric and any applied input conversion for one event."""
        if eid is None or eid not in events:
            return {
                "source_track_metric": None,
                "input_conversion_applied": None,
                "track_hash": None,
                "intensity_hash": None,
                "provenance_hash": provenance_hash,
                "storm_circulation_radius_km": None,
            }
        metrics = sorted({r["_source_metric"] for r in events[eid]})
        convs = [r["_conversion"] for r in events[eid] if r["_conversion"]]
        return {
            "source_track_metric": metrics[0] if len(metrics) == 1 else metrics,
            "input_conversion_applied": convs[0] if convs else None,
            "track_hash": event_track_hashes.get(eid),
            "intensity_hash": event_intensity_hashes.get(eid),
            "provenance_hash": provenance_hash,
            "storm_circulation_radius_km": circulation_radius_km.get(eid),
        }

    unique_rows: dict[str, dict] = {}                                # one computed row per unique shop
    for j, loc in enumerate(locations):
        if primary is not None and primary in event_ids:
            status, wind, reason = classify(event_ids.index(primary), j)
        else:                                                        # no single storm selected
            status, wind, reason = STATUS_NOT_COMPUTABLE, None, REASON_MISSING_HAZARD
        unique_rows[loc["location_id"]] = {
            "location_id": loc["location_id"],
            "wind_speed_mps": wind,
            **wind_fields(wind),
            **source_info(primary),
            "data_status": status,
            "reason_code": reason,
            "event_id": primary,
            "wind_field_id": wind_field_id,                          # identical for all rows => shared field
        }
    loc_rows = []                                                    # one output row per INPUT row, input order
    for i, (kind, ref) in enumerate(row_map):
        if kind == "loc":                                            # every policy at a shop gets the same wind
            loc_rows.append({"input_row": i, **unique_rows[ref]})
        else:                                                        # rejected rows stay visible, never dropped
            r = rejected_locations[ref]
            loc_rows.append({"input_row": i, "location_id": r["location_id"], "wind_speed_mps": None,
                             **wind_fields(None), **source_info(primary),
                             "data_status": STATUS_NOT_COMPUTABLE, "reason_code": r["reason_code"],
                             "event_id": primary, "wind_field_id": None})

    # ---- 11.7 coverage summary -----------------------------------------------------------
    coverage = {s: sum(1 for r in loc_rows if r["data_status"] == s)
                for s in (STATUS_OK, STATUS_BELOW_THRESHOLD, STATUS_OUTSIDE_CIRCULATION,
                          STATUS_OUTSIDE_MODEL_RADIUS, STATUS_NOT_COMPUTABLE)}
    coverage["n_rows_received"] = len(exposure)
    coverage["n_unique_locations"] = len(locations)
    coverage["n_events_offered"] = len(offered)
    coverage["n_events_modelled"] = len(event_ids)
    coverage["n_events_dropped"] = len(dropped_events)
    coverage["rejected_track_rows"] = len(rejected_tracks)

    # ---- 11.8 events metadata ------------------------------------------------------------
    def _event_field(eid, key, default=None):
        """Per-event metadata from the track rows; warns if rows of one event disagree."""
        vals = {r.get(key) for r in events[eid]} - {None}
        if len(vals) > 1:
            warnings.append(f"event {eid}: inconsistent {key} across track rows {sorted(map(str, vals))}")
        return next(iter(vals)) if len(vals) == 1 else default

    def _track_and_member(eid):
        tid, mem = _event_field(eid, "track_id"), _event_field(eid, "member_index")
        if tid is None or mem is None:                               # fall back to CLIMADA's "<sid>_gen<k>" naming
            base, _, gen = eid.partition("_gen")
            tid = tid if tid is not None else base
            mem = mem if mem is not None else (int(gen) if gen.isdigit() else 0)
        return str(tid), int(mem)

    events_meta = [{
        "event_id": eid,
        "track_id": _track_and_member(eid)[0],                       # real storm this event belongs to
        "member_index": _track_and_member(eid)[1],                   # 0 = real track, k = synthetic copy k
        "is_synthetic": _track_and_member(eid)[1] > 0,
        "members_generated": _event_field(eid, "members_generated"),  # None = not stated in input; never guessed
        "season_year": _event_field(eid, "season_year"),             # IBTrACS SEASON; None if the input lacks it
        "track_type": _event_field(eid, "track_type"),
        "name": str(events[eid][0].get("name", "")),
        "n_track_points": len(events[eid]),
        "first_observation": events[eid][0]["_obs_dt"].isoformat(),
        "last_observation": events[eid][-1]["_obs_dt"].isoformat(),
        "sources": sorted({str(r["source"]) for r in events[eid]}),
        **source_info(eid),
        "max_track_wind_1min_mps": round(max(r["_wind_mps"] for r in events[eid]), 6),
        "wind_unit": output_definition["wind_unit"],
        "wind_metric": output_definition["wind_metric"],
        "wind_metric_id": output_definition["wind_metric_id"],
        "averaging_period_min": output_definition["averaging_period_min"],
        "height_m": output_definition["height_m"],
        "output_conversion": copy.deepcopy(out_conv),
    } for eid in event_ids]

    # ---- 11.9 B's own validation checks ---------------------------------------------------
    checks = {
        "units_m_per_s": meta.get("hazard_units", "m/s") in ("m/s", "m s-1"),
        "no_negative_or_nan_wind": all(r["wind_speed_mps"] is None or r["wind_speed_mps"] >= 0
                                       for r in event_location_winds),
        "event_rows_have_explicit_wind_contract": all(
            r.get("wind_unit") == OUTPUT_WIND_UNIT
            and r.get("wind_metric_id") not in (None, "", PENDING)
            and _is_finite_number(r.get("averaging_period_min"))
            and float(r["averaging_period_min"]) > 0
            and _is_finite_number(r.get("height_m"))
            and float(r["height_m"]) > 0
            for r in event_location_winds
        ),
        "climada_default_is_sustained_1min_10m": (
            config.engine != "climada" or output_definition["wind_metric_id"] == "sustained_1min_10m"
        ),
        "shared_wind_field": len({r["wind_field_id"] for r in loc_rows if r["wind_field_id"]}) <= 1,
        "future_rows_rejected_count": sum(1 for r in rejected_tracks if r["reason_code"] == REASON_FUTURE_DATA),
        "every_input_location_returned": len(loc_rows) == len(exposure),
        # every modelled row was fed to the engine on a 1-minute basis (native, or approved conversion)
        "output_is_1min_sustained_10m": all(
            r["_source_metric"] == "1-minute sustained" or (r["_conversion"] or {}).get("status") == "approved"
            for eid in event_ids for r in events[eid]),
        "network_attempts_blocked": sorted(set(network_attempts)),
        "unapproved_conversions_rejected": sum(1 for r in rejected_tracks
                                               if r["reason_code"] == REASON_UNAPPROVED_CONVERSION),
    }
    if engine_error:
        status = "fail"
        warnings.append(engine_error)
    elif not checks["units_m_per_s"] or not checks["no_negative_or_nan_wind"] \
            or not checks["event_rows_have_explicit_wind_contract"] \
            or not checks["climada_default_is_sustained_1min_10m"] \
            or not checks["shared_wind_field"] or not checks["every_input_location_returned"] \
            or not checks["output_is_1min_sustained_10m"]:
        status = "fail"
    elif config.engine == "fixture":
        status = "warn"                                              # test data can never "pass"
    else:
        status = "warn"   # pending parameters are in use; handoff forbids claiming "pass" until confirmed
        warnings.append("validation thresholds and key parameters are pending_confirmation; status capped at warn")
    if dropped_events:
        warnings.append(f"{len(dropped_events)} event(s) not modelled; see dropped_events[]")

    # ---- 11.10 assemble output; the manifest is INSIDE the output hash -------------------
    result = {
        "module": MODULE_NAME,
        "module_version": MODULE_VERSION,
        "run_id": run_id,
        "scenario_id": scenario_id,
        "scenario_weight_status": scenario_weight_status,
        "as_of": as_of,
        "wind_unit": output_definition["wind_unit"],
        "wind_metric": output_definition["wind_metric"],
        "wind_metric_id": output_definition["wind_metric_id"],
        "averaging_period_min": output_definition["averaging_period_min"],
        "height_m": output_definition["height_m"],
        "wind_definition": dict(output_definition, output_conversion=copy.deepcopy(out_conv)),
        "locations": loc_rows,
        "event_location_winds": event_location_winds,
        "events": events_meta,
        "dropped_events": dropped_events,
        "coverage": coverage,
        "rejected_track_rows": rejected_tracks,
        "track_hash": track_hash,
        "event_track_hashes": event_track_hashes,
        "intensity_hash": intensity_hash,
        "event_intensity_hashes": event_intensity_hashes,
        "provenance": provenance,
        "provenance_hash": provenance_hash,
        "validation": {"status": status, "checks": checks, "warnings": sorted(set(warnings))},
    }
    result["manifest"] = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "as_of": as_of,
        "module_version": MODULE_VERSION,
        "module_sha256": _module_sha256(),
        "input_hash": input_hash,
        "config": asdict(config),
        "config_version": config.config_version,
        "config_hash": _canonical_hash(asdict(config)),
        "engine": config.engine,
        "climada_version": _climada_version(),
        "dependency_versions": _dependency_versions(),
        "random_seed": None,                                         # B's wind calculation draws no random numbers
        "pending_parameters": PENDING_PARAMETERS,
        "evidence_register_b": EVIDENCE_REGISTER_B,
        "upstream_data_manifest": data_manifest,                     # passed through from orchestrator / Part A
        "provenance": provenance,
        "provenance_hash": provenance_hash,
        "track_hash": track_hash,
        "event_track_hashes": event_track_hashes,
        "intensity_hash": intensity_hash,
        "event_intensity_hashes": event_intensity_hashes,
        "wind_unit": output_definition["wind_unit"],
        "wind_metric": output_definition["wind_metric"],
        "wind_metric_id": output_definition["wind_metric_id"],
        "averaging_period_min": output_definition["averaging_period_min"],
        "height_m": output_definition["height_m"],
        "wind_definition": dict(output_definition, output_conversion=copy.deepcopy(out_conv)),
        "output_conversion": copy.deepcopy(out_conv),
        "future_data_check": "rejected" if checks["future_rows_rejected_count"] else "clean",
        "wind_field_id": wind_field_id,
        "seasons_used": sorted({e["season_year"] for e in events_meta if e["season_year"] is not None}),
        "members_generated_by_track": {e["track_id"]: e["members_generated"] for e in events_meta},
        "track_selection": data_manifest.get("track_selection"),     # from load_ibtracs_track_records
        "track_normalization": data_manifest.get("track_normalization"),  # from normalize_track_records
    }
    # hazard_version = output_hash. It covers results AND the full manifest (review 3.2 item 6),
    # so any change in source provenance, code, dependencies or config gives a new version.
    result["manifest"]["output_hash"] = _canonical_hash(result)
    return result


# ---------------------------------------------------------------------------------------
# 12. Thin adapters matching the three proposed package interfaces (MODEL_HANDOFF section 4)
# ---------------------------------------------------------------------------------------
def bulletin_to_track_records(bulletin_snapshot: dict) -> list[dict]:
    """
    Flatten a frozen bulletin snapshot into B's track-record contract.
    Expected snapshot keys: storm_id, bulletin_id, published_at, positions[] with
    valid_time, latitude, longitude, max_sustained_wind, wind_unit, wind_averaging_minutes,
    central_pressure_hpa. PAGASA bulletins use 10-minute winds: such events are modelled only
    once an input conversion to 1-minute is approved in HazardConfig.input_wind_conversions.
    The parser that produces this snapshot belongs to the backend/Part A, not Part B.
    """
    rows = []
    for p in bulletin_snapshot.get("positions", []):
        rows.append({
            "event_id": f"{bulletin_snapshot['storm_id']}#{bulletin_snapshot['bulletin_id']}",
            "name": bulletin_snapshot.get("storm_name", ""),
            "observation_time": p.get("valid_time"),
            "latitude": p.get("latitude"),
            "longitude": p.get("longitude"),
            "max_sustained_wind": p.get("max_sustained_wind"),
            "wind_unit": p.get("wind_unit"),
            "wind_averaging_minutes": p.get("wind_averaging_minutes"),
            "central_pressure_hpa": p.get("central_pressure_hpa"),
            "source": bulletin_snapshot.get("source", "bulletin"),
            "published_at": bulletin_snapshot.get("published_at"),
            "data_version": bulletin_snapshot.get("parser_version", PENDING),
        })
    return rows


def hazard_for_storm_scenarios(bulletin_snapshot, exposure, config, *, as_of, data_manifest,
                               run_id=None, scenario_id=None) -> dict:
    """B's contribution to compute_storm_scenarios(): one shared wind field for one bulletin.
    Scenario perturbations and weights are NOT produced here, so the scenario is 'unweighted'.
    scenario_id defaults to '<storm_id>#<bulletin_id>#base' and is part of the hashed result."""
    rows = bulletin_to_track_records(bulletin_snapshot)
    sid = scenario_id or f"{bulletin_snapshot.get('storm_id')}#{bulletin_snapshot.get('bulletin_id')}#base"
    return compute_hazard_wind_field(exposure, rows, config, as_of=as_of, data_manifest=data_manifest,
                                     run_id=run_id, scenario_id=sid, scenario_weight_status="unweighted")


def hazard_for_historical_risk(exposure, historical_events, config, *, as_of, data_manifest, run_id=None) -> dict:
    """B's contribution to compute_historical_risk(): event x location winds for Part D.
    B returns no frequency or probability - those belong to Part D."""
    return compute_hazard_wind_field(exposure, historical_events, config, as_of=as_of,
                                     data_manifest=data_manifest, run_id=run_id)
