# CLIMADA agent backend

This directory contains the local FastAPI service for CLIMADA's three-stage LLM workflow. It is an agent service, not the business engine: the browser still owns a simulated MSW/IndexedDB workspace for policies, model runs, actions and payouts. All business and numerical model records in this prototype are simulated; the LLM provider call may be live.

## Run locally

From this directory:

```sh
python -m pip install -r requirements.txt
# Copy .env.example to .env and set the server-only provider key locally.
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run one Uvicorn worker. The service uses one in-process async queue; multiple workers would create separate queues and are outside this local prototype.

The default provider is OpenAI with model `gpt-4.1-mini` (`LLM_PROVIDER=openai`, `OPENAI_SUMMARY_MODEL=gpt-4.1-mini`). Credentials are read only by the server from `backend/.env` or the process environment. `.env` is ignored by Git and must never be documented, returned in a response or sent by the browser. Without a key, `/api/agents/health` reports an unconfigured live provider and submitted runs fail with a safe retry message.

The default database is `backend/data/agents.sqlite3`. Set `AGENT_TIMEOUT_SECONDS` to change the provider timeout. The app factory accepts an explicit database path for tests and embedding; the normal local server uses the default path.

## API surface

The FastAPI app exposes JSON under `/api/agents`:

| Route | Behavior |
|---|---|
| `GET /api/agents/health` | Returns `configured`, provider, model, mode and a safe status message |
| `GET /api/agents/runs` | Lists persisted runs; optional `portfolioId` and `stormId` filters |
| `POST /api/agents/runs` | Validates and freezes an `AgentSnapshot`, queues a run and returns `202` |
| `POST /api/agents/runs/{runId}/retry` | Creates a traceable retry for a failed or interrupted run |
| `POST /api/agents/runs/{runId}/proposals/{proposalId}` | Applies a human `accept`, `dismiss` or `complete` decision |

Run creation requires `{ requestId, snapshot }`. Retry requires `{ requestId, expectedVersion }`. Proposal decisions require `{ requestId, expectedVersion, decision, inputHash }`. Errors use `{ "message": "..." }`; stale versions, reused request IDs with different payloads and a fingerprint that does not match the stored frozen run return a conflict.

## Three-stage workflow

1. **Intelligence** reads the selected bulletin and validated risk snapshot, then returns concise findings with fact citations.
2. **Operations** uses the earlier output to draft up to three evidence-backed proposals. Communication is only a draft; funding and settlement proposals mean review readiness, never transfer or approval. Distinct runs are independent and there is no semantic duplicate-task guarantee across runs.
3. **Review** checks citations, simulation labels, stage consistency and input completeness. A partial model run cannot receive a `ready` verdict; it is downgraded to `needs_review`.

The provider receives a structured JSON schema for each stage. The service validates the response with Pydantic, rejects unknown fact or merchant IDs, and stores only sanitized provider errors. The server-generated fact catalog and the submitted numerical model result are the numerical boundary for a run; in this prototype those model values are simulated. Agent prose cannot create amounts, eligibility, dates, probabilities, thresholds or payments.

## Persistence and recovery

SQLite stores the run record, its immutable frozen snapshot, job state, idempotency request keys and stage transitions. Stage status is persisted as `waiting`, `running`, `completed` or `failed`; proposals persist as `proposed`, `accepted`, `dismissed` or `done`.

When the service starts, any queued or running run left by a previous process is marked `failed` with `Interrupted by service restart; retry this run.` The original snapshot remains available, but the operator must call the retry route. Retry creates a new run linked to its parent and checks the parent version atomically.

Every mutating request uses a `requestId` fingerprint. Replaying the same request returns the saved response; reusing the ID with a different payload is rejected. Mutations also use optimistic `expectedVersion` checks. Decisions verify the supplied model `inputHash` against the stored frozen run. The browser UI separately checks whether the selected snapshot is current before enabling a decision; that client check is not production verification.

## Snapshot validation

The browser submits one snapshot containing the portfolio, storm, bulletin, rule, model run, actions and payout batches. Before a provider call, the service checks:

- entity IDs and versions agree across the snapshot;
- the model run is `completed` or `partial`, and bulletin/model timestamps are ordered;
- merchant coordinates and coverage dates are valid and finite;
- risk item IDs, payout totals and affected counts are consistent;
- action and batch references point to the selected portfolio, storm and model run;
- duplicate IDs and unknown merchants are rejected.

The service then builds facts from the snapshot. Missing risk items and simulated model provenance become visible warnings. Missing wind remains missing; it is never treated as zero risk.

## Safety and ownership boundary

This local service has CORS for the local Vite origins and no authentication. The browser-provided snapshot is an integration input, not production authority. Agents are advisory and cannot modify policies, rules, ledgers, approvals or payments; they also cannot send notifications. A human remains responsible for accepting, dismissing or completing a draft and for any later business workflow.

Yuxuan owns the UI, orchestration, backend and this integration boundary. Teammates own the external numerical model, its data and validation, using the existing model handoff contracts. The agent service consumes the resulting snapshot; it does not implement or replace the numerical model.

## Tests

From `backend`, run in PowerShell:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest tests -q
```

The verified backend result is 15 pytest cases. They cover the ordered three-stage service flow, safe provider failures, citation validation, partial-input review behavior, idempotency, version conflicts, persistence across service instances and structured provider responses.
