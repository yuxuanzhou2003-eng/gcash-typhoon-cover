"""Adapter: Part A's exposure input -> Part B's location list and Part D's exposure.

Part A (``climada_input.build_exposure_input``, role-a-adapter/0.1.0) returns one row per in-force
policy with ``location_id``, coordinates, ``psgc_barangay_code``, ``coverage_start`` /
``coverage_end`` (UTC) and ``coverage_amount_php``. This module:

* gives B one row per physical store (B rejects repeated ``location_id``),
* gives D ``location_id``, ``coverage_amount_php`` and ``region`` (from the PSGC region code),
* reads the coverage window off the policies, in Philippine local dates (UTC+8), so D tests
  storms against the same calendar dates the policy uses,
* reports anything it could not map, instead of filling it in.

Pure function: no network, no file I/O, inputs are not modified.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# PSGC region codes: the first two digits of the 10-digit code (short codes are zero-padded, so
# "08" == "0800000000"). Source: psgc package vignette (CRAN), PSA PSGC as packaged in 2026.
PSGC_REGIONS = {
    "01": "Ilocos Region", "02": "Cagayan Valley", "03": "Central Luzon", "04": "CALABARZON",
    "05": "Bicol Region", "06": "Western Visayas", "07": "Central Visayas", "08": "Eastern Visayas",
    "09": "Zamboanga Peninsula", "10": "Northern Mindanao", "11": "Davao Region", "12": "SOCCSKSARGEN",
    "13": "National Capital Region", "14": "Cordillera Administrative Region", "16": "Caraga",
    "17": "MIMAROPA", "18": "Negros Island Region", "19": "BARMM",
}
PHT_OFFSET_HOURS = 8


def _parse(text):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def from_part_a(a_output, *, utc_offset_hours=PHT_OFFSET_HOURS):
    """Convert Part A's output into B's and D's inputs.

    Returns dict with ``exposure_for_b``, ``exposure_for_d``, ``coverage_window`` (a value for
    D's ``risk_config["coverage_window"]``, or None if the policies disagree), ``rule_version``
    (C's ``payout_table.meta.rule_version`` must equal it) and ``upstream_issues``.
    """
    issues = []

    def issue(code, severity, message, ids=None):
        issues.append({"code": code, "severity": severity, "message": message,
                       "ids": sorted(str(i) for i in ids)[:50] if ids else []})

    validation = a_output.get("validation") or {}
    if validation.get("status") == "fail":
        issue("upstream_failed", "fail", "Part A validation status is fail; D will not price on it")
    rejected = a_output.get("rejected") or []
    if rejected:
        counts = validation.get("reason_counts") or {}
        summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        issue("exposure_rejected_by_a", "warn",
              f"Part A rejected {len(rejected)} record(s) before pricing ({summary}); they are not in "
              "the portfolio totals", [r.get("record_id") for r in rejected])

    rows = sorted(a_output.get("exposure") or [], key=lambda r: str(r.get("exposure_id")))
    by_location = {}
    for row in rows:
        by_location.setdefault(str(row["location_id"]), []).append(row)
    multi = [lid for lid, group in by_location.items() if len(group) > 1]
    if multi:
        issue("multiple_coverages_per_location", "warn",
              "more than one in-force policy on the same store; D excludes these stores until the "
              "group decides whether to price per policy or per store", multi)

    exposure_for_b, exposure_for_d, no_region, windows = [], [], [], {}
    for lid, group in sorted(by_location.items()):
        first = group[0]
        exposure_for_b.append({"location_id": lid, "latitude": first["latitude"],
                               "longitude": first["longitude"]})
        for row in group:
            code = str(row.get("psgc_barangay_code") or "")
            region = PSGC_REGIONS.get(code[:2]) if len(code) == 10 and code.isdigit() else None
            if region is None:
                no_region.append(lid)
            exposure_for_d.append({"location_id": lid, "coverage_amount_php": row.get("coverage_amount_php"),
                                   "region": region, "coverage_id": row.get("coverage_id"),
                                   "exposure_id": row.get("exposure_id")})
            start, end = _parse(row.get("coverage_start")), _parse(row.get("coverage_end"))
            if start is None or end is None or end <= start:
                windows.setdefault(None, []).append(lid)
                continue
            local = timezone(timedelta(hours=utc_offset_hours))
            start_l = start.astimezone(local)
            last_l = (end - timedelta(seconds=1)).astimezone(local)  # coverage_end is exclusive
            if last_l - start_l >= timedelta(days=365):
                windows.setdefault("full_year", []).append(lid)
            else:
                windows.setdefault((start_l.strftime("%m-%d"), last_l.strftime("%m-%d")), []).append(lid)

    if no_region:
        issue("region_unmapped", "info", "no valid 10-digit PSGC code; store reported under 'unassigned'",
              no_region)

    coverage_window = None
    if len(windows) == 1 and None not in windows:
        key = next(iter(windows))
        if key == "full_year":
            coverage_window = {"start_md": "01-01", "end_md": "12-31", "utc_offset_hours": utc_offset_hours}
        else:
            coverage_window = {"start_md": key[0], "end_md": key[1], "utc_offset_hours": utc_offset_hours}
    elif windows:
        issue("coverage_windows_differ", "warn",
              f"policies use {len(windows)} different coverage windows; D prices one window, so the group "
              "must pick one", [lid for group in windows.values() for lid in group])

    return {
        "exposure_for_b": exposure_for_b,
        "exposure_for_d": exposure_for_d,
        "coverage_window": coverage_window,
        "rule_version": a_output.get("rule_version"),
        "upstream_issues": issues,
    }


def rule_version_issues(a_result, payout_table):
    """C's payout rule version must be the one A's policies were written under."""
    expected = a_result.get("rule_version")
    actual = ((payout_table or {}).get("meta") or {}).get("rule_version")
    if not expected or not actual or actual != expected:
        return [{"code": "rule_version_mismatch", "severity": "fail",
                 "message": f"Part C rule_version {actual!r} differs from Part A policies {expected!r}",
                 "ids": []}]
    return []
