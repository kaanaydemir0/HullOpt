"""A single-writer SQLite store with guarded evaluation state transitions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..evaluation.record import ALLOWED_TRANSITIONS, EvaluationRecord, EvaluationStatus, canonical_json, utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS study (
    study_id TEXT PRIMARY KEY,
    created_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidate (
    id INTEGER PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES study(study_id),
    candidate_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    identity_json TEXT NOT NULL,
    UNIQUE(study_id, candidate_id)
);
CREATE TABLE IF NOT EXISTS evaluation (
    id INTEGER PRIMARY KEY,
    candidate_key INTEGER NOT NULL REFERENCES candidate(id),
    status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','rejected','failed')),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS result (
    id INTEGER PRIMARY KEY,
    evaluation_id INTEGER NOT NULL REFERENCES evaluation(id),
    backend TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(evaluation_id, backend)
);
"""


class ResultStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)

    def __enter__(self) -> "ResultStore":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def create_evaluation(self, record: EvaluationRecord) -> int:
        if record.status != EvaluationStatus.PENDING:
            raise ValueError("New evaluations must be pending")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO study VALUES (?, ?)",
                                    (record.study_id, record.created_at_utc))
            self.connection.execute(
                "INSERT INTO candidate(study_id,candidate_id,candidate_hash,identity_json) VALUES (?,?,?,?)",
                (record.study_id, record.candidate_id, record.candidate_hash, canonical_json(record.identity)),
            )
            key = self.connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            cursor = self.connection.execute(
                "INSERT INTO evaluation(candidate_key,status,created_at_utc,updated_at_utc) VALUES (?,?,?,?)",
                (key, record.status.value, record.created_at_utc, record.updated_at_utc),
            )
            return int(cursor.lastrowid)

    def transition(self, evaluation_id: int, status: EvaluationStatus | str, *, reason: str | None = None,
                   results: dict[str, dict[str, Any]] | None = None) -> None:
        """Store result payloads and the final status in the same transaction."""
        status = EvaluationStatus(status)
        if status in {EvaluationStatus.REJECTED, EvaluationStatus.FAILED} and not reason:
            raise ValueError("Rejected or failed evaluations require a reason")
        if results and status != EvaluationStatus.SUCCEEDED:
            raise ValueError("Results may be committed only on successful completion")
        if results and any(payload.get("backend", backend) != backend for backend, payload in results.items()):
            raise ValueError("Result backend does not match its storage key")
        with self.connection:
            # Explicit write reservation prevents two writers observing the same old state.
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("SELECT status FROM evaluation WHERE id=?", (evaluation_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown evaluation {evaluation_id}")
            old = EvaluationStatus(row["status"])
            if status not in ALLOWED_TRANSITIONS[old]:
                raise ValueError(f"Invalid evaluation transition: {old.value} -> {status.value}")
            for backend, payload in (results or {}).items():
                self.connection.execute("INSERT INTO result(evaluation_id,backend,payload_json) VALUES (?,?,?)",
                                        (evaluation_id, backend, canonical_json(payload)))
            self.connection.execute("UPDATE evaluation SET status=?,updated_at_utc=?,reason=? WHERE id=?",
                                    (status.value, utc_now(), reason, evaluation_id))

    def find_candidate(self, study_id: str, candidate_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT c.*,e.id AS evaluation_id,e.status,e.reason FROM candidate c JOIN evaluation e "
            "ON e.candidate_key=c.id WHERE c.study_id=? AND c.candidate_id=? ORDER BY e.id DESC LIMIT 1",
            (study_id, candidate_id),
        ).fetchone()
        return dict(row) if row else None

    def get_results(self, evaluation_id: int) -> dict[str, Any]:
        return {row["backend"]: json.loads(row["payload_json"]) for row in self.connection.execute(
            "SELECT backend,payload_json FROM result WHERE evaluation_id=?", (evaluation_id,))}
