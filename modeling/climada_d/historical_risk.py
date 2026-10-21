"""Part D: historical risk and pure-risk premium basis.

This module turns B's event catalogue and C's event-by-location payout basis
into annual frequency, expected payout, uncertainty and pure-risk premium
basis.  It is a pure, deterministic computation: no network, no LLM, no
database, no file I/O.  It never adds expenses, capital loading or margin,
never applies limits that are not part of C's payout basis, and never rounds
currency (PHP rounding is pending confirmation; the backend owns it).

Public entry point: :func:`aggregate_historical_risk`.

Conventions (handoff section 3): frequency is events/year, probabilities are
in [0, 1], amounts are PHP, wind speed is m/s, timestamps are ISO 8601 with an
explicit UTC offset.
"""
from __future__ import annotations

import hashlib
import json
import math
import platform
from datetime import datetime, timedelta, timezone

import numpy as np

COMPONENT = "D_historical_risk"
COMPONENT_VERSION = "0.1.0"

AGGREGATION_BASES = ("per_event", "max_per_window", "aggregate_capped")
FREQUENCY_METHODS = ("poisson", "empirical_years")
FREQUENCY_SOURCES = ("recomputed", "hazard_supplied")
PARAM_STATUSES = ("confirmed", "pending_confirmation", "technical_default")

# Business parameters must be supplied by the caller and cannot be defaulted.
BUSINESS_PARAMS = ("observation_period", "aggregation_basis", "frequency_method", "coverage_window")
OPTIONAL_BUSINESS_PARAMS = ("candidate_affordability_threshold_php",)

# Technical parameters only control reporting and numerics.
TECHNICAL_DEFAULTS = {
    "frequency_source": "recomputed",
    "frequency_rel_tolerance": 1e-6,
    "exceedance_thresholds_mps": [18.0, 25.0, 33.0, 42.0, 50.0, 58.0],
    "return_periods_years": [10, 25, 50, 100, 200],
    "simulation_years": 10000,
    "simulation_seed": 20261016,
    "bootstrap_replicates": 200,
    "bootstrap_seed": 7,
    "bootstrap_simulation_years": 2000,
    "confidence_level": 0.90,
    "sensitivity_periods": None,  # None -> automatic first and second half
    "leave_one_out_top_n": 5,
    "reference_coverage_php": 20000.0,
}

FATAL_CODES = {
    "missing_parameter", "invalid_parameter", "invalid_unit", "version_conflict",
    "future_data_rejected", "incomplete_observation_period", "unknown_event",
    "invalid_timestamp", "missing_field", "unbalanced_ensemble", "inconsistent_ensemble_size",
    "upstream_failed",
}

# Part B statuses that mean "computed, and the wind here is below B's reporting threshold".
# They are valid zero-wind results, not missing data, so a payout row carrying one of them is
# treated exactly like an absent row (zero payout). A positive payout on such a row is an error.
ZERO_WIND_STATUSES = {"below_reporting_threshold", "outside_storm_reach"}
UPSTREAM_SEVERITIES = ("info", "warn", "fail")


# --------------------------------------------------------------------------- utils
class _Issues:
    """Collects validation issues as {code, severity, count, ids, message}."""

    def __init__(self):
        self._items = {}

    def add(self, code, severity, message, record_id=None):
        key = (code, severity, message)
        item = self._items.setdefault(key, {"code": code, "severity": severity, "message": message,
                                             "count": 0, "ids": []})
        item["count"] += 1
        if record_id is not None and len(item["ids"]) < 50:
            item["ids"].append(str(record_id))

    def has_fatal(self):
        return any(i["severity"] == "fail" and i["code"] in FATAL_CODES for i in self._items.values())

    def severities(self):
        return {i["severity"] for i in self._items.values()}

    def as_list(self):
        return sorted(self._items.values(), key=lambda i: (i["severity"], i["code"], i["message"]))


def _parse_time(value):
    """Parse an ISO 8601 timestamp with explicit offset, returned in UTC. None if invalid."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _is_number(value):
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _canonical(obj):
    """JSON-safe, order-independent representation used for hashing."""
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in sorted(obj.items(), key=lambda kv: str(kv[0]))}
    if isinstance(obj, (list, tuple)):
        items = [_canonical(v) for v in obj]
        if items and all(isinstance(v, dict) for v in items):
            items = sorted(items, key=lambda v: json.dumps(v, sort_keys=True))
        return items
    if isinstance(obj, (np.floating, float)):
        value = float(obj)
        if not math.isfinite(value):
            return str(value)
        return float(f"{value:.12g}")
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def _hash(obj):
    payload = json.dumps(_canonical(obj), sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _f(value):
    """Convert numpy scalars to plain floats (None stays None)."""
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


# --------------------------------------------------------------------------- config
def _read_param(name, raw, business, issues):
    if isinstance(raw, dict) and "value" in raw:
        status = raw.get("status", "pending_confirmation")
        if status not in PARAM_STATUSES:
            issues.add("invalid_parameter", "fail", f"unknown status '{status}' for {name}", name)
            status = "pending_confirmation"
        if business and status == "technical_default":
            issues.add("invalid_parameter", "fail",
                       f"business parameter {name} cannot be a technical_default", name)
        return {"name": name, "value": raw["value"], "status": status, "source": raw.get("source")}
    return {"name": name, "value": raw, "status": "pending_confirmation", "source": None,
            "note": "unlabelled value treated as pending_confirmation"}


def _parse_md(text):
    try:
        month, day = (int(p) for p in str(text).split("-"))
        datetime(2000, month, day)  # leap year accepts 02-29
        return (month, day)
    except (ValueError, TypeError):
        return None


def _parse_config(risk_config, issues):
    params = {}
    risk_config = risk_config or {}
    for name in BUSINESS_PARAMS:
        if name not in risk_config:
            issues.add("missing_parameter", "fail", f"required business parameter {name} not supplied", name)
            continue
        params[name] = _read_param(name, risk_config[name], True, issues)
    for name in OPTIONAL_BUSINESS_PARAMS:
        if name in risk_config and risk_config[name] is not None:
            params[name] = _read_param(name, risk_config[name], True, issues)
    for name, default in TECHNICAL_DEFAULTS.items():
        if name in risk_config:
            raw = risk_config[name]
            value = raw["value"] if isinstance(raw, dict) and "value" in raw else raw
            params[name] = {"name": name, "value": value, "status": "technical_default",
                            "source": "caller override"}
        else:
            params[name] = {"name": name, "value": default, "status": "technical_default",
                            "source": "D module default"}
    unknown = sorted(set(risk_config) - set(BUSINESS_PARAMS) - set(OPTIONAL_BUSINESS_PARAMS)
                     - set(TECHNICAL_DEFAULTS))
    for name in unknown:
        issues.add("invalid_parameter", "warn", f"unknown parameter {name} ignored", name)

    def bad(name, message):
        issues.add("invalid_parameter", "fail", message, name)

    if "observation_period" in params:
        period = params["observation_period"]["value"]
        try:
            start, end = int(period["start_year"]), int(period["end_year"])
            if start > end:
                bad("observation_period", "observation_period start_year after end_year")
            else:
                params["observation_period"]["value"] = {"start_year": start, "end_year": end}
        except (TypeError, KeyError, ValueError):
            bad("observation_period", "observation_period needs integer start_year and end_year")
    if "aggregation_basis" in params and params["aggregation_basis"]["value"] not in AGGREGATION_BASES:
        bad("aggregation_basis", f"aggregation_basis must be one of {AGGREGATION_BASES}")
    if "frequency_method" in params and params["frequency_method"]["value"] not in FREQUENCY_METHODS:
        bad("frequency_method", f"frequency_method must be one of {FREQUENCY_METHODS}")
    if "coverage_window" in params:
        window = params["coverage_window"]["value"]
        try:
            start_md, end_md = _parse_md(window["start_md"]), _parse_md(window["end_md"])
        except (TypeError, KeyError):
            start_md = end_md = None
        offset = window.get("utc_offset_hours", 0) if isinstance(window, dict) else 0
        if start_md is None or end_md is None:
            bad("coverage_window", "coverage_window needs start_md and end_md as 'MM-DD'")
        elif not _is_number(offset) or not -12 <= float(offset) <= 14:
            bad("coverage_window", "coverage_window utc_offset_hours must be between -12 and 14")
        else:
            params["coverage_window"]["parsed"] = (start_md, end_md)
            params["coverage_window"]["offset_hours"] = float(offset)
    if "candidate_affordability_threshold_php" in params:
        if not _is_number(params["candidate_affordability_threshold_php"]["value"]):
            bad("candidate_affordability_threshold_php", "candidate threshold must be a number")
    if params["frequency_source"]["value"] not in FREQUENCY_SOURCES:
        bad("frequency_source", f"frequency_source must be one of {FREQUENCY_SOURCES}")
    for name in ("simulation_years", "bootstrap_replicates", "bootstrap_simulation_years"):
        value = params[name]["value"]
        if not isinstance(value, (int, np.integer)) or isinstance(value, bool) or value < 0:
            bad(name, f"{name} must be a non-negative integer")
    for name in ("simulation_seed", "bootstrap_seed"):
        if not isinstance(params[name]["value"], (int, np.integer)) or isinstance(params[name]["value"], bool):
            bad(name, f"{name} must be an integer")
    level = params["confidence_level"]["value"]
    if not _is_number(level) or not 0 < float(level) < 1:
        bad("confidence_level", "confidence_level must be in (0, 1)")
    tol = params["frequency_rel_tolerance"]["value"]
    if not _is_number(tol) or float(tol) < 0:
        bad("frequency_rel_tolerance", "frequency_rel_tolerance must be >= 0")
    for name in ("exceedance_thresholds_mps", "return_periods_years"):
        values = params[name]["value"]
        if not isinstance(values, (list, tuple)) or not all(_is_number(v) and float(v) > 0 for v in values):
            bad(name, f"{name} must be a list of positive numbers")
    if not _is_number(params["reference_coverage_php"]["value"]) or params["reference_coverage_php"]["value"] <= 0:
        bad("reference_coverage_php", "reference_coverage_php must be positive")
    periods = params["sensitivity_periods"]["value"]
    if periods is not None:
        try:
            params["sensitivity_periods"]["value"] = [
                {"label": str(p.get("label", f"{int(p['start_year'])}-{int(p['end_year'])}")),
                 "start_year": int(p["start_year"]), "end_year": int(p["end_year"])} for p in periods]
        except (TypeError, KeyError, ValueError, AttributeError):
            bad("sensitivity_periods", "sensitivity_periods must be a list of {start_year, end_year}")
    return params


def _in_window(moment, window):
    (start_md, end_md) = window
    md = (moment.month, moment.day)
    if start_md <= end_md:
        return start_md <= md <= end_md
    return md >= start_md or md <= end_md  # window wraps over the year end


# --------------------------------------------------------------------------- engine
class _Context:
    """Validated arrays the engine works on. Events and rows are sorted by id."""

    def __init__(self, ev_ids, ev_track, ev_year_idx, ev_member, ev_freq, track_ids, track_members,
                 n_years, years, loc_ids, cov, rows_e, rows_l, rows_x, rows_tier, rows_w):
        self.ev_ids = ev_ids
        self.ev_track = ev_track
        self.ev_year_idx = ev_year_idx
        self.ev_member = ev_member
        self.ev_freq = ev_freq
        self.track_ids = track_ids
        self.track_members = track_members
        self.n_years = n_years
        self.years = years
        self.loc_ids = loc_ids
        self.cov = cov
        self.r_e = rows_e
        self.r_l = rows_l
        self.r_x = rows_x
        self.r_tier = rows_tier
        self.r_w = rows_w
        self.n_ev = len(ev_ids)
        self.n_loc = len(loc_ids)
        # rows are sorted by event: pointer array for fast expansion
        self.ev_ptr = np.searchsorted(rows_e, np.arange(self.n_ev + 1)) if self.n_ev else np.zeros(1, int)
        self.ev_total = np.bincount(rows_e, weights=rows_x, minlength=self.n_ev) if self.n_ev \
            else np.zeros(0)
        # order used by the analytic max-per-window formula: by location, payout descending
        self.max_order = np.lexsort((-rows_x, rows_l))
        self.balanced = len(set(track_members.tolist())) <= 1
        self._engine_cache = {}


def _poisson_analytic(ctx, rates, basis):
    """Exact Poisson results for per_event and max_per_window. Returns (mean, second_moment)."""
    n_loc = ctx.n_loc
    if basis == "per_event":
        w = rates[ctx.r_e]
        mean = np.bincount(ctx.r_l, weights=w * ctx.r_x, minlength=n_loc)
        var = np.bincount(ctx.r_l, weights=w * ctx.r_x ** 2, minlength=n_loc)
        return mean, var + mean ** 2
    # max_per_window: annual max M, P(M > m) = 1 - exp(-sum of rates with payout > m)
    order = ctx.max_order
    loc = ctx.r_l[order]
    x = ctx.r_x[order]
    r = rates[ctx.r_e[order]]
    cum = np.cumsum(r)
    start_of_group = np.r_[True, loc[1:] != loc[:-1]] if len(loc) else np.zeros(0, bool)
    group_start_idx = np.maximum.accumulate(np.where(start_of_group, np.arange(len(loc)), 0)) \
        if len(loc) else np.zeros(0, int)
    offset = np.where(group_start_idx > 0, cum[group_start_idx - 1], 0.0) if len(loc) else np.zeros(0)
    lam = cum - offset  # sum of rates of rows with payout >= x within the same location
    next_same = np.r_[loc[1:] == loc[:-1], False] if len(loc) else np.zeros(0, bool)
    x_next = np.where(next_same, np.r_[x[1:], 0.0] if len(x) else x, 0.0)
    p_exceed = -np.expm1(-lam)
    mean = np.bincount(loc, weights=(x - x_next) * p_exceed, minlength=n_loc)
    second = np.bincount(loc, weights=(x ** 2 - x_next ** 2) * p_exceed, minlength=n_loc)
    return mean, second


def _aggregate_years(ctx, occ_event, occ_year, n_years, basis):
    """Aggregate occurrences into per (year, location) annual payouts.

    Returns key arrays (year, loc, value, triggered) and per-year portfolio totals
    and per-year largest single-event portfolio payout (OEP).
    """
    lengths = ctx.ev_ptr[occ_event + 1] - ctx.ev_ptr[occ_event]
    total = int(lengths.sum())
    oep = np.zeros(n_years)
    if len(occ_event):
        np.maximum.at(oep, occ_year, ctx.ev_total[occ_event])
    if total == 0:
        empty = np.zeros(0)
        return np.zeros(0, int), np.zeros(0, int), empty, empty.astype(bool), np.zeros(n_years), oep
    starts = np.repeat(ctx.ev_ptr[occ_event], lengths)
    within = np.arange(total) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    row_idx = starts + within
    year = np.repeat(occ_year, lengths)
    loc = ctx.r_l[row_idx]
    key = year.astype(np.int64) * ctx.n_loc + loc
    uniq, inv = np.unique(key, return_inverse=True)
    if basis == "max_per_window":
        value = np.zeros(len(uniq))
        np.maximum.at(value, inv, ctx.r_x[row_idx])
    else:
        value = np.bincount(inv, weights=ctx.r_x[row_idx], minlength=len(uniq))
    trig = np.zeros(len(uniq))
    np.maximum.at(trig, inv, (ctx.r_tier[row_idx] > 0).astype(float))
    k_year = (uniq // ctx.n_loc).astype(int)
    k_loc = (uniq % ctx.n_loc).astype(int)
    if basis == "aggregate_capped":
        value = np.minimum(value, ctx.cov[k_loc])
    totals = np.bincount(k_year, weights=value, minlength=n_years)
    return k_year, k_loc, value, trig > 0, totals, oep


def _empirical_engine(ctx, basis, keep=None):
    """Pseudo-years (season year, ensemble member). Cached per (basis, kept events)."""
    cache_key = (basis, None if keep is None else keep.tobytes())
    if cache_key in ctx._engine_cache:
        return ctx._engine_cache[cache_key]
    m = int(ctx.track_members[0]) if len(ctx.track_members) else 1
    n_pseudo = ctx.n_years * m
    events = np.arange(ctx.n_ev) if keep is None else np.flatnonzero(keep)
    occ_year = ctx.ev_year_idx[events] * m + ctx.ev_member[events]
    result = _aggregate_years(ctx, events, occ_year, n_pseudo, basis) + (m,)
    ctx._engine_cache[cache_key] = result
    return result


def _simulate_poisson(ctx, rates, basis, n_years, seed, chunk=1000):
    """Monte Carlo years: N ~ Poisson(total rate), events drawn by rate, whole event rows kept
    together so every location in the same storm moves together (spatial correlation)."""
    rng = np.random.default_rng(seed)
    total_rate = float(rates.sum())
    loc_sum = np.zeros(ctx.n_loc)
    loc_sq = np.zeros(ctx.n_loc)
    loc_trig = np.zeros(ctx.n_loc)
    totals = np.zeros(n_years)
    oep = np.zeros(n_years)
    if n_years == 0:
        return None
    probs = rates / total_rate if total_rate > 0 else None
    for begin in range(0, n_years, chunk):
        size = min(chunk, n_years - begin)
        counts = rng.poisson(total_rate, size) if total_rate > 0 else np.zeros(size, int)
        occ_event = rng.choice(ctx.n_ev, size=int(counts.sum()), p=probs) if counts.sum() else \
            np.zeros(0, int)
        occ_year = np.repeat(np.arange(size), counts)
        k_year, k_loc, value, trig, chunk_totals, chunk_oep = _aggregate_years(
            ctx, occ_event, occ_year, size, basis)
        loc_sum += np.bincount(k_loc, weights=value, minlength=ctx.n_loc)
        loc_sq += np.bincount(k_loc, weights=value ** 2, minlength=ctx.n_loc)
        loc_trig += np.bincount(k_loc, weights=trig.astype(float), minlength=ctx.n_loc)
        totals[begin:begin + size] = chunk_totals
        oep[begin:begin + size] = chunk_oep
    return {"mean": loc_sum / n_years, "second": loc_sq / n_years, "trigger_prob": loc_trig / n_years,
            "totals": totals, "oep": oep}


def _evaluate(ctx, method, basis, keep=None, year_mult=None, sim_years=None, seed=None):
    """Expected annual payout per location and portfolio, with optional event mask and
    per-year multipliers (used by the bootstrap and sub-period sensitivity)."""
    keep_f = np.ones(ctx.n_ev) if keep is None else keep.astype(float)
    ymult = np.ones(ctx.n_years) if year_mult is None else np.asarray(year_mult, float)
    if method == "poisson":
        rates = ctx.ev_freq * keep_f * ymult[ctx.ev_year_idx] if ctx.n_ev else np.zeros(0)
        if basis in ("per_event", "max_per_window"):
            mean, second = _poisson_analytic(ctx, rates, basis)
            return {"mean": mean, "second": second, "approx": "exact"}
        sim = _simulate_poisson(ctx, rates, basis, sim_years or 0, seed)
        if sim is None:
            nan = np.full(ctx.n_loc, np.nan)
            return {"mean": nan, "second": nan, "approx": "not_simulated"}
        return {"mean": sim["mean"], "second": sim["second"], "approx": f"monte_carlo_{sim_years}_years"}
    k_year, k_loc, value, trig, totals, oep, m = _empirical_engine(ctx, basis, keep)
    year_weight = ymult / (ctx.n_years * m)  # weight of each pseudo-year of season year y
    w = year_weight[k_year // m] if len(k_year) else np.zeros(0)
    mean = np.bincount(k_loc, weights=w * value, minlength=ctx.n_loc)
    second = np.bincount(k_loc, weights=w * value ** 2, minlength=ctx.n_loc)
    return {"mean": mean, "second": second, "approx": "exact_over_pseudo_years"}


# --------------------------------------------------------------------------- main API
def aggregate_historical_risk(exposure, event_catalogue, hazard_coverage, payout_table, risk_config,
                              *, as_of, data_manifest, upstream_issues=None):
    """Aggregate B/C historical outputs into frequency, expected payout and pure-risk premium basis.

    Parameters
    ----------
    exposure : list of dict
        From A. ``location_id``, ``coverage_amount_php`` (maximum payout per coverage window,
        used for rate-on-coverage and the aggregate cap), optional ``region``.
    event_catalogue : list of dict
        From B. One row per event (historical or synthetic): ``event_id``, ``track_id``,
        ``member_index`` (0 = historical original), ``members_generated``, ``season_year``,
        ``hazard_version``; ``reference_time`` (required if the coverage window is not the
        full year), ``frequency_per_year`` and ``frequency_unit`` (optional, cross-checked).
    hazard_coverage : list of dict
        From B. ``location_id``, ``data_status`` ("ok" or a reason code), ``hazard_version``.
    payout_table : dict
        From C. ``meta`` (``rule_version``, ``hazard_version``, ``payout_currency``,
        ``wind_speed_unit``, ``min_reported_wind_mps``) and ``rows``: ``event_id``,
        ``location_id``, ``trigger_tier``, ``modeled_payout_basis``, ``data_status``, optional
        ``wind_speed_mps``.  A missing (event, location) row means wind below
        ``min_reported_wind_mps`` and zero payout.
    risk_config : dict
        Parameters as ``{"value": ..., "status": "confirmed" | "pending_confirmation",
        "source": ...}``. Unlabelled values are treated as pending_confirmation.
    as_of : str
        Calculation cut-off (ISO 8601 with offset).
    data_manifest : dict
        Upstream manifest; passed through and extended.
    upstream_issues : list of dict, optional
        Problems found by an adapter (for example ``from_part_b``) before D runs, as
        ``{"code", "severity" ("info"|"warn"|"fail"), "message", "ids"}``. They are merged into
        D's validation so they cannot be lost between modules.
    """
    issues = _Issues()
    raw_inputs = {"exposure": exposure, "event_catalogue": event_catalogue,
                  "hazard_coverage": hazard_coverage, "payout_table": payout_table,
                  "risk_config": risk_config, "as_of": as_of, "data_manifest": data_manifest,
                  "upstream_issues": upstream_issues or []}
    input_hash = _hash(raw_inputs)
    for item in upstream_issues or []:
        severity = item.get("severity", "warn")
        if severity not in UPSTREAM_SEVERITIES:
            severity = "warn"
        code = str(item.get("code", "upstream_issue"))
        if severity == "fail" and code != "future_data_rejected":
            code = "upstream_failed"
        ids = item.get("ids") or [None]
        message = str(item.get("message", "reported by upstream adapter"))
        for record_id in ids:
            issues.add(code, severity, message, record_id)
    params = _parse_config(risk_config, issues)
    as_of_dt = _parse_time(as_of)
    if as_of_dt is None:
        issues.add("invalid_timestamp", "fail", "as_of must be ISO 8601 with an explicit offset", "as_of")

    exposure = exposure or []
    event_catalogue = event_catalogue or []
    hazard_coverage = hazard_coverage or []
    payout_table = payout_table or {}
    meta = payout_table.get("meta") or {}
    payout_rows = payout_table.get("rows") or []

    # ---- payout table metadata
    for field in ("rule_version", "hazard_version", "payout_currency"):
        if field not in meta or meta.get(field) in (None, ""):
            issues.add("missing_field", "fail", f"payout_table.meta.{field} missing", field)
    if meta.get("payout_currency", "PHP") != "PHP":
        issues.add("invalid_unit", "fail", "payout currency must be PHP", meta.get("payout_currency"))
    if meta.get("wind_speed_unit", "m/s") != "m/s":
        issues.add("invalid_unit", "fail", "wind speed unit must be m/s", meta.get("wind_speed_unit"))
    min_wind = meta.get("min_reported_wind_mps")
    if min_wind is not None and not _is_number(min_wind):
        issues.add("invalid_parameter", "fail", "min_reported_wind_mps must be a number", "min_reported_wind_mps")
        min_wind = None
    rule_version = meta.get("rule_version")
    hazard_version = meta.get("hazard_version")

    # ---- exposure (A)
    loc_info = {}
    loc_bad = {}
    seen = {}
    for row in exposure:
        lid = row.get("location_id")
        if lid is None:
            issues.add("missing_field", "warn", "exposure row without location_id ignored")
            continue
        lid = str(lid)
        seen[lid] = seen.get(lid, 0) + 1
        cov = row.get("coverage_amount_php")
        if not _is_number(cov) or float(cov) < 0:
            loc_bad.setdefault(lid, "invalid_value")
            issues.add("invalid_value", "warn", "coverage_amount_php missing, negative or not finite", lid)
            cov = float("nan")
        loc_info[lid] = {"coverage_amount_php": float(cov), "region": row.get("region")}
    for lid, n in seen.items():
        if n > 1:
            loc_bad[lid] = "duplicate_record"
            issues.add("duplicate_record", "warn", "duplicate location_id in exposure", lid)

    # ---- hazard coverage (B)
    coverage_status = {}
    for row in hazard_coverage:
        lid = str(row.get("location_id"))
        if row.get("hazard_version") in (None, ""):
            issues.add("missing_field", "fail", "hazard_coverage hazard_version missing", lid)
        elif hazard_version is not None and row.get("hazard_version") != hazard_version:
            issues.add("version_conflict", "fail", "hazard_coverage hazard_version differs from payout table", lid)
        if lid in coverage_status:
            loc_bad.setdefault(lid, "duplicate_record")
            issues.add("duplicate_record", "warn", "duplicate location in hazard_coverage", lid)
        coverage_status[lid] = row.get("data_status") or "missing_hazard"
    for lid in loc_info:
        status = coverage_status.get(lid)
        if status is None:
            loc_bad.setdefault(lid, "missing_hazard")
            issues.add("missing_hazard", "warn", "no hazard coverage entry for location", lid)
        elif status != "ok":
            loc_bad.setdefault(lid, str(status))
            issues.add(str(status), "warn", "hazard not computed for location", lid)

    # ---- event catalogue (B)
    period = params.get("observation_period", {}).get("value") or {}
    window = params.get("coverage_window", {}).get("parsed")
    full_year = window == ((1, 1), (12, 31))
    window_offset = params.get("coverage_window", {}).get("offset_hours", 0.0)
    events = {}
    excluded_events = {}  # event_id -> reason (rows for these are dropped, not errors)
    track_members = {}
    track_member_sets = {}
    supplied_freq = {}
    for row in event_catalogue:
        eid = row.get("event_id")
        if eid is None:
            issues.add("missing_field", "fail", "event row without event_id")
            continue
        eid = str(eid)
        if eid in events or eid in excluded_events:
            issues.add("duplicate_record", "fail", "duplicate event_id in catalogue", eid)
            issues.add("version_conflict", "fail", "catalogue is not a clean event set", eid)
            continue
        missing = [f for f in ("track_id", "member_index", "season_year") if row.get(f) is None]
        if missing:
            issues.add("missing_field", "fail", f"event missing {', '.join(missing)}", eid)
            continue
        if row.get("hazard_version") != hazard_version:
            issues.add("version_conflict", "fail", "event hazard_version differs from payout table", eid)
        try:
            season = int(row["season_year"])
            member = int(row["member_index"])
        except (TypeError, ValueError):
            issues.add("invalid_value", "fail", "season_year/member_index must be integers", eid)
            issues.add("missing_field", "fail", "event fields not parseable", eid)
            continue
        ref = None
        if row.get("reference_time") is not None:
            ref = _parse_time(row["reference_time"])
            if ref is None:
                issues.add("invalid_timestamp", "fail", "reference_time not ISO 8601 with offset", eid)
                continue
        if as_of_dt is not None and ((ref is not None and ref > as_of_dt) or season > as_of_dt.year):
            issues.add("future_data_rejected", "fail", "event is later than as_of", eid)
            continue
        if row.get("frequency_per_year") is not None:
            if row.get("frequency_unit") != "1/year":
                issues.add("invalid_unit", "fail", "frequency_unit must be '1/year'", eid)
            elif not _is_number(row["frequency_per_year"]) or row["frequency_per_year"] < 0:
                issues.add("invalid_value", "warn", "hazard-supplied frequency invalid; ignored", eid)
            else:
                supplied_freq[eid] = float(row["frequency_per_year"])
        track = str(row["track_id"])
        mg = row.get("members_generated")
        if mg is not None:
            if not isinstance(mg, (int, np.integer)) or isinstance(mg, bool) or mg < 1:
                issues.add("invalid_value", "fail", "members_generated must be a positive integer", eid)
                issues.add("inconsistent_ensemble_size", "fail", "members_generated invalid", track)
                continue
            if track_members.get(track, mg) != mg:
                issues.add("inconsistent_ensemble_size", "fail",
                           "members_generated differs within one track", track)
            track_members[track] = int(mg)
        if member < 0 or (mg is not None and member >= mg):
            issues.add("inconsistent_ensemble_size", "fail", "member_index outside 0..members_generated-1", eid)
            continue
        if member in track_member_sets.setdefault(track, set()):
            issues.add("duplicate_record", "fail", "duplicate (track_id, member_index)", eid)
            issues.add("version_conflict", "fail", "catalogue is not a clean event set", eid)
            continue
        track_member_sets[track].add(member)
        policy_season = season
        if window is not None and window[0] > window[1] and ref is not None:
            local_ref = ref + timedelta(hours=window_offset)
            # Label a wrapping policy window by the calendar year in which it ends.
            # December 2001 and January 2002 therefore share policy season 2002.
            if (local_ref.month, local_ref.day) >= window[0]:
                policy_season = local_ref.year + 1
            else:
                policy_season = local_ref.year
        if period and not period["start_year"] <= policy_season <= period["end_year"]:
            excluded_events[eid] = "outside_observation_period"
            continue
        if window is not None and not full_year:
            if ref is None:
                excluded_events[eid] = "missing_timestamp"
                continue
            # policy dates are local calendar dates: shift the UTC storm time before comparing
            if not _in_window(ref + timedelta(hours=window_offset), window):
                excluded_events[eid] = "outside_coverage_window"
                continue
        events[eid] = {"track": track, "member": member, "season": policy_season, "ref": ref}

    # tracks whose members_generated was not supplied: infer from members present (warn)
    for track, members in track_member_sets.items():
        if track not in track_members:
            track_members[track] = max(members) + 1
            issues.add("ensemble_size_inferred", "warn",
                       "members_generated not supplied; inferred from member_index (biased high "
                       "if B dropped members)", track)

    n_out = sum(1 for r in excluded_events.values() if r == "outside_observation_period")
    n_win = sum(1 for r in excluded_events.values() if r == "outside_coverage_window")
    if n_out:
        issues.add("outside_observation_period", "info",
                   f"{n_out} events outside observation period excluded")
    if n_win:
        issues.add("outside_coverage_window", "info", f"{n_win} events outside coverage window excluded")

    if period and as_of_dt is not None and period["end_year"] >= as_of_dt.year:
        issues.add("incomplete_observation_period", "fail",
                   "observation period must end before the as_of year (a partial year understates "
                   "frequency)", period["end_year"])

    # ---- payout rows (C)
    catalogue_ids = {str(r.get("event_id")) for r in event_catalogue if r.get("event_id") is not None}
    rows_by_key = {}
    row_problem_locs = {}
    rows_dropped_excluded = 0
    tier_payout_mismatch = 0
    for row in payout_rows:
        eid, lid = str(row.get("event_id")), str(row.get("location_id"))
        if row.get("rule_version") is not None and row.get("rule_version") != rule_version:
            issues.add("version_conflict", "fail", "payout row rule_version differs from table meta", eid)
            continue
        if eid not in events:
            if eid in excluded_events:
                rows_dropped_excluded += 1
                if excluded_events[eid] == "missing_timestamp" and lid in loc_info:
                    row_problem_locs.setdefault(lid, "missing_timestamp")
                    issues.add("missing_timestamp", "warn",
                               "event affecting this location has no reference_time for the "
                               "coverage-window test", lid)
                continue
            if eid in catalogue_ids:  # event exists but was rejected above; already reported
                continue
            issues.add("unknown_event", "fail", "payout row references an event not in the catalogue", eid)
            continue
        if lid not in loc_info:
            issues.add("unmapped_exposure", "warn", "payout row for a location not in exposure; ignored", lid)
            continue
        if row.get("data_status") in (None, ""):
            row_problem_locs.setdefault(lid, "missing_field")
            issues.add("missing_field", "fail", "payout row data_status missing", f"{eid}|{lid}")
            continue
        status = row["data_status"]
        if status in ZERO_WIND_STATUSES:
            x = row.get("modeled_payout_basis")
            if x is None or (_is_number(x) and float(x) == 0.0):
                continue  # same as an absent row: wind below B's reporting threshold, zero payout
            row_problem_locs.setdefault(lid, "invalid_value")
            issues.add("invalid_value", "warn", "positive payout on a row B marked as below threshold",
                       f"{eid}|{lid}")
            continue
        if status != "ok":
            row_problem_locs.setdefault(lid, str(status))
            issues.add(str(status), "warn", "payout row not computable", f"{eid}|{lid}")
            continue
        x, tier = row.get("modeled_payout_basis"), row.get("trigger_tier")
        if not _is_number(x) or float(x) < 0 or not isinstance(tier, (int, np.integer)) \
                or isinstance(tier, bool) or tier < 0:
            row_problem_locs.setdefault(lid, "invalid_value")
            issues.add("invalid_value", "warn", "payout or tier missing, negative or not finite",
                       f"{eid}|{lid}")
            continue
        wind = row.get("wind_speed_mps")
        if wind is not None and (not _is_number(wind) or float(wind) < 0):
            row_problem_locs.setdefault(lid, "invalid_value")
            issues.add("invalid_value", "warn", "wind_speed_mps negative or not finite", f"{eid}|{lid}")
            continue
        key = (eid, lid)
        if key in rows_by_key:
            row_problem_locs[lid] = "duplicate_record"
            issues.add("duplicate_record", "warn", "duplicate (event, location) payout row", f"{eid}|{lid}")
            continue
        if (tier > 0) != (float(x) > 0):
            tier_payout_mismatch += 1
            issues.add("tier_payout_inconsistent", "warn", "trigger_tier and payout disagree", f"{eid}|{lid}")
        cov = loc_info[lid]["coverage_amount_php"]
        if math.isfinite(cov) and float(x) > cov + 1e-9:
            issues.add("payout_exceeds_coverage", "warn",
                       "event payout above coverage_amount_php (C's rule, not capped by D)", f"{eid}|{lid}")
        rows_by_key[key] = (float(x), int(tier), float(wind) if wind is not None else float("nan"))
    if rows_dropped_excluded:
        issues.add("rows_for_excluded_events", "info",
                   f"{rows_dropped_excluded} payout rows belong to excluded events and were dropped")
    for lid, reason in row_problem_locs.items():
        loc_bad.setdefault(lid, reason)

    method = params.get("frequency_method", {}).get("value")
    if method == "empirical_years" and len(set(track_members.values())) > 1:
        issues.add("unbalanced_ensemble", "fail",
                   "empirical_years needs the same members_generated for every track")

    # ---- parameters summary and pending list
    param_list = [{k: v for k, v in p.items() if k not in ("parsed", "offset_hours")} for p in params.values()]
    pending = sorted(p["name"] for p in params.values() if p["status"] == "pending_confirmation")
    base = {
        "component": COMPONENT,
        "component_version": COMPONENT_VERSION,
        "as_of": as_of,
        "rule_version": rule_version,
        "hazard_version": hazard_version,
        "input_hash": input_hash,
        "parameters": param_list,
        "pending_parameters": pending,
        "basis_status": "provisional" if pending else "confirmed_inputs",
    }

    if issues.has_fatal():
        result = dict(base)
        result.update({
            "sample_period": period or None,
            "frequency": None, "locations": [
                {"location_id": lid, "status": "not_computed", "reason_code": "run_failed"}
                for lid in sorted(loc_info)],
            "risk_summary": None, "sensitivity": None, "candidate_threshold_checks": None,
            "data_manifest": _manifest(data_manifest, params),
            "validation": _validation(issues, pending, n_excluded=len(loc_info)),
        })
        result["output_hash"] = _hash({k: v for k, v in result.items() if k != "output_hash"})
        return result

    # ---- build context
    n_years = period["end_year"] - period["start_year"] + 1
    years = list(range(period["start_year"], period["end_year"] + 1))
    ev_ids = sorted(events)
    track_ids = sorted({events[e]["track"] for e in ev_ids})
    track_index = {t: i for i, t in enumerate(track_ids)}
    ev_track = np.array([track_index[events[e]["track"]] for e in ev_ids], dtype=int)
    ev_year_idx = np.array([events[e]["season"] - period["start_year"] for e in ev_ids], dtype=int)
    ev_member = np.array([events[e]["member"] for e in ev_ids], dtype=int)
    tm = np.array([track_members[t] for t in track_ids], dtype=float)
    recomputed = 1.0 / (n_years * tm[ev_track]) if len(ev_ids) else np.zeros(0)

    # frequency cross-check against B's (CLIMADA) values
    freq_check = _frequency_check(ev_ids, recomputed, supplied_freq, params, issues, events, track_members)
    if params["frequency_source"]["value"] == "hazard_supplied" and method == "empirical_years":
        issues.add("frequency_source_ignored", "warn",
                   "empirical_years weights every pseudo-year equally; hazard-supplied frequencies "
                   "are only cross-checked, not used")
    if params["frequency_source"]["value"] == "hazard_supplied":
        if len(supplied_freq) != len(ev_ids):
            issues.add("missing_field", "fail", "frequency_source=hazard_supplied but some events lack it")
            freq = recomputed
        else:
            freq = np.array([supplied_freq[e] for e in ev_ids])
    else:
        freq = recomputed

    loc_ids_all = sorted(loc_info)
    computable = [lid for lid in loc_ids_all if lid not in loc_bad]
    loc_index = {lid: i for i, lid in enumerate(computable)}
    ev_index = {e: i for i, e in enumerate(ev_ids)}
    keys = sorted(k for k in rows_by_key if k[1] in loc_index)
    r_e = np.array([ev_index[k[0]] for k in keys], dtype=int)
    r_l = np.array([loc_index[k[1]] for k in keys], dtype=int)
    r_x = np.array([rows_by_key[k][0] for k in keys], dtype=float)
    r_tier = np.array([rows_by_key[k][1] for k in keys], dtype=int)
    r_w = np.array([rows_by_key[k][2] for k in keys], dtype=float)
    cov = np.array([loc_info[lid]["coverage_amount_php"] for lid in computable], dtype=float)
    ctx = _Context(ev_ids, ev_track, ev_year_idx, ev_member, freq, track_ids,
                   np.array([track_members[t] for t in track_ids], dtype=int), n_years, years,
                   computable, cov, r_e, r_l, r_x, r_tier, r_w)

    if ctx.n_ev == 0:
        issues.add("empty_event_set", "warn", "no events in the observation period and coverage window")
    if method == "empirical_years" and ctx.n_ev and not ctx.balanced:  # defensive; caught above
        issues.add("unbalanced_ensemble", "fail", "empirical_years needs a balanced ensemble")

    basis = params["aggregation_basis"]["value"]
    sim_years = int(params["simulation_years"]["value"])
    sim_seed = int(params["simulation_seed"]["value"])
    if method == "poisson" and basis == "aggregate_capped" and sim_years == 0:
        issues.add("invalid_parameter", "fail", "aggregate_capped with poisson needs simulation_years > 0")
    if issues.has_fatal():
        result = dict(base)
        result.update({
            "sample_period": period or None,
            "frequency": None,
            "locations": [
                {"location_id": lid, "status": "not_computed", "reason_code": "run_failed"}
                for lid in sorted(loc_info)
            ],
            "risk_summary": None,
            "sensitivity": None,
            "candidate_threshold_checks": None,
            "data_manifest": _manifest(data_manifest, params),
            "validation": _validation(issues, pending, n_excluded=len(loc_info)),
        })
        result["output_hash"] = _hash({k: v for k, v in result.items() if k != "output_hash"})
        return result

    # ---- core results
    main = _evaluate(ctx, method, basis, sim_years=sim_years, seed=sim_seed)
    rates = freq
    total_rate = float(rates.sum())
    trig_rate = np.bincount(r_l, weights=rates[r_e] * (r_tier > 0), minlength=ctx.n_loc)
    per_event_mean = np.bincount(r_l, weights=rates[r_e] * r_x, minlength=ctx.n_loc)
    tail = _tail(ctx, method, basis, sim_years, sim_seed)
    confirmed_for_prob = all(params[p]["status"] == "confirmed"
                             for p in ("frequency_method", "observation_period", "aggregation_basis"))
    if method == "poisson":
        trig_prob = -np.expm1(-trig_rate)
    else:
        trig_prob = tail["trigger_prob"]

    thresholds = [float(t) for t in params["exceedance_thresholds_mps"]["value"]]
    exceed = {}
    for thr in thresholds:
        if min_wind is not None and thr < float(min_wind):
            exceed[thr] = None
            continue
        exceed[thr] = np.bincount(r_l, weights=rates[r_e] * (r_w >= thr), minlength=ctx.n_loc)
    wind_missing = np.bincount(r_l, weights=np.isnan(r_w).astype(float), minlength=ctx.n_loc) > 0
    if min_wind is None and thresholds:
        issues.add("exceedance_floor_unknown", "warn",
                   "payout_table.meta.min_reported_wind_mps not supplied; low thresholds may be "
                   "understated if C only emits rows above some wind level")

    # ---- uncertainty: bootstrap over observation years (keeps a year's storms together)
    boot = _bootstrap(ctx, method, basis, params)
    level = float(params["confidence_level"]["value"])
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2

    locations = []
    mean = main["mean"]
    second = main["second"]
    sd = np.sqrt(np.maximum(second - mean ** 2, 0.0))
    ref_cov = float(params["reference_coverage_php"]["value"])
    for lid in loc_ids_all:
        info = loc_info[lid]
        if lid in loc_bad:
            locations.append({"location_id": lid, "region": info["region"], "status": "not_computable",
                              "reason_code": loc_bad[lid]})
            continue
        i = loc_index[lid]
        cov_amt = info["coverage_amount_php"]
        rate_on_cov = mean[i] / cov_amt if cov_amt > 0 else None
        ci = None
        if boot is not None:
            ci = {"level": level, "lower": _f(np.quantile(boot["loc"][:, i], lo_q)),
                  "upper": _f(np.quantile(boot["loc"][:, i], hi_q))}
        locations.append({
            "location_id": lid,
            "region": info["region"],
            "status": "ok",
            "reason_code": None,
            "coverage_amount_php": cov_amt,
            "trigger_frequency_per_year": _f(trig_rate[i]),
            "conditional_trigger_rate": _f(trig_rate[i] / total_rate) if total_rate > 0 else None,
            "annual_trigger_probability": _f(trig_prob[i]) if confirmed_for_prob else None,
            "annual_trigger_probability_status": "computed" if confirmed_for_prob else "pending_confirmation",
            "expected_payout_per_event_basis": _f(per_event_mean[i] / total_rate) if total_rate > 0 else None,
            "expected_payout_given_trigger_basis": _f(per_event_mean[i] / trig_rate[i]) if trig_rate[i] > 0
            else None,
            "expected_annual_payout_basis": _f(mean[i]),
            "pure_risk_premium_basis": _f(mean[i]),
            "annual_payout_sd": _f(sd[i]),
            "rate_on_coverage": _f(rate_on_cov),
            "pure_premium_per_reference_coverage": _f(rate_on_cov * ref_cov) if rate_on_cov is not None
            else None,
            "pure_premium_ci": ci,
            "exceedance_frequency_per_year": [
                {"threshold_mps": thr,
                 "frequency_per_year": None if exceed[thr] is None or wind_missing[i] else _f(exceed[thr][i]),
                 "status": "ok" if exceed[thr] is not None and not wind_missing[i] else
                 ("below_reported_wind_floor" if exceed[thr] is None else "missing_wind_speed")}
                for thr in thresholds],
        })

    portfolio_mean = float(mean.sum())
    portfolio_sd = _portfolio_sd(ctx, method, basis, rates, tail)
    trig_event_rate = 0.0
    if ctx.n_ev:
        any_trig = np.zeros(ctx.n_ev)
        np.maximum.at(any_trig, r_e, (r_tier > 0).astype(float))
        trig_event_rate = float((rates * any_trig).sum())
    rp_table = []
    for t in params["return_periods_years"]["value"]:
        q = 1 - 1 / float(t)
        rp_table.append({
            "return_period_years": t,
            "aggregate_annual_payout_basis": _f(np.quantile(tail["totals"], q)) if len(tail["totals"]) else None,
            "largest_event_payout_basis": _f(np.quantile(tail["oep"], q)) if len(tail["oep"]) else None,
            "beyond_observation_period": bool(float(t) > n_years),
        })
    if any(r["beyond_observation_period"] for r in rp_table):
        issues.add("low_credibility_return_period", "info",
                   f"return periods longer than the {n_years}-year observation period are extrapolations")

    by_region = _region_summary(locations, params, ref_cov)
    excluded = [{"location_id": l["location_id"], "reason_code": l["reason_code"]}
                for l in locations if l["status"] != "ok"]
    if excluded:
        issues.add("partial_portfolio", "warn",
                   f"{len(excluded)} locations not computable; portfolio totals exclude them")

    risk_summary = {
        "event_frequency_per_year": _f(total_rate),
        "trigger_event_frequency_per_year": _f(trig_event_rate),
        "expected_payout_basis": _f(portfolio_mean),
        "pure_risk_premium_basis": _f(portfolio_mean),
        "annual_payout_sd": _f(portfolio_sd["value"]),
        "annual_payout_sd_method": portfolio_sd["method"],
        "pure_premium_ci": None if boot is None else {
            "level": level, "lower": _f(np.quantile(boot["portfolio"], lo_q)),
            "upper": _f(np.quantile(boot["portfolio"], hi_q)),
            "replicates": int(len(boot["portfolio"])), "method": boot["method"]},
        "return_period_table": rp_table,
        "tail_method": tail["method"],
        "by_region": by_region,
        "aggregation_basis": basis,
        "frequency_method": method,
        "calculation": main["approx"],
        "coverage": {"n_locations": len(loc_ids_all), "n_computable": len(computable),
                     "excluded": excluded},
        "notes": [
            "expected_payout_basis equals pure_risk_premium_basis by design: D adds no expenses, "
            "capital loading or margin.",
            "Amounts are unrounded PHP; rounding rules are pending confirmation and owned by the backend.",
        ],
    }

    sensitivity = _sensitivity(ctx, method, basis, params, portfolio_mean, sim_years, sim_seed, issues)
    checks = _candidate_checks(by_region, params)

    result = dict(base)
    result.update({
        "sample_period": {"start_year": period["start_year"], "end_year": period["end_year"],
                          "n_years": n_years},
        "frequency": freq_check | {
            "method": method,
            "source": params["frequency_source"]["value"],
            "event_frequency_per_year": _f(total_rate),
            "n_events": ctx.n_ev,
            "n_tracks": len(track_ids),
            "members_generated_values": sorted(set(int(m) for m in ctx.track_members)),
            "unit": "1/year",
        },
        "locations": locations,
        "risk_summary": risk_summary,
        "sensitivity": sensitivity,
        "candidate_threshold_checks": checks,
        "data_manifest": _manifest(data_manifest, params),
    })
    result["validation"] = _validation(issues, pending, n_excluded=len(excluded))
    result["output_hash"] = _hash({k: v for k, v in result.items() if k != "output_hash"})
    return result


# --------------------------------------------------------------------------- helpers
def _frequency_check(ev_ids, recomputed, supplied, params, issues, events, track_members):
    tol = float(params["frequency_rel_tolerance"]["value"])
    out = {"hazard_supplied_present": len(supplied), "max_relative_difference": None,
           "n_mismatched": 0, "climada_style_frequency": None}
    if supplied:
        diffs = []
        for i, e in enumerate(ev_ids):
            if e in supplied:
                rel = abs(supplied[e] - recomputed[i]) / recomputed[i]
                diffs.append(rel)
                if rel > tol:
                    out["n_mismatched"] += 1
                    issues.add("frequency_mismatch", "warn",
                               "B's frequency differs from 1/(observation years x members_generated); "
                               "check B's year span and ensemble count", e)
        out["max_relative_difference"] = _f(max(diffs)) if diffs else None
    if ev_ids:
        # CLIMADA TropCyclone.frequency_from_tracks: year span from the data, ensemble size from
        # events present / originals present. Shown so the difference is visible.
        seasons = [events[e]["season"] for e in ev_ids]
        span = max(seasons) - min(seasons) + 1
        n_orig = sum(1 for e in ev_ids if events[e]["member"] == 0)
        ens = len(ev_ids) / n_orig if n_orig else 1.0
        out["climada_style_frequency"] = {"year_span_from_data": span, "ensemble_size_from_data": ens,
                                          "frequency_each_event": _f(1.0 / (span * ens))}
    return out


def _tail(ctx, method, basis, sim_years, seed):
    """Annual portfolio totals and largest-event payout per year, plus per-location trigger probability."""
    if method == "poisson":
        if sim_years == 0 or ctx.n_ev == 0:
            return {"totals": np.zeros(0), "oep": np.zeros(0), "trigger_prob": np.zeros(ctx.n_loc),
                    "second": None, "method": "not_simulated"}
        sim = _simulate_poisson(ctx, ctx.ev_freq, basis, sim_years, seed + 1)
        return {"totals": sim["totals"], "oep": sim["oep"], "trigger_prob": sim["trigger_prob"],
                "second": sim["second"], "method": f"poisson_monte_carlo_{sim_years}_years"}
    k_year, k_loc, value, trig, totals, oep, m = _empirical_engine(ctx, basis)
    n_pseudo = ctx.n_years * m
    trig_prob = np.bincount(k_loc, weights=trig.astype(float), minlength=ctx.n_loc) / n_pseudo
    return {"totals": totals, "oep": oep, "trigger_prob": trig_prob, "second": None,
            "method": f"empirical_{n_pseudo}_pseudo_years ({ctx.n_years} seasons x {m} members)"}


def _portfolio_sd(ctx, method, basis, rates, tail):
    if method == "poisson" and basis == "per_event":
        # compound Poisson: Var(S) = sum_e rate_e * (portfolio payout of event e)^2
        return {"value": math.sqrt(float((rates * ctx.ev_total ** 2).sum())) if ctx.n_ev else 0.0,
                "method": "exact"}
    if len(tail["totals"]) == 0:
        return {"value": None if ctx.n_ev else 0.0, "method": "not_simulated"}
    return {"value": float(np.std(tail["totals"])), "method": tail["method"]}


def _bootstrap(ctx, method, basis, params):
    n_rep = int(params["bootstrap_replicates"]["value"])
    if n_rep == 0 or ctx.n_years < 2:
        return None
    rng = np.random.default_rng(int(params["bootstrap_seed"]["value"]))
    boot_sim = int(params["bootstrap_simulation_years"]["value"])
    loc_means = np.zeros((n_rep, ctx.n_loc))
    port = np.zeros(n_rep)
    for b in range(n_rep):
        draw = rng.integers(0, ctx.n_years, ctx.n_years)
        mult = np.bincount(draw, minlength=ctx.n_years).astype(float)
        res = _evaluate(ctx, method, basis, year_mult=mult, sim_years=max(boot_sim, 1),
                        seed=int(params["bootstrap_seed"]["value"]) + 1000 + b)
        loc_means[b] = res["mean"]
        port[b] = res["mean"].sum()
    label = "year_block_bootstrap"
    if method == "poisson" and basis == "aggregate_capped":
        label += f" (each replicate uses {boot_sim} simulated years, so includes Monte Carlo noise)"
    return {"loc": loc_means, "portfolio": port, "method": label}


def _sensitivity(ctx, method, basis, params, base_value, sim_years, seed, issues):
    out = {"base_pure_risk_premium_basis": _f(base_value), "items": []}

    def add(group, label, value, note=None):
        rel = (value / base_value - 1) if base_value else None
        out["items"].append({"group": group, "label": label, "pure_risk_premium_basis": _f(value),
                             "relative_change": _f(rel), "note": note})

    for alt in AGGREGATION_BASES:
        if alt != basis:
            add("aggregation_basis", alt, _evaluate(ctx, method, alt, sim_years=sim_years, seed=seed)["mean"].sum())
    alt_method = "empirical_years" if method == "poisson" else "poisson"
    if alt_method == "empirical_years" and not ctx.balanced:
        add("frequency_method", alt_method, float("nan"), "not available: ensemble is unbalanced")
    else:
        add("frequency_method", alt_method,
            _evaluate(ctx, alt_method, basis, sim_years=sim_years, seed=seed)["mean"].sum())

    periods = params["sensitivity_periods"]["value"]
    start = ctx.years[0]
    if periods is None and ctx.n_years >= 4:
        half = ctx.n_years // 2
        periods = [{"label": f"{start}-{start + half - 1}", "start_year": start, "end_year": start + half - 1},
                   {"label": f"{start + half}-{ctx.years[-1]}", "start_year": start + half,
                    "end_year": ctx.years[-1]}]
    for p in periods or []:
        s, e = max(p["start_year"], start), min(p["end_year"], ctx.years[-1])
        if s > e:
            add("observation_period", p["label"], float("nan"), "outside the base observation period")
            continue
        mult = np.zeros(ctx.n_years)
        mult[s - start:e - start + 1] = ctx.n_years / (e - s + 1)
        add("observation_period", p["label"],
            _evaluate(ctx, method, basis, year_mult=mult, sim_years=sim_years, seed=seed)["mean"].sum(),
            f"frequency re-estimated on {e - s + 1} seasons")

    # leave one historical storm (track with all its synthetic members) out
    if ctx.n_ev:
        contrib = np.bincount(ctx.ev_track, weights=ctx.ev_freq * ctx.ev_total, minlength=len(ctx.track_ids))
        top = np.argsort(-contrib, kind="stable")[: int(params["leave_one_out_top_n"]["value"])]
        for t in top:
            if contrib[t] <= 0:
                continue
            keep = ctx.ev_track != t
            add("leave_one_storm_out", ctx.track_ids[t],
                _evaluate(ctx, method, basis, keep=keep, sim_years=sim_years, seed=seed)["mean"].sum(),
                "pure premium without this historical storm and its synthetic variants")
    for mult in (0.5, 2.0):
        add("frequency_scale", f"x{mult}",
            _evaluate(ctx, method, basis, year_mult=np.full(ctx.n_years, mult),
                      sim_years=sim_years, seed=seed)["mean"].sum(),
            "all event frequencies scaled; tests the factor-of-two model-skill band")
    return out


def _region_summary(locations, params, ref_cov):
    groups = {}
    for loc in locations:
        if loc["status"] != "ok":
            continue
        g = groups.setdefault(loc["region"] or "unassigned", {"pp": 0.0, "cov": 0.0, "n": 0, "per_ref": []})
        g["pp"] += loc["pure_risk_premium_basis"]
        g["cov"] += loc["coverage_amount_php"]
        g["n"] += 1
        if loc["pure_premium_per_reference_coverage"] is not None:
            g["per_ref"].append(loc["pure_premium_per_reference_coverage"])
    out = []
    for region in sorted(groups):
        g = groups[region]
        out.append({"region": region, "n_locations": g["n"], "pure_risk_premium_basis": _f(g["pp"]),
                    "coverage_amount_php": _f(g["cov"]),
                    "rate_on_coverage": _f(g["pp"] / g["cov"]) if g["cov"] > 0 else None,
                    "pure_premium_per_reference_coverage": _f(g["pp"] / g["cov"] * ref_cov) if g["cov"] > 0
                    else None,
                    "max_location_pure_premium_per_reference_coverage": _f(max(g["per_ref"])) if g["per_ref"]
                    else None})
    return out


def _candidate_checks(by_region, params):
    p = params.get("candidate_affordability_threshold_php")
    if p is None:
        return None
    threshold = float(p["value"])
    ref = float(params["reference_coverage_php"]["value"])
    return {
        "threshold_php": threshold,
        "reference_coverage_php": ref,
        "status": p["status"],
        "interpretation": "informational only; not a pass/fail rule (handoff section 6)",
        "regions": [{"region": r["region"],
                     "pure_premium_per_reference_coverage": r["pure_premium_per_reference_coverage"],
                     "max_location_value": r["max_location_pure_premium_per_reference_coverage"],
                     "region_average_above_threshold": None if r["pure_premium_per_reference_coverage"] is None
                     else r["pure_premium_per_reference_coverage"] > threshold,
                     "any_location_above_threshold": None if r["max_location_pure_premium_per_reference_coverage"]
                     is None else r["max_location_pure_premium_per_reference_coverage"] > threshold}
                    for r in by_region],
    }


def _manifest(data_manifest, params):
    manifest = dict(data_manifest or {})
    manifest["component_d"] = {
        "component": COMPONENT,
        "component_version": COMPONENT_VERSION,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "config_hash": _hash([{k: v for k, v in p.items() if k not in ("parsed", "offset_hours")} for p in params.values()]),
        "simulation_seed": params["simulation_seed"]["value"],
        "bootstrap_seed": params["bootstrap_seed"]["value"],
    }
    return manifest


def _validation(issues, pending, n_excluded):
    items = issues.as_list()
    severities = issues.severities()
    if "fail" in severities:
        status = "fail"
    elif "warn" in severities or pending or n_excluded:
        status = "warn"
    else:
        status = "pass"
    warnings = [f"{i['code']}: {i['message']} ({i['count']})" for i in items if i["severity"] == "warn"]
    if pending:
        warnings.append("pending_confirmation: results are provisional and depend on " + ", ".join(pending))
    return {
        "status": status,
        "warnings": warnings,
        "errors": [f"{i['code']}: {i['message']} ({i['count']})" for i in items if i["severity"] == "fail"],
        "issues": items,
        "future_data_check": "rejected" if any(i["code"] == "future_data_rejected" for i in items) else "passed",
    }
