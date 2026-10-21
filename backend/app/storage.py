"""Small SQLite persistence layer for frozen agent jobs.

The service deliberately keeps database work in short synchronous sections.
The worker never holds one of these transactions while awaiting a provider
request, so a slow or unavailable model cannot block API writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class StorageError(RuntimeError):
    """Base class for persistence and optimistic-concurrency failures."""


class IdempotencyConflict(StorageError):
    pass


class VersionConflict(StorageError):
    pass


class RecordNotFound(StorageError):
    pass


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class AgentStorage:
    """SQLite store for agent runs, immutable snapshots, and request keys."""

    _RUN_COLUMNS = (
        "id",
        "version",
        "portfolio_id",
        "portfolio_version",
        "storm_id",
        "model_run_id",
        "bulletin_id",
        "rule_id",
        "input_hash",
        "snapshot_hash",
        "created_at",
        "updated_at",
        "status",
        "mode",
        "model",
        "steps_json",
        "facts_json",
        "proposals_json",
        "review_json",
        "error",
        "warnings_json",
        "outputs_json",
        "parent_run_id",
    )

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or (Path(__file__).resolve().parents[1] / "data" / "agents.sqlite3"))
        self._lock = threading.RLock()
        self._memory = self.db_path == ":memory:"
        self._memory_uri = "file:climada_agents_memory?mode=memory&cache=shared"
        self._keeper: sqlite3.Connection | None = None
        if self._memory:
            self._keeper = self._new_connection(self._memory_uri, uri=True)
        else:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _new_connection(self, target: str, *, uri: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(target, timeout=15, isolation_level=None, check_same_thread=False, uri=uri)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if not self._memory:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._memory:
            assert self._keeper is not None
            yield self._keeper
            return
        conn = self._new_connection(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    portfolio_id TEXT NOT NULL,
                    portfolio_version INTEGER NOT NULL,
                    storm_id TEXT NOT NULL,
                    model_run_id TEXT NOT NULL,
                    bulletin_id TEXT NOT NULL,
                    rule_id TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    model TEXT NOT NULL,
                    steps_json TEXT NOT NULL,
                    facts_json TEXT NOT NULL,
                    proposals_json TEXT NOT NULL,
                    review_json TEXT,
                    error TEXT,
                    warnings_json TEXT NOT NULL,
                    outputs_json TEXT NOT NULL,
                    parent_run_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_agent_runs_portfolio_storm
                    ON agent_runs (portfolio_id, storm_id, created_at);
                CREATE TABLE IF NOT EXISTS agent_snapshots (
                    run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
                    snapshot_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_jobs (
                    run_id TEXT PRIMARY KEY REFERENCES agent_runs(id) ON DELETE CASCADE,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    enqueued_at TEXT NOT NULL,
                    last_started_at TEXT
                );
                CREATE TABLE IF NOT EXISTS agent_request_keys (
                    request_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agent_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
                    stage TEXT,
                    status TEXT NOT NULL,
                    detail TEXT,
                    at TEXT NOT NULL
                );
                """
            )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "version": row["version"],
            "portfolioId": row["portfolio_id"],
            "portfolioVersion": row["portfolio_version"],
            "stormId": row["storm_id"],
            "modelRunId": row["model_run_id"],
            "bulletinId": row["bulletin_id"],
            "ruleId": row["rule_id"],
            "inputHash": row["input_hash"],
            "snapshotHash": row["snapshot_hash"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "status": row["status"],
            "mode": row["mode"],
            "model": row["model"],
            "steps": _loads(row["steps_json"], []),
            "facts": _loads(row["facts_json"], []),
            "proposals": _loads(row["proposals_json"], []),
            "review": _loads(row["review_json"], None),
            "error": row["error"],
            "warnings": _loads(row["warnings_json"], []),
            "outputs": _loads(row["outputs_json"], {}),
            "parentRunId": row["parent_run_id"],
        }

    @staticmethod
    def _db_values(record: dict[str, Any]) -> tuple[Any, ...]:
        return (
            record["id"],
            record["version"],
            record["portfolioId"],
            record["portfolioVersion"],
            record["stormId"],
            record["modelRunId"],
            record["bulletinId"],
            record["ruleId"],
            record["inputHash"],
            record["snapshotHash"],
            record["createdAt"],
            record["updatedAt"],
            record["status"],
            record["mode"],
            record["model"],
            _json(record.get("steps", [])),
            _json(record.get("facts", [])),
            _json(record.get("proposals", [])),
            _json(record["review"]) if record.get("review") is not None else None,
            record.get("error"),
            _json(record.get("warnings", [])),
            _json(record.get("outputs", {})),
            record.get("parentRunId"),
        )

    @staticmethod
    def _select_one(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise RecordNotFound("Agent run not found.")
        return row

    @staticmethod
    def _check_request(conn: sqlite3.Connection, request_id: str, fingerprint: str) -> dict[str, Any] | None:
        row = conn.execute(
            "SELECT fingerprint, response_json FROM agent_request_keys WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        if row["fingerprint"] != fingerprint:
            raise IdempotencyConflict("requestId has already been used with a different payload.")
        return _loads(row["response_json"], {})

    def create_run(
        self,
        *,
        request_id: str,
        fingerprint: str,
        operation: str,
        record: dict[str, Any],
        snapshot: dict[str, Any],
        snapshot_hash: str,
        response: dict[str, Any],
        at: str,
    ) -> tuple[dict[str, Any], bool]:
        """Insert a run and its immutable snapshot, or replay its idempotent response."""

        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous = self._check_request(conn, request_id, fingerprint)
                if previous is not None:
                    conn.execute("COMMIT")
                    return previous, True
                conn.execute(
                    """INSERT INTO agent_runs (
                        id, version, portfolio_id, portfolio_version, storm_id,
                        model_run_id, bulletin_id, rule_id, input_hash,
                        snapshot_hash, created_at, updated_at, status, mode,
                        model, steps_json, facts_json, proposals_json, review_json,
                        error, warnings_json, outputs_json, parent_run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._db_values(record),
                )
                conn.execute(
                    "INSERT INTO agent_snapshots(run_id, snapshot_hash, snapshot_json) VALUES (?, ?, ?)",
                    (record["id"], snapshot_hash, _json(snapshot)),
                )
                conn.execute(
                    "INSERT INTO agent_jobs(run_id, state, attempts, enqueued_at) VALUES (?, ?, ?, ?)",
                    (record["id"], record["status"], 0, at),
                )
                conn.execute(
                    """INSERT INTO agent_request_keys(request_id, fingerprint, operation, run_id, response_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (request_id, fingerprint, operation, record["id"], _json(response), at),
                )
                conn.execute(
                    "INSERT INTO agent_transitions(run_id, stage, status, detail, at) VALUES (?, ?, ?, ?, ?)",
                    (record["id"], None, "queued", "Agent run queued.", at),
                )
                conn.execute("COMMIT")
                return response, False
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def retry_run(
        self,
        *,
        request_id: str,
        fingerprint: str,
        operation: str,
        parent_run_id: str,
        expected_version: int,
        record: dict[str, Any],
        snapshot: dict[str, Any],
        snapshot_hash: str,
        response: dict[str, Any],
        at: str,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically verify a failed run and create a traceable retry attempt."""

        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous = self._check_request(conn, request_id, fingerprint)
                if previous is not None:
                    conn.execute("COMMIT")
                    return previous, True
                parent = self._select_one(conn, parent_run_id)
                if int(parent["version"]) != expected_version:
                    raise VersionConflict("This agent run changed. Refresh and retry.")
                if parent["status"] not in {"failed", "cancelled"}:
                    raise StorageError("Only a failed agent run can be retried.")
                conn.execute(
                    """INSERT INTO agent_runs (
                        id, version, portfolio_id, portfolio_version, storm_id,
                        model_run_id, bulletin_id, rule_id, input_hash,
                        snapshot_hash, created_at, updated_at, status, mode,
                        model, steps_json, facts_json, proposals_json, review_json,
                        error, warnings_json, outputs_json, parent_run_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    self._db_values(record),
                )
                conn.execute(
                    "INSERT INTO agent_snapshots(run_id, snapshot_hash, snapshot_json) VALUES (?, ?, ?)",
                    (record["id"], snapshot_hash, _json(snapshot)),
                )
                conn.execute(
                    "INSERT INTO agent_jobs(run_id, state, attempts, enqueued_at) VALUES (?, ?, ?, ?)",
                    (record["id"], record["status"], 0, at),
                )
                conn.execute(
                    """INSERT INTO agent_request_keys(request_id, fingerprint, operation, run_id, response_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (request_id, fingerprint, operation, record["id"], _json(response), at),
                )
                conn.execute(
                    "INSERT INTO agent_transitions(run_id, stage, status, detail, at) VALUES (?, ?, ?, ?, ?)",
                    (record["id"], None, "queued", "Retry attempt queued.", at),
                )
                conn.execute("COMMIT")
                return response, False
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as conn:
            return self._record_from_row(self._select_one(conn, run_id))

    def get_snapshot(self, run_id: str) -> dict[str, Any]:
        with self._lock, self._connection() as conn:
            self._select_one(conn, run_id)
            row = conn.execute("SELECT snapshot_json FROM agent_snapshots WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise RecordNotFound("Agent snapshot not found.")
            return _loads(row["snapshot_json"], {})

    def list_runs(self, portfolio_id: str | None = None, storm_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock, self._connection() as conn:
            clauses: list[str] = []
            params: list[Any] = []
            if portfolio_id:
                clauses.append("portfolio_id = ?")
                params.append(portfolio_id)
            if storm_id:
                clauses.append("storm_id = ?")
                params.append(storm_id)
            where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM agent_runs{where} ORDER BY created_at DESC, id DESC",
                params,
            ).fetchall()
            return [self._record_from_row(row) for row in rows]

    def save_run(self, record: dict[str, Any], *, stage: str | None = None, detail: str | None = None) -> None:
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._select_one(conn, record["id"])
                conn.execute(
                    """UPDATE agent_runs SET
                        version=?, portfolio_id=?, portfolio_version=?, storm_id=?,
                        model_run_id=?, bulletin_id=?, rule_id=?, input_hash=?,
                        snapshot_hash=?, created_at=?, updated_at=?, status=?, mode=?,
                        model=?, steps_json=?, facts_json=?, proposals_json=?,
                        review_json=?, error=?, warnings_json=?, outputs_json=?, parent_run_id=?
                       WHERE id=?""",
                    self._db_values(record)[1:] + (record["id"],),
                )
                conn.execute(
                    "UPDATE agent_jobs SET state=?, last_started_at=CASE WHEN ?='running' THEN ? ELSE last_started_at END WHERE run_id=?",
                    (record["status"], record["status"], record["updatedAt"], record["id"]),
                )
                if stage is not None or detail is not None:
                    conn.execute(
                        "INSERT INTO agent_transitions(run_id, stage, status, detail, at) VALUES (?, ?, ?, ?, ?)",
                        (record["id"], stage, record["status"], detail, record["updatedAt"]),
                    )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def mutate_run(
        self,
        *,
        request_id: str,
        fingerprint: str,
        operation: str,
        run_id: str,
        expected_version: int,
        mutator: Callable[[dict[str, Any]], dict[str, Any]],
        response_factory: Callable[[dict[str, Any]], dict[str, Any]],
        at: str,
    ) -> tuple[dict[str, Any], bool]:
        """Apply a local state mutation and save its idempotent response atomically."""

        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                previous = self._check_request(conn, request_id, fingerprint)
                if previous is not None:
                    conn.execute("COMMIT")
                    return previous, True
                row = self._select_one(conn, run_id)
                record = self._record_from_row(row)
                if int(record["version"]) != expected_version:
                    raise VersionConflict("This agent run changed. Refresh and retry.")
                updated = mutator(record)
                response = response_factory(updated)
                conn.execute(
                    """UPDATE agent_runs SET
                        version=?, portfolio_id=?, portfolio_version=?, storm_id=?,
                        model_run_id=?, bulletin_id=?, rule_id=?, input_hash=?,
                        snapshot_hash=?, created_at=?, updated_at=?, status=?, mode=?,
                        model=?, steps_json=?, facts_json=?, proposals_json=?,
                        review_json=?, error=?, warnings_json=?, outputs_json=?, parent_run_id=?
                       WHERE id=?""",
                    self._db_values(updated)[1:] + (updated["id"],),
                )
                conn.execute(
                    "UPDATE agent_jobs SET state=? WHERE run_id=?",
                    (updated["status"], updated["id"]),
                )
                conn.execute(
                    "INSERT INTO agent_transitions(run_id, stage, status, detail, at) VALUES (?, ?, ?, ?, ?)",
                    (updated["id"], "proposals", "completed", operation, at),
                )
                conn.execute(
                    """INSERT INTO agent_request_keys(request_id, fingerprint, operation, run_id, response_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (request_id, fingerprint, operation, updated["id"], _json(response), at),
                )
                conn.execute("COMMIT")
                return response, False
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def recover_unfinished(self, *, at: str) -> list[str]:
        """Mark pre-existing in-flight work failed after a process restart."""

        recovered: list[str] = []
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT * FROM agent_runs WHERE status IN ('queued', 'running')"
                ).fetchall()
                for row in rows:
                    record = self._record_from_row(row)
                    for step in record["steps"]:
                        if step.get("status") == "running":
                            step["status"] = "failed"
                            step["error"] = "Interrupted by service restart; retry this run."
                            step["completedAt"] = at
                    record["version"] = int(record["version"]) + 1
                    record["updatedAt"] = at
                    record["status"] = "failed"
                    record["error"] = "Interrupted by service restart; retry this run."
                    self._select_one(conn, record["id"])
                    conn.execute(
                        """UPDATE agent_runs SET version=?, updated_at=?, status=?, steps_json=?, error=? WHERE id=?""",
                        (
                            record["version"],
                            record["updatedAt"],
                            record["status"],
                            _json(record["steps"]),
                            record["error"],
                            record["id"],
                        ),
                    )
                    conn.execute("UPDATE agent_jobs SET state=? WHERE run_id=?", ("failed", record["id"]))
                    conn.execute(
                        "INSERT INTO agent_transitions(run_id, stage, status, detail, at) VALUES (?, ?, ?, ?, ?)",
                        (record["id"], None, "failed", record["error"], at),
                    )
                    recovered.append(record["id"])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        return recovered

    def close(self) -> None:
        with self._lock:
            if self._keeper is not None:
                self._keeper.close()
                self._keeper = None
