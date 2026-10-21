# CLIMADA

CLIMADA is a local operations prototype for merchant typhoon cover. It combines a browser workbench for portfolio analysis, simulated actions and simulated payouts with a separate FastAPI service for evidence-backed LLM agent drafts.

Start with the [project overview and ownership](docs/README.md). The short version is:

- The business workspace remains a local simulation. MSW serves the workspace API and IndexedDB stores the browser state. Its default is `VITE_DATA_MODE=mock`.
- All business and numerical model records used by this prototype remain simulated; the live part is the configured LLM provider call over a synthetic snapshot.
- The agent service runs at `http://localhost:8000` (`127.0.0.1:8000`). Vite proxies `/api/agents` to it during development.
- Agent runs use three ordered stages: **intelligence → operations → review**. The service stores the frozen snapshot, facts, stage results and draft tasks in SQLite.
- Agents provide advisory text only. People accept, dismiss or complete proposals; agents do not approve or pay batches and do not send notifications.

## Quick start

Start the agent API from `backend` with one worker:

```sh
cd backend
python -m pip install -r requirements.txt
# Create backend/.env from .env.example and add the server-only key locally.
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The local provider is configured for `gpt-4.1-mini` through `backend/.env`. That file is ignored by Git; never copy its values into frontend variables or documentation.

In a second terminal, start the browser:

```sh
cd frontend
npm ci
npm run dev
```

Open the Vite address, load the demo dataset, run a compatible risk analysis, then open **Agents**. The browser snapshot is an input to the local agent service and is not production authority. The service is local-only and has no authentication.

## Verified checks

The current verified results are 13 Vitest tests, 15 backend pytest cases, eight standard Playwright tests, one opt-in live agent Playwright test, and a deterministic A → B → C → D model contract. The canonical model test run reports 34 passed and two real-CLIMADA tests skipped because CLIMADA is not installed locally. The live agent check used the configured OpenAI `gpt-4.1-mini` provider with synthetic fixtures only; it consumes provider calls. See [frontend/README.md](frontend/README.md), [backend/README.md](backend/README.md), [modeling/README.md](modeling/README.md) and [the agent workflow](docs/AGENT_WORKFLOW.en.md) for commands and boundaries.

Further references:

- [Project overview, architecture and ownership](docs/README.md)
- [Agent workflow and API contract](docs/AGENT_WORKFLOW.en.md)
- [Frontend guide](frontend/README.md)
- [Backend guide](backend/README.md)
- [Architecture and processing sequences](docs/ARCHITECTURE.en.md)
- [Model handoff specification](docs/MODEL_HANDOFF.en.md)
- [Integrated numerical model package](modeling/README.md)
- [Business rules register](docs/BUSINESS_RULES.en.md)
