"""Role C - candidate rule configuration schema (v0.2).

A rule config is the "form" that holds one candidate set of trigger and payout
rules. The engine contains no rule numbers; it only reads them from here.

BUSINESS_RULES.en.md section 5: during the prototype Role C may design and
compare candidate configurations, each labelled `status: experimental`, never
named official/approved/production. Product decides which, if any, is promoted.

Any field set to "pending_confirmation" is treated as NOT DECIDED; the engine
then returns `rule_pending_confirmation` instead of a number.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

PENDING = "pending_confirmation"

RULE_STATUSES = {"experimental", PENDING, "test_fixture", "confirmed"}
BOUNDARIES = {"inclusive", "exclusive"}          # inclusive: wind >= threshold triggers
WINDOW_RULES = {"event_overlaps_window", "event_within_window", "hazard_window_max"}
COVERAGE_WINDOW_MODES = {"literal", "recurring_month_day"}
OUTSIDE_REACH_TREATMENTS = {"no_trigger", "not_computable"}   # legacy B status outside_storm_reach
ROUNDING_MODES = {"half_up", "down"}
FORBIDDEN_NAME_WORDS = re.compile(r"official|approved|production", re.IGNORECASE)

REQUIRED_FOR_PAYOUT = (
    "wind_metric", "tiers", "threshold_boundary", "coverage_window_rule",
    "outside_reach_treatment", "rounding",
)


def is_pending(v: Any) -> bool:
    return v is None or v == PENDING


def config_hash(cfg: dict) -> str:
    payload = json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def pending_fields(cfg: dict) -> list[str]:
    """Required fields that are still undecided."""
    out = [k for k in REQUIRED_FOR_PAYOUT if is_pending(cfg.get(k))]
    # per_event_cap_php: null = no model-side cap in this candidate; "pending_confirmation" blocks.
    if cfg.get("per_event_cap_php", PENDING) == PENDING:
        out.append("per_event_cap_php")
    tiers = cfg.get("tiers")
    if isinstance(tiers, list):
        for t in tiers:
            for k in ("threshold_mps", "benefit_days"):
                if is_pending(t.get(k)):
                    out.append(f"tiers[{t.get('tier')}].{k}")
    r = cfg.get("rounding")
    if isinstance(r, dict):
        for k in ("unit_php", "mode"):
            if is_pending(r.get(k)):
                out.append(f"rounding.{k}")
    if "coverage_window_mode" in cfg and is_pending(cfg.get("coverage_window_mode")):
        out.append("coverage_window_mode")
    if "coverage_window_timezone_offset_hours" in cfg \
            and is_pending(cfg.get("coverage_window_timezone_offset_hours")):
        out.append("coverage_window_timezone_offset_hours")
    return sorted(set(out))


def validate_rule_config(cfg: dict) -> None:
    """Raise ValueError if the config is malformed (a programming error, not a pending value)."""
    for k in ("rule_version", "candidate_id", "rule_status"):
        if not cfg.get(k):
            raise ValueError(f"rule config missing {k}")
    if cfg["rule_status"] not in RULE_STATUSES:
        raise ValueError(f"rule_status must be one of {sorted(RULE_STATUSES)}")
    if cfg["rule_status"] != "confirmed":
        for k in ("rule_version", "candidate_id"):
            if FORBIDDEN_NAME_WORDS.search(str(cfg[k])):
                raise ValueError(f"{k} must not contain official/approved/production")
    if cfg.get("wind_unit", "m/s") != "m/s":
        raise ValueError("rule thresholds must be expressed in m/s")

    def check_enum(key, allowed):
        v = cfg.get(key)
        if not is_pending(v) and v not in allowed:
            raise ValueError(f"{key}={v!r} not in {sorted(allowed)}")

    check_enum("threshold_boundary", BOUNDARIES)
    check_enum("coverage_window_rule", WINDOW_RULES)
    check_enum("coverage_window_mode", COVERAGE_WINDOW_MODES)
    check_enum("outside_reach_treatment", OUTSIDE_REACH_TREATMENTS)

    tz_offset = cfg.get("coverage_window_timezone_offset_hours")
    if not is_pending(tz_offset) and tz_offset is not None \
            and (not isinstance(tz_offset, (int, float)) or isinstance(tz_offset, bool)
                 or not math.isfinite(tz_offset) or not -24 <= tz_offset <= 24):
        raise ValueError("coverage_window_timezone_offset_hours must be between -24 and 24")

    tiers = cfg.get("tiers")
    if not is_pending(tiers):
        if not isinstance(tiers, list) or not tiers:
            raise ValueError("tiers must be a non-empty list or pending")
        if [t.get("tier") for t in tiers] != list(range(1, len(tiers) + 1)):
            raise ValueError("tiers must be numbered 1..n in order")
        if all(not is_pending(t.get("threshold_mps")) and not is_pending(t.get("benefit_days")) for t in tiers):
            th = [t["threshold_mps"] for t in tiers]
            bd = [t["benefit_days"] for t in tiers]
            if any(x <= 0 for x in th) or any(b <= a for a, b in zip(th, th[1:])):
                raise ValueError("tier thresholds must be positive and strictly increasing")
            if any((not isinstance(x, int)) or x <= 0 for x in bd) or any(b < a for a, b in zip(bd, bd[1:])):
                raise ValueError("benefit_days must be positive whole days and non-decreasing")

    cap = cfg.get("per_event_cap_php", PENDING)
    if cap not in (None, PENDING) and (not isinstance(cap, (int, float)) or cap <= 0):
        raise ValueError("per_event_cap_php must be positive, null or pending")

    r = cfg.get("rounding")
    if isinstance(r, dict):
        if not is_pending(r.get("mode")) and r["mode"] not in ROUNDING_MODES:
            raise ValueError(f"rounding.mode not in {sorted(ROUNDING_MODES)}")
        if not is_pending(r.get("unit_php")) and r["unit_php"] <= 0:
            raise ValueError("rounding.unit_php must be positive")
