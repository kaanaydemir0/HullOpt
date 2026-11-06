"""Common backend interface; a component is never presented as total resistance."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Conditions:
    speeds_kn: tuple[float, ...]
    rho_kg_m3: float = 1025.0
    nu_m2_s: float = 1.18831e-6

    def __post_init__(self) -> None:
        if not self.speeds_kn or any(not math.isfinite(v) or v <= 0 for v in self.speeds_kn):
            raise ValueError("Speeds must be positive and finite")
        if not all(math.isfinite(v) and v > 0 for v in (self.rho_kg_m3, self.nu_m2_s)):
            raise ValueError("Density and kinematic viscosity must be positive and finite")


@dataclass(frozen=True)
class RawResult:
    backend: str
    label: str
    quantity: str
    is_physical_prediction: bool
    speeds_kn: tuple[float, ...]
    values: tuple[float, ...]
    units: str
    warnings: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.backend or not self.label or not self.quantity:
            raise ValueError("Backend, label, and quantity are mandatory")
        if len(self.speeds_kn) != len(self.values):
            raise ValueError("One result value is required per speed")
        if any(not math.isfinite(value) for value in self.values):
            raise ValueError("Result values must be finite")
        if self.backend == "MOCK" and self.is_physical_prediction:
            raise ValueError("MOCK results cannot be physical predictions")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResistanceBackend(Protocol):
    name: str

    def evaluate(self, candidate_measurements: Any, conditions: Conditions) -> RawResult: ...


def measurement_value(measurements: Any, name: str) -> float:
    value = measurements[name] if isinstance(measurements, dict) else getattr(measurements, name)
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value
