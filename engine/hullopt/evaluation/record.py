"""Deterministic candidate identity and explicit evaluation states."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def candidate_hash(identity: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvaluationStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    REJECTED = "rejected"
    FAILED = "failed"


ALLOWED_TRANSITIONS = {
    EvaluationStatus.PENDING: {EvaluationStatus.RUNNING, EvaluationStatus.REJECTED},
    EvaluationStatus.RUNNING: {EvaluationStatus.SUCCEEDED, EvaluationStatus.REJECTED, EvaluationStatus.FAILED},
    EvaluationStatus.SUCCEEDED: set(),
    EvaluationStatus.REJECTED: set(),
    EvaluationStatus.FAILED: set(),
}


@dataclass
class EvaluationRecord:
    study_id: str
    candidate_id: str
    candidate_hash: str
    identity: dict[str, Any]
    status: EvaluationStatus = EvaluationStatus.PENDING
    created_at_utc: str = field(default_factory=utc_now)
    updated_at_utc: str = field(default_factory=utc_now)
    reason: str | None = None

    def __post_init__(self) -> None:
        self.status = EvaluationStatus(self.status)
        if candidate_hash(self.identity) != self.candidate_hash:
            raise ValueError("candidate_hash does not match identity")

    def transition(self, status: EvaluationStatus | str, *, reason: str | None = None) -> None:
        next_status = EvaluationStatus(status)
        if next_status not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"Invalid evaluation transition: {self.status.value} -> {next_status.value}")
        if next_status in {EvaluationStatus.REJECTED, EvaluationStatus.FAILED} and not reason:
            raise ValueError("Rejected or failed evaluations require a reason")
        self.status = next_status
        self.reason = reason
        self.updated_at_utc = utc_now()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        return result
