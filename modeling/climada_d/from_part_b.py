"""Adapter: Part B's hazard output -> Part D's event catalogue and hazard coverage.

Part B (``hazard_wind_b.compute_hazard_wind_field``, v0.1.0) returns ``events[]``,
``event_location_winds[]``, ``locations[]``, ``rejected_track_rows[]``, ``validation`` and a
``manifest``. D needs, per event, which real storm it belongs to, its ensemble member number,
how many members were generated, its season and a time stamp; and per store, whether B could
compute wind there at all. This module derives those fields and reports anything it had to
infer, so nothing is filled in silently.

Pure function: no network, no file I/O, inputs are not modified.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# B's codes for a store B refused before computing any wind (bad or duplicate input from A).
B_EXPOSURE_REJECTIONS = {"missing_coordinate", "unmapped_exposure", "duplicate_location_id"}
# B's statuses that are valid zero-wind results, not missing data.
B_ZERO_WIND = {"below_reporting_threshold", "outside_storm_reach", "outside_storm_circulation"}
# CLIMADA names synthetic tracks "<original sid>_gen<k>" (tc_tracks_synth.py, CLIMADA 6.1.0).
SYNTHETIC_SID = re.compile(r"^(?P<track>.+)_gen(?P<member>\d+)$")
# B's warning text when a storm cannot be modelled for lack of central pressure.
B_DROPPED_WARNING = re.compile(r"event (?P<eid>\S+): missing central pressure; event cannot be modelled")


def _year_utc(text):
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc).year


def from_part_b(b_output, *, members_generated=None):
    """Convert Part B's output into D's inputs.

    Parameters
    ----------
    b_output : dict
        The dict returned by B's ``compute_hazard_wind_field`` / ``hazard_for_historical_risk``.
    members_generated : int, optional
        Members per real storm, counting the original (CLIMADA ``nb_synth_tracks + 1``). Only
        needed when B adds synthetic tracks and does not put ``members_generated`` on each event.

    Returns
    -------
    dict with ``event_catalogue``, ``hazard_coverage``, ``hazard_version``,
    ``min_reported_wind_mps`` (pass to C for its ``payout_table.meta``) and ``upstream_issues``
    (pass to D's ``aggregate_historical_risk(..., upstream_issues=...)``).
    """
    issues = []

    def issue(code, severity, message, ids=None):
        issues.append({"code": code, "severity": severity, "message": message,
                       "ids": sorted(str(i) for i in ids)[:50] if ids else []})

    manifest = b_output.get("manifest") or {}
    hazard_version = manifest.get("output_hash")
    if not hazard_version:
        issue("missing_field", "fail", "Part B manifest has no output_hash to use as hazard_version")
    validation = b_output.get("validation") or {}
    if validation.get("status") == "fail":
        issue("upstream_failed", "fail", "Part B validation status is fail; D will not price on it",
              [w for w in validation.get("warnings", [])][:5])
    future_rejected = manifest.get("future_data_check") == "rejected"
    rejected_future_ids = [
        r.get("event_id") or r.get("row") for r in (b_output.get("rejected_track_rows") or [])
        if r.get("reason_code") == "future_data_rejected"
    ]
    if future_rejected or rejected_future_ids:
        issue("future_data_rejected", "fail",
              "Part B rejected track information later than the shared as_of; D will not price this run",
              rejected_future_ids)
    if manifest.get("engine") == "fixture" or (manifest.get("config") or {}).get("engine") == "fixture":
        issue("fixture_hazard", "warn", "Part B ran its fixture engine: winds are test data, not CLIMADA output")

    # ---------------------------------------------------------------- events
    events_meta = b_output.get("events") or []
    modelled = {str(e["event_id"]) for e in events_meta}
    any_synthetic = members_generated is not None or any(
        SYNTHETIC_SID.match(str(e["event_id"])) or e.get("member_index") not in (None, 0) for e in events_meta)
    if members_generated is not None and (not isinstance(members_generated, int) or members_generated < 1):
        issue("invalid_parameter", "fail", "members_generated must be a positive integer")
        members_generated = None

    catalogue, season_inferred, explicit_members = [], [], 0
    for ev in events_meta:
        eid = str(ev["event_id"])
        match = SYNTHETIC_SID.match(eid)
        track_id = ev.get("track_id") or (match.group("track") if match else eid)
        member = ev.get("member_index")
        if member is None:
            member = int(match.group("member")) if match else 0
        season = ev.get("season_year")
        if season is None:
            season = _year_utc(ev.get("first_observation"))
            season_inferred.append(eid)
        mg = ev.get("members_generated")
        if mg is not None:
            explicit_members += 1
        elif members_generated is not None:
            mg = members_generated
        elif not any_synthetic:
            mg = 1  # historical tracks only: one member per storm
        row = {"event_id": eid, "track_id": str(track_id), "member_index": member,
               "season_year": season, "reference_time": ev.get("first_observation"),
               "hazard_version": hazard_version, "name": ev.get("name")}
        if mg is not None:
            row["members_generated"] = mg  # left out only when unknown, so D raises ensemble_size_inferred
        catalogue.append(row)

    if season_inferred:
        issue("season_from_first_observation", "info",
              "season_year taken from the UTC year of the first track point; ask B to pass IBTrACS SEASON",
              season_inferred)
    if not any_synthetic and events_meta:
        issue("historical_tracks_only", "info",
              "no synthetic members found: each storm is one event with frequency 1/(observation years)")
    if any_synthetic and members_generated is None and explicit_members < len(events_meta):
        issue("members_generated_missing", "warn",
              "synthetic tracks present but members_generated not supplied; D will infer it from the "
              "highest member number it sees")

    # storms B could not model: whole-event rejections and missing-pressure warnings
    rejected = b_output.get("rejected_track_rows") or []
    dropped = {str(r["event_id"]) for r in rejected
               if r.get("event_id") is not None and r.get("reason_code") != "future_data_rejected"}
    structured_dropped = b_output.get("dropped_events") or []
    dropped.update(str(r["event_id"]) for r in structured_dropped if r.get("event_id") is not None)
    for text in validation.get("warnings", []):
        found = B_DROPPED_WARNING.search(str(text))
        if found:
            dropped.add(found.group("eid"))
    dropped -= modelled
    if dropped:
        issue("hazard_events_dropped", "warn",
              f"{len(dropped)} storm(s) were not modelled by B; frequency and pure premium are "
              "understated unless these storms never reach the portfolio", dropped)
    partial = {str(r["event_id"]) for r in rejected if r.get("event_id") is not None} & modelled
    if partial:
        issue("track_points_rejected", "info",
              "some track points were rejected by B but the storm was still modelled", partial)

    # ---------------------------------------------------------------- store coverage
    status = {}
    for row in b_output.get("locations") or []:
        lid = row.get("location_id")
        if lid is None:
            continue
        lid = str(lid)
        reason = row.get("reason_code")
        if reason in B_EXPOSURE_REJECTIONS:
            status[lid] = reason  # a rejection always wins, including the duplicate case
        else:
            status.setdefault(lid, "ok")
    for row in b_output.get("event_location_winds") or []:
        lid = str(row["location_id"])
        if status.get(lid, "ok") != "ok":
            continue
        if row.get("data_status") not in B_ZERO_WIND | {"ok"}:
            status[lid] = row.get("reason_code") or "missing_hazard"
        else:
            status.setdefault(lid, "ok")
    coverage = [{"location_id": lid, "data_status": s, "hazard_version": hazard_version}
                for lid, s in sorted(status.items())]

    config = manifest.get("config") or {}
    return {
        "event_catalogue": catalogue,
        "hazard_coverage": coverage,
        "hazard_version": hazard_version,
        "min_reported_wind_mps": config.get("intensity_threshold_mps"),
        "upstream_issues": issues,
    }
