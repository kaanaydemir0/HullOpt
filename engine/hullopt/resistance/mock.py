"""Deterministic software exercise. The values have no hydrodynamic meaning."""

from __future__ import annotations

from typing import Any

from .base import Conditions, RawResult, measurement_value


class MockBackend:
    name = "MOCK"

    def evaluate(self, candidate_measurements: Any, conditions: Conditions) -> RawResult:
        volume = measurement_value(candidate_measurements, "volume_m3")
        return RawResult(
            backend=self.name, label="MOCK — SOFTWARE TEST ONLY; NOT A RESISTANCE PREDICTION",
            quantity="software_test_score", is_physical_prediction=False,
            speeds_kn=conditions.speeds_kn,
            values=tuple(volume * (1.0 + speed / 100.0) for speed in conditions.speeds_kn),
            units="arbitrary", warnings=("Synthetic values; never use for engineering decisions or optimization rankings.",),
            metadata={"purpose": "M2 end-to-end storage and reporting verification"},
        )
