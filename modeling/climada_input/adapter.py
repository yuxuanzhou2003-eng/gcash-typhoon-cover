"""Role A - deterministic input adapter.

Turns a frozen snapshot of merchant, location and coverage records into the
common model exposure input (MODEL_HANDOFF.en.md section 3).

Rules:
- Pure function: no network, no LLM, no database writes, no clock reads.
- Never invents product terms. Missing or conflicting data is returned with a
  reason_code and is not filled in.
- Does not compute wind, triggers, payouts or prices.
- Merchant personal fields never reach the output.
"""
from __future__ import annotations
import hashlib, json, math, re
from datetime import datetime, timezone

ADAPTER_VERSION = "role-a-adapter/0.2.1"
SCHEMA_VERSION = "exposure-input/0.2.0"

# how the coordinate was produced; nominal radius is for Role B uncertainty only
PRECISION = {"store_address_geocoded": 50, "barangay_centroid": 1000,
             "municipality_centroid": 5000}

# PH bounding box (WGS84), generous, for basic sanity only.
PH_LON = (114.0, 127.5)
PH_LAT = (4.0, 21.5)
PSGC_RE = re.compile(r"^\d{10}$")
ALLOWED_CURRENCY = {"PHP"}
ACTIVE_STATUSES = {"in_force"}
SENSITIVE_FIELDS = {"merchant_name", "owner_name", "mobile_number", "email",
                    "address_line", "tin", "gcash_account_no"}

REASON = {  # handoff-suggested codes + adapter-specific ones
    "missing_coordinate", "unmapped_exposure", "coverage_gap",
    "version_conflict", "future_data_rejected", "invalid_unit",
    "invalid_coordinate", "invalid_psgc", "duplicate_record",
    "inactive_coverage", "missing_field",
}


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(obj) -> str:
    return "sha256:" + hashlib.sha256(_canon(obj).encode()).hexdigest()


def _ts(s):
    if not s:
        return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    if d.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {s}")
    return d.astimezone(timezone.utc)


def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def build_exposure_input(snapshot: dict, *, as_of: str, data_manifest: dict) -> dict:
    """Validate and transform a frozen snapshot into the model exposure input.

    snapshot = {"snapshot_id", "data_version", "rule_version",
                "merchants": [...], "locations": [...], "coverages": [...]}
    """
    if not isinstance(snapshot, dict):
        raise ValueError("snapshot must be an object")
    if not isinstance(data_manifest, dict):
        raise ValueError("data_manifest must be an object")
    cutoff = _ts(as_of)
    rejected, warnings = [], []

    def reject(kind, rid, code, detail):
        rejected.append({"record_type": kind, "record_id": rid,
                         "reason_code": code, "detail": detail})

    # A run without both version boundaries is not traceable and must not
    # silently bypass the equality check below.
    missing_versions = [
        key for key in ("data_version", "rule_version")
        if snapshot.get(key) in (None, "") or data_manifest.get(key) in (None, "")
    ]
    if missing_versions:
        return _result(snapshot, as_of, data_manifest, [], [{
            "record_type": "snapshot", "record_id": snapshot.get("snapshot_id"),
            "reason_code": "missing_field",
            "detail": "missing required version boundary: " + ", ".join(missing_versions),
        }], ["snapshot rejected: missing version boundary"], status="fail")

    # version check against the manifest the caller froze
    for key in ("data_version", "rule_version"):
        if key in data_manifest and snapshot.get(key) != data_manifest[key]:
            return _result(snapshot, as_of, data_manifest, [], [{
                "record_type": "snapshot", "record_id": snapshot.get("snapshot_id"),
                "reason_code": "version_conflict",
                "detail": f"{key}: snapshot={snapshot.get(key)} manifest={data_manifest[key]}"}],
                ["snapshot rejected: version_conflict"], status="fail")

    def visible(rec, kind, rid):
        rt = rec.get("recorded_at")
        if rt is None:
            reject(kind, rid, "missing_field", "recorded_at"); return False
        try:
            recorded = _ts(rt)
        except (TypeError, ValueError, OverflowError) as exc:
            reject(kind, rid, "invalid_unit", f"recorded_at: {exc}"); return False
        if recorded > cutoff:
            reject(kind, rid, "future_data_rejected", f"recorded_at {rt} > as_of"); return False
        return True

    def dedupe(rows, key, kind):
        seen, out = {}, {}
        for r in sorted(rows, key=lambda r: (str(r.get(key)), str(r.get("recorded_at")))):
            rid = r.get(key)
            if rid is None:
                reject(kind, None, "missing_field", key); continue
            if not visible(r, kind, rid):
                continue
            if rid in seen:
                if _canon(_strip(seen[rid])) != _canon(_strip(r)):
                    reject(kind, rid, "duplicate_record", "conflicting versions; both excluded")
                    out.pop(rid, None)
                continue
            seen[rid] = r; out[rid] = r
        return out

    merchants = dedupe(snapshot.get("merchants", []), "merchant_id", "merchant")
    locations = dedupe(snapshot.get("locations", []), "location_id", "location")
    coverages = dedupe(snapshot.get("coverages", []), "coverage_id", "coverage")

    # location validation
    good_loc = {}
    for lid, loc in locations.items():
        lon, lat = _num(loc.get("longitude")), _num(loc.get("latitude"))
        if lon is None or lat is None:
            reject("location", lid, "missing_coordinate", "longitude/latitude"); continue
        if loc.get("crs", "EPSG:4326") != "EPSG:4326":
            reject("location", lid, "invalid_unit", f"crs={loc.get('crs')}"); continue
        if not (PH_LON[0] <= lon <= PH_LON[1] and PH_LAT[0] <= lat <= PH_LAT[1]):
            reject("location", lid, "invalid_coordinate", f"({lon},{lat}) outside PH bbox"); continue
        psgc = loc.get("psgc_barangay_code")
        if psgc is not None and not PSGC_RE.match(str(psgc)):
            reject("location", lid, "invalid_psgc", f"{psgc!r} is not 10 digits"); continue
        prec = loc.get("location_precision")
        if prec not in PRECISION:
            reject("location", lid, "missing_field" if prec is None else "invalid_unit",
                   f"location_precision={prec!r}"); continue
        if loc.get("merchant_id") not in merchants:
            reject("location", lid, "unmapped_exposure", "merchant not in snapshot"); continue
        good_loc[lid] = loc

    exposure = []
    for cid, cov in coverages.items():
        if cov.get("status") not in ACTIVE_STATUSES:
            reject("coverage", cid, "inactive_coverage", f"status={cov.get('status')}"); continue
        lid = cov.get("location_id")
        if lid not in good_loc:
            reject("coverage", cid, "unmapped_exposure", f"location {lid} missing or rejected"); continue
        loc = good_loc[lid]
        if cov.get("merchant_id") != loc.get("merchant_id"):
            reject("coverage", cid, "unmapped_exposure", "coverage/location merchant mismatch"); continue
        try:
            start, end = _ts(cov.get("coverage_start")), _ts(cov.get("coverage_end"))
        except ValueError as e:
            reject("coverage", cid, "invalid_unit", str(e)); continue
        if not start or not end or end <= start:
            reject("coverage", cid, "coverage_gap", "missing or non-positive coverage window"); continue
        cur = cov.get("currency")
        if cur not in ALLOWED_CURRENCY:
            reject("coverage", cid, "invalid_unit", f"currency={cur}"); continue
        amt = _num(cov.get("coverage_amount"))
        if amt is None or amt <= 0:
            # do NOT derive from daily collections: tier multiple and cap are pending_confirmation
            reject("coverage", cid, "coverage_gap", "coverage_amount missing; not derived"); continue
        daily_basis_present = "daily_collection_basis_php" in cov
        daily_basis = _num(cov.get("daily_collection_basis_php")) if daily_basis_present else None
        if daily_basis_present and (daily_basis is None or daily_basis <= 0):
            reject("coverage", cid, "invalid_unit",
                   "daily_collection_basis_php must be finite and > 0"); continue
        if cov.get("rule_version") not in (None, snapshot.get("rule_version")):
            reject("coverage", cid, "version_conflict",
                   f"coverage rule_version {cov.get('rule_version')}"); continue
        if start > cutoff:
            warnings.append(f"{cid}: coverage starts after as_of (kept; C/D decide applicability)")
        row = {
            "exposure_id": f"{cid}:{lid}",
            "location_id": lid,
            "coverage_id": cid,
            "longitude": round(_num(loc["longitude"]), 6),
            "latitude": round(_num(loc["latitude"]), 6),
            "psgc_barangay_code": loc.get("psgc_barangay_code"),
            "location_precision": loc["location_precision"],
            "precision_radius_m": PRECISION[loc["location_precision"]],
            "coverage_start": _iso(start),
            "coverage_end": _iso(end),
            "coverage_amount_php": amt,
            "currency": "PHP",
            "data_status": "ok",
        }
        # Collections are an optional underwriting input. Preserve the supplied
        # value and its audit context; never infer it from coverage_amount.
        if daily_basis_present:
            row["daily_collection_basis_php"] = daily_basis
            for field in ("daily_collection_basis_definition",
                          "daily_collection_basis_observation_window",
                          "daily_collection_basis_provenance"):
                if field in cov:
                    row[field] = cov[field]
        exposure.append(row)

    leaked = [k for row in exposure for k in row if k in SENSITIVE_FIELDS]
    assert not leaked, leaked
    status = "pass" if not rejected else ("warn" if exposure else "fail")
    return _result(snapshot, as_of, data_manifest, exposure, rejected, warnings, status)


def _strip(r):
    return {k: v for k, v in r.items() if k != "recorded_at"}


def _result(snapshot, as_of, manifest, exposure, rejected, warnings, status="pass"):
    exposure = sorted(exposure, key=lambda r: r["exposure_id"])
    rejected = sorted(rejected, key=lambda r: (r["record_type"], str(r["record_id"]), r["reason_code"]))
    counts = {}
    for r in rejected:
        counts[r["reason_code"]] = counts.get(r["reason_code"], 0) + 1
    body = {
        "schema_version": SCHEMA_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "snapshot_id": snapshot.get("snapshot_id"),
        "data_version": snapshot.get("data_version"),
        "rule_version": snapshot.get("rule_version"),
        "as_of": as_of,
        "units": {"coordinates": "WGS84 decimal degrees", "amount": "PHP",
                  "time": "ISO 8601 UTC"},
        "input_hash": _sha({"snapshot": snapshot, "as_of": as_of, "data_manifest": manifest}),
        "data_manifest": manifest,
        "exposure": exposure,
        "rejected": rejected,
        "validation": {"status": status, "accepted": len(exposure),
                       "rejected": len(rejected), "reason_counts": counts,
                       "warnings": sorted(warnings)},
    }
    body["output_hash"] = _sha({k: body[k] for k in (
        "schema_version", "adapter_version", "snapshot_id", "data_version", "rule_version",
        "as_of", "data_manifest", "exposure", "rejected", "validation",
    )})
    return body


def location_rows(result: dict) -> list[dict]:
    """Unique location list for accepted exposure (one row per location_id)."""
    seen = {}
    for e in result["exposure"]:
        seen.setdefault(e["location_id"], {k: e[k] for k in (
            "location_id", "latitude", "longitude", "location_precision",
            "precision_radius_m", "psgc_barangay_code")})
    return [seen[k] for k in sorted(seen)]
