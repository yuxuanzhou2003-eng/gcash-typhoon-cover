"""Role C - trigger tier and modeled payout basis (v0.2).

Pipeline place: A (exposure) -> B (wind) -> **C (this module)** -> D (historical risk)

Inputs (all frozen, passed in by the orchestrator):
  exposure_input : Role A's output. Each exposure row needs
                   exposure_id, location_id, coverage_start, coverage_end,
                   daily_collection_basis_php  (BUSINESS_RULES s5; A supplies the definition)
                   contractual_coverage_limit_php (optional; passed through, never recalculated)
  hazard_output  : Role B's output (event_location_winds[], events[], manifest, validation)
  rule_config    : one candidate rule config (rules.py)

Payout arithmetic (BUSINESS_RULES s5):
  gross_benefit_basis  = daily_collection_basis x benefit_days(triggered tier)
  modeled_payout_basis = min(gross_benefit_basis, per_event_cap)   [cap only if configured]
  Full precision internally; rounding applied once at the output boundary.

Boundaries (MODEL_HANDOFF s2, BUSINESS_RULES s5):
- Pure function: no network, LLM, clock, file or database access.
- Does NOT apply eligibility, effective dates, waiting periods, final or aggregate
  limits, contractual limits or duplicate-event handling. Those are backend decisions;
  C only flags them.
- Never outputs a payout number when an input or rule is missing.
- Outputs are modeling hypotheses under an experimental candidate, not policy terms.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Optional

from .rules import config_hash, pending_fields, validate_rule_config

MODULE_NAME = "climada_model.trigger_payout_c"
MODULE_VERSION = "0.2.0"

# Reason codes from MODEL_HANDOFF s3 ...
R_MISSING_HAZARD = "missing_hazard"
R_COVERAGE_GAP = "coverage_gap"
R_VERSION = "version_conflict"
R_FUTURE = "future_data_rejected"
R_UNIT = "invalid_unit"
R_UPSTREAM_FAILED = "upstream_validation_failed"
R_MISSING_FIELD = "missing_field"
R_DUPLICATE = "duplicate_event_row"
# ... plus Role C specific codes
R_RULE_PENDING = "rule_pending_confirmation"
R_METRIC = "wind_metric_mismatch"
R_OUTSIDE_WINDOW = "outside_coverage_window"           # computed: event outside policy window -> tier 0
R_REPORTING_FLOOR = "reporting_threshold_above_lowest_tier"
R_MISSING_COLLECTIONS = "missing_daily_collection_basis"

# Flags: information for the backend. C never resolves these.
F_DUP_EVENT = "duplicate_event_row"
F_POSSIBLE_DUP_STORM = "possible_duplicate_storm"
F_STRADDLE = "event_straddles_window_peak_time_unknown"
F_MULTI_TRIGGER = "multiple_triggers_for_policy"
F_STARTS_AFTER_AS_OF = "coverage_starts_after_as_of"
F_ABOVE_CONTRACT_LIMIT = "modeled_above_contractual_limit"
F_CAP_APPLIED = "per_event_cap_applied"
F_TEST_FIXTURE = "test_fixture_rules_not_product_evidence"
F_EXPERIMENTAL = "experimental_candidate_not_policy_term"

# Role B data_status values (v0.1.0 plus the split agreed for v0.1.1+)
B_OK = "ok"
B_BELOW = "below_reporting_threshold"
B_OUTSIDE_LEGACY = "outside_storm_reach"
B_OUTSIDE_CIRCULATION = "outside_storm_circulation"    # agreed: no payout
B_OUTSIDE_RADIUS = "outside_model_radius"              # agreed: unknown
B_NOT_COMPUTABLE = "not_computable"


def _hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _utc(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else None


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _mapping(value: Any) -> dict:
    """Return a mapping or an empty mapping for malformed upstream sections."""
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list:
    """Return only mapping rows, leaving malformed rows to a warning at the call site."""
    return value if isinstance(value, list) else []


def _identifier(value: Any) -> Optional[str]:
    """Canonical identifier used for indexing while retaining None for missing IDs."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value)
    return text if text else None


def _stable_row(row: Any) -> str:
    """Stable ordering key for duplicate rows with otherwise arbitrary values."""
    return json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)


def _wind_for_rule(row: dict, rule_metric: str) -> tuple[Any, Optional[str]]:
    """Select B's explicitly identified native or approved converted wind."""
    metric_id = row.get("wind_metric_id")
    if not metric_id:
        metric = row.get("wind_metric")
        averaging = row.get("wind_averaging_minutes")
        height = row.get("wind_height_m")
        if metric == "max_sustained_wind" and averaging == 1 and height == 10:
            metric_id = "sustained_1min_10m"
        else:
            metric_id = metric
    if metric_id == rule_metric:
        return row.get("wind_speed_mps"), metric_id

    converted_id = row.get("converted_metric_id")
    if not converted_id:
        converted = str(row.get("converted_metric") or "").strip().lower()
        if converted in {"10-minute sustained wind at 10 m", "10 minute sustained wind at 10 m"}:
            converted_id = "sustained_10min_10m"
        elif converted in {"1-minute sustained wind at 10 m", "1 minute sustained wind at 10 m"}:
            converted_id = "sustained_1min_10m"
    if row.get("output_conversion_status") == "approved" and converted_id == rule_metric:
        return row.get("wind_speed_mps_converted"), converted_id
    return None, metric_id


def _recurring_window(c_start: datetime, c_end: datetime, event_start: datetime,
                      event_end: datetime, offset_hours: float) -> Optional[tuple[datetime, datetime]]:
    """Return the recurring local-calendar window overlapping an event.

    Coverage dates from A are literal UTC instants by default.  This helper is
    used only when the rule explicitly opts into ``recurring_month_day``.  The
    start/end month-day and local times are taken from the policy dates, then
    rebuilt in the event year using the configured fixed offset (for example,
    +8 for Philippine local dates).  A window whose end month-day precedes its
    start wraps into the following year.
    """
    if not all(isinstance(x, datetime) for x in (c_start, c_end, event_start, event_end)):
        return None
    try:
        tz = timezone(timedelta(hours=float(offset_hours)))
    except (TypeError, ValueError, OverflowError):
        return None
    start_local = c_start.astimezone(tz)
    end_local = c_end.astimezone(tz)
    event_start_local = event_start.astimezone(tz)
    event_end_local = event_end.astimezone(tz)
    start_key = (start_local.month, start_local.day, start_local.hour, start_local.minute,
                 start_local.second, start_local.microsecond)
    end_key = (end_local.month, end_local.day, end_local.hour, end_local.minute,
               end_local.second, end_local.microsecond)
    wraps_year = end_key < start_key or (
        end_key == start_key and end_local.date() > start_local.date()
    )

    def build(source: datetime, year: int) -> datetime:
        # February 29 is a valid policy boundary in leap years.  For a
        # non-leap target year, use February 28 rather than crashing; the
        # boundary remains explicit in provenance and is conservative.
        day = source.day
        if source.month == 2 and day == 29:
            try:
                datetime(year, 2, 29)
            except ValueError:
                day = 28
        return datetime(year, source.month, day, source.hour, source.minute,
                        source.second, source.microsecond, tzinfo=tz)

    for year in range(event_start_local.year - 1, event_end_local.year + 2):
        window_start_local = build(start_local, year)
        window_end_local = build(end_local, year + (1 if wraps_year else 0))
        window_start = window_start_local.astimezone(timezone.utc)
        window_end = window_end_local.astimezone(timezone.utc)
        if event_start <= window_end and window_start <= event_end:
            return window_start, window_end
    return None


def select_tier(wind_mps: float, tiers: list[dict], boundary: str) -> int:
    """Highest tier whose threshold is met. 0 = no tier met."""
    hit = 0
    for t in tiers:                                   # validated strictly increasing
        th = t["threshold_mps"]
        if (wind_mps >= th) if boundary == "inclusive" else (wind_mps > th):
            hit = t["tier"]
    return hit


def _round(amount: Decimal, rounding: dict) -> float:
    unit = Decimal(str(rounding["unit_php"]))
    mode = ROUND_HALF_UP if rounding["mode"] == "half_up" else ROUND_DOWN
    return float((amount / unit).quantize(Decimal("1"), rounding=mode) * unit)


def benefit_amounts(daily_collection_basis: float, benefit_days: int, cap: Optional[float],
                    rounding: dict) -> tuple[float, float, bool]:
    """Return (gross_benefit_basis, modeled_payout_basis, cap_applied), rounded at the output boundary."""
    gross = Decimal(str(daily_collection_basis)) * Decimal(benefit_days)       # full precision
    capped = min(gross, Decimal(str(cap))) if cap is not None else gross
    return _round(gross, rounding), _round(capped, rounding), capped < gross


def _row(exp, event_id, wind, b_status, *, tier, gross, payout, computable, reason,
         detail=None, flags=(), peak_time=None):
    exp = _mapping(exp)
    return {
        "event_id": event_id,
        "exposure_id": _identifier(exp.get("exposure_id")),
        "coverage_id": exp.get("coverage_id"),
        "location_id": _identifier(exp.get("location_id")),
        "wind_speed_mps": wind,
        "peak_time_utc": peak_time,
        "hazard_data_status": b_status,
        "computable": computable,
        "trigger_tier": tier,                         # int >= 0 when computable; None when not
        "daily_collection_basis_php": exp.get("daily_collection_basis_php"),
        "gross_benefit_basis_php": gross,             # before cap; None when not computable
        "modeled_payout_basis_php": payout,           # after model-side cap; None when not computable
        "contractual_coverage_limit_php": exp.get("contractual_coverage_limit_php"),  # pass-through only
        "reason_code": reason,
        "detail": detail,
        "flags": sorted(set(flags)),
    }


def compute_trigger_basis(exposure_input: dict, hazard_output: dict, rule_config: dict, *, as_of: str) -> dict:
    """Apply one candidate rule config to A's exposure and B's wind results."""
    exposure_is_mapping = isinstance(exposure_input, dict)
    hazard_is_mapping = isinstance(hazard_output, dict)
    exposure_input = copy.deepcopy(exposure_input) if exposure_is_mapping else {}
    hazard_output = copy.deepcopy(hazard_output) if hazard_is_mapping else {}
    rule_config = copy.deepcopy(rule_config)
    validate_rule_config(rule_config)

    warnings: list[str] = []
    run_reason: Optional[str] = None
    run_detail: Optional[str] = None
    cutoff = _utc(as_of)
    if cutoff is None:
        raise ValueError("as_of must be ISO 8601 with time zone")

    if not exposure_is_mapping:
        warnings.append("exposure input is not an object; treated as empty")
    if not hazard_is_mapping:
        warnings.append("hazard input is not an object; treated as empty")

    # ---- 1. run-level checks -------------------------------------------------------------
    rv = rule_config["rule_version"]
    if exposure_input.get("rule_version") != rv:
        run_reason, run_detail = R_VERSION, (
            f"rule config {rv!r} != exposure rule_version {exposure_input.get('rule_version')!r}")
    for name, src in (("exposure", exposure_input), ("hazard", hazard_output)):
        t = _utc(src.get("as_of"))
        if t is None:
            warnings.append(f"{name} output has no parseable as_of")
        elif t > cutoff and run_reason is None:
            run_reason, run_detail = R_FUTURE, f"{name} as_of {src.get('as_of')} > {as_of}"
        elif t != cutoff:
            warnings.append(f"{name} as_of {src.get('as_of')} differs from run as_of {as_of}")
    hazard_validation = _mapping(hazard_output.get("validation"))
    hazard_manifest = _mapping(hazard_output.get("manifest"))
    hazard_validation_status = hazard_validation.get("status")
    future_data_check = hazard_manifest.get("future_data_check")
    # A failed B run or an explicit future-row rejection invalidates every C row.
    # Keep future_data_rejected as the reason when both signals are present because
    # it is the more specific handoff reason used by A/B/D.
    if future_data_check == "rejected":
        run_reason, run_detail = R_FUTURE, "hazard manifest future_data_check=rejected"
    elif hazard_validation_status == "fail":
        run_reason, run_detail = R_UPSTREAM_FAILED, "hazard validation status=fail"
    units_check = _mapping(hazard_validation.get("checks")).get("units_m_per_s", False)
    if not units_check and run_reason is None:
        run_reason, run_detail = R_UNIT, "hazard output does not confirm m/s units"
    if hazard_validation_status == "fail":
        warnings.append("hazard validation status=fail; no payout rows are computable")
    if future_data_check == "rejected":
        warnings.append("hazard manifest future_data_check=rejected; no payout rows are computable")

    missing_rules = pending_fields(rule_config)
    if missing_rules:
        warnings.append(f"rules pending_confirmation: {', '.join(missing_rules)}")
    status_label = rule_config["rule_status"]
    base_flags = {"test_fixture": [F_TEST_FIXTURE], "experimental": [F_EXPERIMENTAL]}.get(status_label, [])
    if status_label == "test_fixture":
        warnings.append("TEST FIXTURE rule set: outputs are not product evidence")
    if status_label == "experimental":
        warnings.append("experimental candidate: modeling hypothesis, not an approved policy term")

    hazard_config = _mapping(hazard_manifest.get("config"))
    reporting_floor = hazard_config.get("intensity_threshold_mps")
    output_conversion = _mapping(hazard_config.get("output_wind_conversion"))
    if (output_conversion.get("status") == "approved"
            and output_conversion.get("target_metric_id") == rule_config.get("wind_metric")
            and _num(reporting_floor) and _num(output_conversion.get("factor_from_1min"))):
        reporting_floor = float(reporting_floor) * float(output_conversion["factor_from_1min"])
    lowest = rule_config["tiers"][0]["threshold_mps"] if not missing_rules else None
    cap = rule_config.get("per_event_cap_php")

    # ---- 2. index hazard rows and events ---------------------------------------------------
    # Index only well-shaped rows.  Duplicate keys are retained separately and
    # invalidated below; selecting the first row would make results depend on
    # upstream list ordering (and could price an arbitrary wind value).
    events: dict[str, dict] = {}
    event_rows: dict[str, list[dict]] = {}
    malformed_event_rows = 0
    raw_events = hazard_output.get("events", [])
    if not isinstance(raw_events, list):
        warnings.append("hazard events is not a list; no event rows indexed")
        raw_events = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            malformed_event_rows += 1
            continue
        eid = _identifier(raw.get("event_id"))
        if eid is None:
            malformed_event_rows += 1
            continue
        event_rows.setdefault(eid, []).append(raw)
    duplicate_event_ids = {eid for eid, rows in event_rows.items() if len(rows) > 1}
    for eid, rows in event_rows.items():
        # Canonicalize even malformed duplicate event metadata so list order
        # cannot affect timestamps or duplicate-storm detection.
        event = copy.deepcopy(min(rows, key=_stable_row))
        event["event_id"] = eid
        events[eid] = event
    if malformed_event_rows:
        warnings.append(f"ignored {malformed_event_rows} malformed hazard event row(s)")
    if duplicate_event_ids:
        warnings.append(f"duplicate event catalogue row(s): {sorted(duplicate_event_ids)}")

    by_loc: dict[tuple[str, str], dict] = {}
    by_exp: dict[tuple[str, str], dict] = {}             # B window-max rows keyed by exposure_id
    loc_rows: dict[tuple[str, str], list[dict]] = {}
    exp_rows: dict[tuple[str, str], list[dict]] = {}
    malformed_wind_rows = 0
    raw_winds = hazard_output.get("event_location_winds", [])
    if not isinstance(raw_winds, list):
        warnings.append("hazard event_location_winds is not a list; no wind rows indexed")
        raw_winds = []
    for raw in raw_winds:
        if not isinstance(raw, dict):
            malformed_wind_rows += 1
            continue
        eid = _identifier(raw.get("event_id"))
        exposure_id = _identifier(raw.get("exposure_id"))
        location_id = _identifier(raw.get("location_id"))
        if eid is None or (exposure_id is None and location_id is None):
            malformed_wind_rows += 1
            continue
        if exposure_id is not None:
            exp_rows.setdefault((eid, exposure_id), []).append(raw)
        else:
            loc_rows.setdefault((eid, location_id), []).append(raw)
    duplicate_exp_keys = {key for key, rows in exp_rows.items() if len(rows) > 1}
    duplicate_loc_keys = {key for key, rows in loc_rows.items() if len(rows) > 1}
    for key, rows in exp_rows.items():
        by_exp[key] = min(rows, key=_stable_row)
    for key, rows in loc_rows.items():
        by_loc[key] = min(rows, key=_stable_row)
    if malformed_wind_rows:
        warnings.append(f"ignored {malformed_wind_rows} malformed hazard wind row(s)")
    dup_keys = {(eid, "exp", exposure_id) for eid, exposure_id in duplicate_exp_keys}
    dup_keys |= {(eid, "loc", location_id) for eid, location_id in duplicate_loc_keys}
    if dup_keys:
        warnings.append(f"duplicate hazard event-location/exposure row(s): {sorted(dup_keys)}")
    possible_dups = set()
    ev = sorted(events.values(), key=lambda e: e["event_id"])
    for i, a in enumerate(ev):
        for b in ev[i + 1:]:
            a_start, a_end = _utc(a.get("first_observation")), _utc(a.get("last_observation"))
            b_start, b_end = _utc(b.get("first_observation")), _utc(b.get("last_observation"))
            if a.get("name") and a.get("name") == b.get("name") and \
                    a_start is not None and a_end is not None and b_start is not None and b_end is not None and \
                    a_start <= b_end and b_start <= a_end:
                possible_dups |= {a["event_id"], b["event_id"]}
    if possible_dups:
        warnings.append(f"possible duplicate storms (flagged only): {sorted(possible_dups)}")

    raw_exposures = exposure_input.get("exposure", [])
    if not isinstance(raw_exposures, list):
        warnings.append("exposure is not a list; no exposure rows indexed")
        raw_exposures = []
    exposures: list[dict] = []
    malformed_exposure_rows = 0
    for raw in raw_exposures:
        if isinstance(raw, dict):
            exposures.append(raw)
        else:
            malformed_exposure_rows += 1
            # Keep a structural placeholder so every event still has a
            # deterministic non-computable row for the malformed exposure.
            exposures.append({"_malformed": True})
    exposures.sort(key=lambda r: (_identifier(r.get("exposure_id")) or "", _stable_row(r)))
    if malformed_exposure_rows:
        warnings.append(f"ignored {malformed_exposure_rows} malformed exposure row(s)")
    event_ids = sorted(events)
    window_rule = rule_config.get("coverage_window_rule")
    # Literal instants preserve the live-storm behavior.  Historical pricing
    # must opt in explicitly to recurring month-day policy windows.
    window_mode = rule_config.get("coverage_window_mode", "literal")
    window_offset_hours = rule_config.get("coverage_window_timezone_offset_hours", 0)

    # ---- 3. evaluate every (event, policy) pair ---------------------------------------------
    results = []
    for eid in event_ids:
        e = _mapping(events.get(eid))
        e_start, e_end = _utc(e.get("first_observation")), _utc(e.get("last_observation"))
        for exp in exposures:
            flags = list(base_flags)
            exp_id = _identifier(exp.get("exposure_id"))
            location_id = _identifier(exp.get("location_id"))
            c_start, c_end = _utc(exp.get("coverage_start")), _utc(exp.get("coverage_end"))
            if c_start and c_start > cutoff:
                flags.append(F_STARTS_AFTER_AS_OF)
            if eid in possible_dups:
                flags.append(F_POSSIBLE_DUP_STORM)
            duplicate_key = None
            has_exp_row = exp_id is not None and (eid, exp_id) in by_exp
            if window_rule == "hazard_window_max" or has_exp_row:
                duplicate_key = (eid, "exp", exp_id)
                w = by_exp.get((eid, exp_id)) if exp_id is not None else None
                if duplicate_key in dup_keys:
                    flags.append(F_DUP_EVENT)
            else:
                duplicate_key = (eid, "loc", location_id)
                w = by_loc.get((eid, location_id)) if location_id is not None else None
                if duplicate_key in dup_keys:
                    flags.append(F_DUP_EVENT)
            duplicate_row = duplicate_key in dup_keys or eid in duplicate_event_ids
            # A row carrying both identifiers is invalid if either identity is
            # duplicated, regardless of which lookup key the rule uses.
            duplicate_row = duplicate_row or (
                exp_id is not None and (eid, "exp", exp_id) in dup_keys
            ) or (
                location_id is not None and (eid, "loc", location_id) in dup_keys
            )
            if duplicate_row and F_DUP_EVENT not in flags:
                flags.append(F_DUP_EVENT)
            if duplicate_row:
                # Do not expose either competing wind value.  This makes the
                # result safe and identical regardless of which duplicate was
                # first in the upstream list.
                w = None
                wind = None
                b_status = R_DUPLICATE
                peak = None
            else:
                wind, selected_metric = (_wind_for_rule(w, rule_config["wind_metric"])
                                         if isinstance(w, dict) else (None, None))
                b_status = w.get("data_status") if isinstance(w, dict) else None
                peak = w.get("peak_time_utc") if isinstance(w, dict) else None

            def nc(reason, detail=None):
                return _row(exp, eid, wind, b_status, tier=None, gross=None, payout=None, computable=False,
                            reason=reason, detail=detail, flags=flags, peak_time=peak)

            def zero(reason=None, detail=None, wind_value=wind):
                return _row(exp, eid, wind_value, b_status, tier=0, gross=0.0, payout=0.0, computable=True,
                            reason=reason, detail=detail, flags=flags, peak_time=peak)

            if run_reason:
                results.append(nc(run_reason, run_detail)); continue
            if exp_id is None or location_id is None:
                results.append(nc(R_MISSING_FIELD, "exposure requires exposure_id and location_id")); continue
            if duplicate_row:
                results.append(nc(R_DUPLICATE, "duplicate event-location/exposure row; wind is ambiguous")); continue
            if missing_rules:
                results.append(nc(R_RULE_PENDING, ", ".join(missing_rules))); continue
            if w is None:
                results.append(nc(R_MISSING_HAZARD, "no hazard row for this policy and event")); continue
            if w.get("wind_unit", "m/s") != "m/s":
                results.append(nc(R_UNIT, f"wind_unit={w.get('wind_unit')}")); continue
            if selected_metric != rule_config["wind_metric"]:
                results.append(nc(R_METRIC,
                                  f"hazard={selected_metric!r} rule={rule_config['wind_metric']!r}; "
                                  "an approved matching B conversion is required")); continue
            if c_start is None or c_end is None:
                results.append(nc(R_COVERAGE_GAP, "missing coverage window")); continue

            # coverage window
            if window_rule == "hazard_window_max":
                # B computed the max wind inside this policy's window; no wind inside window -> B says so
                if not w.get("window_applied"):
                    results.append(nc(R_COVERAGE_GAP, "hazard row not restricted to policy window")); continue
                if w.get("window_overlap") is False:
                    results.append(zero(R_OUTSIDE_WINDOW)); continue
            else:
                if e_start is None or e_end is None:
                    results.append(nc(R_COVERAGE_GAP, "missing event time span")); continue
                if window_mode == "recurring_month_day":
                    recurring = _recurring_window(c_start, c_end, e_start, e_end, window_offset_hours)
                    overlaps = recurring is not None
                    within = recurring is not None and recurring[0] <= e_start and e_end <= recurring[1]
                else:
                    overlaps = e_start <= c_end and c_start <= e_end
                    within = c_start <= e_start and e_end <= c_end
                if overlaps and not within:
                    flags.append(F_STRADDLE)
                if not (within if window_rule == "event_within_window" else overlaps):
                    results.append(zero(R_OUTSIDE_WINDOW)); continue

            # hazard status
            if b_status == B_NOT_COMPUTABLE:
                results.append(nc(w.get("reason_code") or R_MISSING_HAZARD, "hazard not computed")); continue
            if b_status == B_OUTSIDE_CIRCULATION:
                results.append(zero(None, "outside storm circulation: no trigger (agreed with B)", None)); continue
            if b_status == B_OUTSIDE_RADIUS:
                results.append(nc(R_COVERAGE_GAP, "inside circulation but beyond model radius (agreed: unknown)")); continue
            if b_status == B_OUTSIDE_LEGACY:
                if rule_config["outside_reach_treatment"] == "no_trigger":
                    results.append(zero(None, "outside storm reach treated as no trigger", None))
                else:
                    results.append(nc(R_COVERAGE_GAP, "outside storm reach"))
                continue
            if b_status == B_BELOW:
                if _num(reporting_floor) and lowest >= reporting_floor:
                    results.append(zero(None, "below hazard reporting threshold", None))
                else:
                    results.append(nc(R_REPORTING_FLOOR,
                                      f"hazard reports winds only from {reporting_floor} m/s; lowest tier {lowest}"))
                continue
            if b_status != B_OK or not _num(wind) or wind < 0:
                results.append(nc(R_MISSING_HAZARD, f"unusable hazard row status={b_status}")); continue

            tier = select_tier(float(wind), rule_config["tiers"], rule_config["threshold_boundary"])
            if tier == 0:
                results.append(zero()); continue
            dcb = exp.get("daily_collection_basis_php")
            if not _num(dcb) or dcb <= 0:
                results.append(nc(R_MISSING_COLLECTIONS, "daily_collection_basis_php missing; never derived"))
                continue
            days = rule_config["tiers"][tier - 1]["benefit_days"]
            gross, payout, capped = benefit_amounts(dcb, days, cap, rule_config["rounding"])
            if capped:
                flags.append(F_CAP_APPLIED)
            lim = exp.get("contractual_coverage_limit_php")
            if _num(lim) and payout > lim:
                flags.append(F_ABOVE_CONTRACT_LIMIT)    # backend applies the contractual limit
            results.append(_row(exp, eid, wind, b_status, tier=tier, gross=gross, payout=payout,
                                computable=True, reason=None, flags=flags, peak_time=peak))

    trig_count: dict[str, int] = {}
    potential_trig_count: dict[str, int] = {}
    for r in results:
        if r["trigger_tier"]:
            trig_count[r["exposure_id"]] = trig_count.get(r["exposure_id"], 0) + 1
        if r["trigger_tier"] or F_DUP_EVENT in r["flags"]:
            # A duplicate row cannot be priced, but it still represents a
            # possible second trigger that the backend must resolve.
            potential_trig_count[r["exposure_id"]] = potential_trig_count.get(r["exposure_id"], 0) + 1
    for r in results:
        if potential_trig_count.get(r["exposure_id"], 0) > 1:
            r["flags"] = sorted(set(r["flags"]) | {F_MULTI_TRIGGER})
    if not event_ids and exposures:
        warnings.append("no events in hazard output; no trigger rows produced")

    # ---- 4. single-storm view for ModelRun.locations[] -------------------------------------
    primary = event_ids[0] if len(event_ids) == 1 else None
    raw_locations = hazard_output.get("locations", [])
    if raw_locations is not None and not isinstance(raw_locations, list):
        warnings.append("hazard locations is not a list; ignored for the live-storm view")
        raw_locations = []
    if primary is None and raw_locations:
        ids = {_identifier(l.get("event_id")) for l in raw_locations if isinstance(l, dict) and l.get("event_id")}
        ids.discard(None)
        primary = ids.pop() if len(ids) == 1 else None
    view_keys = ("exposure_id", "location_id", "wind_speed_mps", "hazard_data_status", "trigger_tier",
                 "gross_benefit_basis_php", "modeled_payout_basis_php", "reason_code", "flags")
    locations_view = [{k: r[k] for k in view_keys} for r in results if r["event_id"] == primary] if primary else []

    # ---- 5. summary and validation ----------------------------------------------------------
    reason_counts: dict[str, int] = {}
    for r in results:
        if r["reason_code"]:
            reason_counts[r["reason_code"]] = reason_counts.get(r["reason_code"], 0) + 1
    n_nc = sum(1 for r in results if not r["computable"])
    comp = [r for r in results if r["computable"]]
    summary = {
        "n_events": len(event_ids),
        "n_policies": len(exposures),
        "n_rows": len(results),
        "n_computable": len(comp),
        "n_not_computable": n_nc,
        "n_triggered_rows": sum(1 for r in comp if r["trigger_tier"]),
        "n_policies_triggered": len(trig_count),
        "tier_counts": {str(t): sum(1 for r in comp if r["trigger_tier"] == t)
                        for t in sorted({r["trigger_tier"] for r in comp})},
        "total_gross_benefit_basis_php": round(sum(r["gross_benefit_basis_php"] for r in comp), 2),
        "total_modeled_payout_basis_php": round(sum(r["modeled_payout_basis_php"] for r in comp), 2),
        "n_cap_applied": sum(1 for r in comp if F_CAP_APPLIED in r["flags"]),
        "reason_counts": dict(sorted(reason_counts.items())),
    }
    if run_reason in (R_VERSION, R_FUTURE, R_UNIT, R_UPSTREAM_FAILED):
        status = "fail"
    elif status_label != "confirmed" or missing_rules or n_nc or warnings:
        status = "warn"                                # experimental and fixture runs never "pass"
    else:
        status = "pass"

    body = {
        "module": MODULE_NAME,
        "module_version": MODULE_VERSION,
        "rule_version": rv,
        "candidate_id": rule_config["candidate_id"],
        "rule_status": status_label,
        "as_of": as_of,
        "primary_event_id": primary,
        "results": results,
        "locations": locations_view,
        "summary": summary,
        "validation": {"status": status, "warnings": sorted(set(warnings)),
                       "pending_rule_fields": missing_rules},
    }

    # D consumes a full event x location table, while the existing ``results``
    # and ``locations`` views remain unchanged for the live-storm API.
    hazard_version = hazard_manifest.get("output_hash")
    rule_config_digest = config_hash(rule_config)
    payout_table = {
        "meta": {
            "rule_version": rv,
            "candidate_id": rule_config["candidate_id"],
            "rule_config_hash": rule_config_digest,
            "coverage_window_mode": window_mode,
            "hazard_version": hazard_version,
            "payout_currency": "PHP",
            "wind_speed_unit": "m/s",
            "min_reported_wind_mps": reporting_floor if _num(reporting_floor) else None,
        },
        "rows": [
            {
                "event_id": r.get("event_id"),
                "location_id": r.get("location_id"),
                "exposure_id": r.get("exposure_id"),
                "coverage_id": r.get("coverage_id"),
                "trigger_tier": r.get("trigger_tier"),
                "modeled_payout_basis": r.get("modeled_payout_basis_php"),
                "gross_benefit_basis": r.get("gross_benefit_basis_php"),
                "data_status": "ok" if r.get("computable") else (r.get("reason_code") or "not_computable"),
                "wind_speed_mps": r.get("wind_speed_mps"),
                "rule_version": rv,
                "reason_code": r.get("reason_code"),
            }
            for r in results
        ],
    }
    body["payout_table"] = payout_table

    upstream_hashes = {
        "exposure": exposure_input.get("output_hash"),
        "hazard": hazard_version,
    }
    # Keep the provenance envelope explicit so downstream consumers can audit
    # which candidate, cutoff, and upstream snapshots produced these rows.
    provenance = {
        "as_of": as_of,
        "module": MODULE_NAME,
        "module_version": MODULE_VERSION,
        "candidate_id": rule_config["candidate_id"],
        "coverage_window_mode": window_mode,
        "coverage_window_timezone_offset_hours": window_offset_hours,
        "rule_config_hash": rule_config_digest,
        "upstream_hashes": upstream_hashes,
        "exposure_data_manifest": copy.deepcopy(exposure_input.get("data_manifest")),
        "hazard_manifest": copy.deepcopy(hazard_manifest),
    }
    body["manifest"] = {
        "exposure_output_hash": upstream_hashes["exposure"],
        "hazard_output_hash": upstream_hashes["hazard"],
        "rule_config_hash": rule_config_digest,
        "upstream_hashes": upstream_hashes,
        "provenance": provenance,
    }
    body["manifest"]["output_hash"] = _hash({
        "module": MODULE_NAME,
        "module_version": MODULE_VERSION,
        "candidate_id": rule_config["candidate_id"],
        "as_of": as_of,
        "rule_config_hash": rule_config_digest,
        "upstream_hashes": upstream_hashes,
        "provenance": provenance,
        "results": body["results"],
        "locations": body["locations"],
        "summary": body["summary"],
        "validation": body["validation"],
        "payout_table": payout_table,
    })
    return body
