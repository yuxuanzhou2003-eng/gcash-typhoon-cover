from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def snapshot(*, status: str = "completed", simulated: bool = True) -> dict:
    return {
        "portfolio": {
            "id": "p-1",
            "name": "Demo portfolio",
            "version": 2,
            "source": "demo",
            "createdAt": "2026-10-01T00:00:00Z",
            "merchants": [
                {
                    "id": "m-1",
                    "name": "Merchant One",
                    "region": "Metro Manila",
                    "lat": 14.6,
                    "lng": 121.0,
                    "sumInsured": 10000,
                    "coverageStart": "2026-01-01T00:00:00Z",
                    "coverageEnd": "2026-12-31T23:59:59Z",
                }
            ],
        },
        "storm": {"id": "s-1", "name": "Demo storm"},
        "bulletin": {
            "id": "b-1",
            "issuedAt": "2026-10-10T00:00:00Z",
            "label": "Bulletin 1",
            "wind": 35.0,
            "center": {"lat": 14.5, "lng": 121.0},
            "track": [{"lat": 14.5, "lng": 121.0}],
            "phase": "forecast",
            "source": "synthetic bulletin",
        },
        "rule": {
            "id": "r-1",
            "name": "Demo rule",
            "version": "1",
            "tiers": [{"wind": 30, "fraction": 0.5}],
            "expenseRate": 0.1,
            "annualEventRate": 0.2,
            "currency": "PHP",
            "source": "synthetic rule",
        },
        "modelRun": {
            "id": "mr-1",
            "portfolioId": "p-1",
            "portfolioVersion": 2,
            "stormId": "s-1",
            "bulletinId": "b-1",
            "ruleId": "r-1",
            "asOf": "2026-10-10T00:00:00Z",
            "createdAt": "2026-10-10T00:01:00Z",
            "status": status,
            "modelVersion": "demo-model",
            "simulated": simulated,
            "inputHash": "model-input-hash",
            "items": [{"merchantId": "m-1", "wind": 35, "payout": 5000, "eligible": True, "reason": "triggered"}],
            "totalPayout": 5000,
            "affectedCount": 1,
        },
        "actions": [],
        "batches": [],
    }


class FakeProvider:
    configured = True
    provider = "fake"
    model = "fake-model"

    def __init__(self, *, failure: bool = False, malformed: bool = False, ready_partial: bool = False):
        self.failure = failure
        self.malformed = malformed
        self.ready_partial = ready_partial
        self.calls: list[tuple[str, dict]] = []

    async def complete(self, stage: str, payload: dict) -> dict:
        self.calls.append((stage, payload))
        if self.failure:
            raise RuntimeError("provider key should never be persisted")
        if self.malformed and stage == "intelligence":
            return {"summary": "bad", "findings": [{"text": "bad", "evidenceIds": ["missing"]}]}
        if stage == "intelligence":
            return {"summary": "Intelligence summary", "findings": [{"text": "Supported finding", "evidenceIds": ["model-run:status"]}]}
        if stage == "operations":
            return {
                "summary": "Operations summary",
                "findings": [{"text": "Task is grounded", "evidenceIds": ["model-run:total-payout"]}],
                "proposals": [
                    {
                        "title": "Review merchant",
                        "detail": "Prepare a review draft.",
                        "kind": "review",
                        "merchantIds": ["m-1"],
                        "evidenceIds": ["risk:m-1"],
                    }
                ],
            }
        return {
            "summary": "Review summary",
            "findings": [{"text": "Review finding", "evidenceIds": ["model-run:status"]}],
            "verdict": "ready" if self.ready_partial else "ready",
            "issues": [],
        }


class GateProvider(FakeProvider):
    async def complete(self, stage: str, payload: dict) -> dict:
        result = await super().complete(stage, payload)
        if stage == "review":
            await asyncio.sleep(0.2)
        return result


def wait_for_status(client: TestClient, run_id: str, status: str) -> dict:
    for _ in range(100):
        body = client.get("/api/agents/runs").json()
        match = next(item for item in body if item["id"] == run_id)
        if match["status"] == status:
            return match
        time.sleep(0.01)
    raise AssertionError(f"run did not reach {status}: {match}")


def create_body(request_id: str, *, model_status: str = "completed") -> dict:
    return {"requestId": request_id, "snapshot": snapshot(status=model_status)}


def test_live_three_stage_flow_and_proposal_decisions(tmp_path: Path) -> None:
    provider = FakeProvider()
    app = create_app(str(tmp_path / "agents.sqlite3"), provider)
    with TestClient(app) as client:
        created = client.post("/api/agents/runs", json=create_body("req-1"))
        assert created.status_code == 202
        queued = created.json()
        completed = wait_for_status(client, queued["id"], "completed")
        assert [step["status"] for step in completed["steps"]] == ["completed"] * 3
        assert [stage for stage, _ in provider.calls] == ["intelligence", "operations", "review"]
        assert completed["mode"] == "live"
        assert completed["inputHash"] == "model-input-hash"
        assert next(fact["value"] for fact in completed["facts"] if fact["id"] == "portfolio:sum-insured") == "PHP 100.00"
        assert next(fact["value"] for fact in completed["facts"] if fact["id"] == "model-run:total-payout") == "PHP 50.00"
        assert completed["proposals"][0]["status"] == "proposed"
        proposal_id = completed["proposals"][0]["id"]
        accept = client.post(
            f"/api/agents/runs/{queued['id']}/proposals/{proposal_id}",
            json={"requestId": "decision-1", "expectedVersion": completed["version"], "decision": "accept", "inputHash": "model-input-hash"},
        )
        assert accept.status_code == 200
        accepted = accept.json()
        assert accepted["proposals"][0]["status"] == "accepted"
        done = client.post(
            f"/api/agents/runs/{queued['id']}/proposals/{proposal_id}",
            json={"requestId": "decision-2", "expectedVersion": accepted["version"], "decision": "complete", "inputHash": "model-input-hash"},
        )
        assert done.status_code == 200
        assert done.json()["proposals"][0]["status"] == "done"


def test_provider_failure_is_persisted_without_leaking_exception(tmp_path: Path) -> None:
    app = create_app(str(tmp_path / "agents.sqlite3"), FakeProvider(failure=True))
    with TestClient(app) as client:
        created = client.post("/api/agents/runs", json=create_body("req-failure"))
        failed = wait_for_status(client, created.json()["id"], "failed")
        assert "provider key" not in (failed["error"] or "")
        assert "Live provider request failed" in (failed["error"] or "")
        assert failed["steps"][0]["status"] == "failed"


def test_malformed_citation_fails_run(tmp_path: Path) -> None:
    app = create_app(str(tmp_path / "agents.sqlite3"), FakeProvider(malformed=True))
    with TestClient(app) as client:
        created = client.post("/api/agents/runs", json=create_body("req-malformed"))
        failed = wait_for_status(client, created.json()["id"], "failed")
        assert "unknown evidence ID" in (failed["error"] or "")


def test_partial_input_cannot_produce_ready_review(tmp_path: Path) -> None:
    app = create_app(str(tmp_path / "agents.sqlite3"), FakeProvider(ready_partial=True))
    with TestClient(app) as client:
        created = client.post("/api/agents/runs", json=create_body("req-partial", model_status="partial"))
        completed = wait_for_status(client, created.json()["id"], "completed")
        assert completed["review"]["verdict"] == "needs_review"
        assert completed["warnings"]
        assert client.post(
            f"/api/agents/runs/{created.json()['id']}/proposals/{completed['proposals'][0]['id']}",
            json={"requestId": "partial-accept", "expectedVersion": completed["version"], "decision": "accept", "inputHash": "model-input-hash"},
        ).status_code == 400


def test_incomplete_completed_input_cannot_produce_ready_review(tmp_path: Path) -> None:
    provider = FakeProvider()
    app = create_app(str(tmp_path / "agents.sqlite3"), provider)
    body = create_body("req-incomplete")
    body["snapshot"]["modelRun"]["items"][0]["wind"] = None
    with TestClient(app) as client:
        created = client.post("/api/agents/runs", json=body)
        completed = wait_for_status(client, created.json()["id"], "completed")
        assert completed["review"]["verdict"] == "needs_review"
        assert any("missing wind" in warning for warning in completed["warnings"])


def test_failed_run_can_retry_as_a_new_persisted_attempt(tmp_path: Path) -> None:
    provider = FakeProvider(failure=True)
    app = create_app(str(tmp_path / "agents.sqlite3"), provider)
    with TestClient(app) as client:
        first = client.post("/api/agents/runs", json=create_body("req-retry"))
        failed = wait_for_status(client, first.json()["id"], "failed")
        provider.failure = False
        retried = client.post(
            f"/api/agents/runs/{failed['id']}/retry",
            json={"requestId": "retry-1", "expectedVersion": failed["version"]},
        )
        assert retried.status_code == 200
        assert retried.json()["id"] != failed["id"]
        assert retried.json()["status"] == "queued"
        assert wait_for_status(client, retried.json()["id"], "completed")["status"] == "completed"


def test_proposal_mutation_is_rejected_while_review_is_in_flight(tmp_path: Path) -> None:
    provider = GateProvider()
    app = create_app(str(tmp_path / "agents.sqlite3"), provider)
    with TestClient(app) as client:
        created = client.post("/api/agents/runs", json=create_body("req-in-flight"))
        run_id = created.json()["id"]
        proposal = None
        running = None
        for _ in range(100):
            running = next(item for item in client.get("/api/agents/runs").json() if item["id"] == run_id)
            if running["status"] == "running" and running["proposals"]:
                proposal = running["proposals"][0]
                break
            time.sleep(0.01)
        assert proposal is not None and running is not None
        decision = client.post(
            f"/api/agents/runs/{run_id}/proposals/{proposal['id']}",
            json={"requestId": "in-flight-decision", "expectedVersion": running["version"], "decision": "dismiss", "inputHash": "model-input-hash"},
        )
        assert decision.status_code == 400
        wait_for_status(client, run_id, "completed")


def test_restart_recovery_marks_inflight_job_failed(tmp_path: Path) -> None:
    from app.schemas import AgentSnapshot
    from app.service import AgentService
    from app.storage import AgentStorage

    db = str(tmp_path / "agents.sqlite3")
    storage = AgentStorage(db)
    service = AgentService(storage, FakeProvider())
    frozen = AgentSnapshot.model_validate(snapshot())
    normalized, snapshot_hash, facts, warnings = service._prepare_snapshot(frozen)
    record = service._new_record(
        run_id="agent-interrupted",
        snapshot=frozen,
        snapshot_hash=snapshot_hash,
        facts=facts,
        warnings=warnings,
        now="2026-10-12T00:00:00Z",
    )
    response = service._public_record(record)
    storage.create_run(
        request_id="interrupted-request",
        fingerprint="interrupted-fingerprint",
        operation="create",
        record=record,
        snapshot=normalized,
        snapshot_hash=snapshot_hash,
        response=response,
        at="2026-10-12T00:00:00Z",
    )
    record["status"] = "running"
    record["version"] = 2
    record["updatedAt"] = "2026-10-12T00:00:01Z"
    record["steps"][0]["status"] = "running"
    storage.save_run(record)
    assert storage.recover_unfinished(at="2026-10-12T00:00:02Z") == ["agent-interrupted"]
    recovered = storage.get_run("agent-interrupted")
    assert recovered["status"] == "failed"
    assert "Interrupted by service restart" in recovered["error"]


def test_idempotency_stale_version_and_restart_persistence(tmp_path: Path) -> None:
    db = str(tmp_path / "agents.sqlite3")
    provider = FakeProvider()
    app = create_app(db, provider)
    with TestClient(app) as client:
        first = client.post("/api/agents/runs", json=create_body("same-request"))
        replay = client.post("/api/agents/runs", json=create_body("same-request"))
        assert replay.status_code == 202
        assert replay.json()["id"] == first.json()["id"]
        mismatch = create_body("same-request")
        mismatch["snapshot"]["storm"]["name"] = "Other storm label"
        assert client.post("/api/agents/runs", json=mismatch).status_code == 409
        completed = wait_for_status(client, first.json()["id"], "completed")
        proposal_id = completed["proposals"][0]["id"]
        stale = client.post(
            f"/api/agents/runs/{first.json()['id']}/proposals/{proposal_id}",
            json={"requestId": "stale", "expectedVersion": 1, "decision": "dismiss", "inputHash": "model-input-hash"},
        )
        assert stale.status_code == 409
    app2 = create_app(db, FakeProvider())
    with TestClient(app2) as client2:
        persisted = client2.get("/api/agents/runs").json()
        assert any(item["id"] == first.json()["id"] for item in persisted)
