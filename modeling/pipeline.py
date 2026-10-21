"""One product-facing A -> B -> C -> D historical-model boundary."""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Optional

from .climada_input import build_exposure_input
from .climada_trigger import compute_trigger_basis
from .climada_d import aggregate_historical_risk
from .climada_d.from_part_a import from_part_a, rule_version_issues
from .climada_d.from_part_b import from_part_b
from .hazard_wind_b import HazardConfig, hazard_for_historical_risk

PIPELINE_VERSION = "climada-model-pipeline/0.1.0"


def _hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _status(*statuses: Optional[str]) -> str:
    values = set(statuses)
    if "fail" in values:
        return "fail"
    if "warn" in values:
        return "warn"
    return "pass"


def run_historical_model(
    snapshot: dict,
    historical_tracks: list[dict],
    hazard_config: HazardConfig,
    rule_config: dict,
    risk_config: dict,
    *,
    as_of: str,
    data_manifest: dict,
    model_run_id: Optional[str] = None,
    members_generated: Optional[int] = None,
) -> dict:
    """Run the four teammate modules and return one immutable ModelRun-shaped result.

    The caller supplies frozen data and every configuration.  This function performs
    no network access, LLM calls, database writes or business settlement decisions.
    """
    envelope = {
        "pipeline_version": PIPELINE_VERSION,
        "snapshot": snapshot,
        "historical_tracks": historical_tracks,
        "hazard_config": hazard_config,
        "rule_config": rule_config,
        "risk_config": risk_config,
        "as_of": as_of,
        "data_manifest": data_manifest,
        "members_generated": members_generated,
    }
    input_hash = _hash(envelope)
    run_id = model_run_id or f"model-run-{input_hash.removeprefix('sha256:')[:16]}"

    a_output = build_exposure_input(snapshot, as_of=as_of, data_manifest=data_manifest)
    a_for_d = from_part_a(a_output)

    b_output = hazard_for_historical_risk(
        a_for_d["exposure_for_b"], historical_tracks, hazard_config,
        as_of=as_of, data_manifest=a_output.get("data_manifest") or data_manifest,
        run_id=run_id,
    )
    b_for_d = from_part_b(b_output, members_generated=members_generated)

    c_output = compute_trigger_basis(a_output, b_output, rule_config, as_of=as_of)

    d_config = copy.deepcopy(risk_config)
    if "coverage_window" not in d_config and a_for_d.get("coverage_window") is not None:
        d_config["coverage_window"] = {
            "value": a_for_d["coverage_window"],
            "status": "pending_confirmation",
            "source": "Part A policy coverage windows",
        }
    upstream_issues = [*a_for_d["upstream_issues"], *b_for_d["upstream_issues"],
                       *rule_version_issues(a_for_d, c_output["payout_table"])]
    if c_output.get("validation", {}).get("status") == "fail":
        upstream_issues.append({
            "code": "trigger_stage_failed", "severity": "fail",
            "message": "Part C validation failed; historical pricing is not computable",
            "ids": [],
        })

    d_output = aggregate_historical_risk(
        a_for_d["exposure_for_d"], b_for_d["event_catalogue"],
        b_for_d["hazard_coverage"], c_output["payout_table"], d_config,
        as_of=as_of, data_manifest=b_output.get("manifest") or {},
        upstream_issues=upstream_issues,
    )

    stages = {"input": a_output, "hazard": b_output, "trigger": c_output,
              "historical_risk": d_output}
    validation = {
        "status": _status(
            a_output.get("validation", {}).get("status"),
            b_output.get("validation", {}).get("status"),
            c_output.get("validation", {}).get("status"),
            d_output.get("validation", {}).get("status"),
        ),
        "stages": {
            key: value.get("validation", {})
            for key, value in stages.items()
        },
        "warnings": sorted({
            str(warning)
            for value in stages.values()
            for warning in value.get("validation", {}).get("warnings", [])
        }),
    }
    result = {
        "model_run_id": run_id,
        "model_version": PIPELINE_VERSION,
        "rule_version": rule_config.get("rule_version"),
        "as_of": as_of,
        "input_hash": input_hash,
        "data_manifest": copy.deepcopy(data_manifest),
        "locations": d_output.get("locations", []),
        "event_location_results": c_output.get("results", []),
        "risk_summary": d_output.get("risk_summary"),
        "validation": validation,
        "stage_hashes": {
            "input": a_output.get("output_hash"),
            "hazard": b_output.get("manifest", {}).get("output_hash"),
            "trigger": c_output.get("manifest", {}).get("output_hash"),
            "historical_risk": d_output.get("output_hash"),
        },
        "stages": stages,
    }
    result["output_hash"] = _hash({k: v for k, v in result.items() if k != "output_hash"})
    return result
