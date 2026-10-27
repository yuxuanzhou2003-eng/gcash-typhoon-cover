"""FastAPI application for the CLIMADA agent workflow."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .provider import LiveProvider
from .schemas import ProposalDecisionRequest, RetryRequest, RunCreateRequest
from .service import AgentService, AgentServiceError
from .storage import AgentStorage


def _error_status(message: str) -> int:
    lowered = message.lower()
    if "not found" in lowered:
        return 404
    if any(
        phrase in lowered
        for phrase in (
            "changed",
            "stale",
            "already been used",
            "fingerprint",
            "only a failed",
            "only an open",
            "only an accepted",
            "only a proposed",
        )
    ):
        return 409
    return 400


def create_app(db_path: str | None = None, provider: Any | None = None) -> FastAPI:
    storage = AgentStorage(db_path)
    service = AgentService(storage, provider if provider is not None else LiveProvider())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            storage.close()

    app = FastAPI(title="CLIMADA Agent API", version="0.1.0", lifespan=lifespan)
    app.state.agent_service = service
    app.state.agent_storage = storage
    allowed_origins = ["http://localhost:5173", "http://127.0.0.1:5173"]
    extra_origins = os.environ.get("AGENT_ALLOWED_ORIGINS", "")
    allowed_origins += [origin.strip() for origin in extra_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Api-Key"],
    )

    # When AGENT_API_KEY is set (production), every /api/agents/* call except the
    # health probe must present it. Unset locally so dev workflows stay frictionless.
    api_key = os.environ.get("AGENT_API_KEY")

    @app.middleware("http")
    async def require_api_key(request: Request, call_next):
        if (
            api_key
            and request.url.path.startswith("/api/agents/")
            and request.url.path != "/api/agents/health"
            and request.method != "OPTIONS"
            and request.headers.get("x-api-key") != api_key
        ):
            return JSONResponse(status_code=401, content={"message": "Missing or invalid X-Api-Key."})
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Keep the public error shape stable and avoid returning raw request
        # values in validation details.
        location = exc.errors()[0].get("loc", ()) if exc.errors() else ()
        field = str(location[-1]) if location else "request"
        return JSONResponse(status_code=400, content={"message": f"Invalid {field}."})

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(status_code=exc.status_code, content={"message": detail})

    @app.exception_handler(AgentServiceError)
    async def service_error(_: Request, exc: AgentServiceError) -> JSONResponse:
        message = str(exc) or "Agent request failed."
        return JSONResponse(status_code=_error_status(message), content={"message": message})

    @app.get("/api/agents/health")
    async def agent_health() -> dict[str, Any]:
        return service.health.model_dump(mode="json")

    @app.get("/api/agents/runs")
    async def agent_runs(
        portfolioId: str | None = Query(default=None, max_length=128),
        stormId: str | None = Query(default=None, max_length=128),
    ) -> list[dict[str, Any]]:
        return service.get_runs(portfolioId, stormId)

    @app.post("/api/agents/runs", status_code=202)
    async def create_agent_run(request: RunCreateRequest) -> dict[str, Any]:
        try:
            return await service.submit(request)
        except (AgentServiceError, ValueError) as exc:
            if isinstance(exc, AgentServiceError):
                raise
            raise AgentServiceError(str(exc)) from exc

    @app.post("/api/agents/runs/{run_id}/retry")
    async def retry_agent_run(run_id: str, request: RetryRequest) -> dict[str, Any]:
        try:
            return await service.retry(run_id, request)
        except AgentServiceError:
            raise

    @app.post("/api/agents/runs/{run_id}/proposals/{proposal_id}")
    async def decide_agent_proposal(
        run_id: str,
        proposal_id: str,
        request: ProposalDecisionRequest,
    ) -> dict[str, Any]:
        try:
            return await service.decide_proposal(run_id, proposal_id, request)
        except AgentServiceError:
            raise

    return app


app = create_app()
