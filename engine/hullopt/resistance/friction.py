"""ITTC-1957 correlation line, smooth-surface friction component only."""

from __future__ import annotations

import math
from typing import Any

from .base import Conditions, RawResult, measurement_value

KNOT_TO_M_S = 1852.0 / 3600.0


class ITTC57Friction:
    name = "PY-ITTC57-FRICTION-ONLY"

    def __init__(self, *, include_transom: bool = True):
        self.include_transom = include_transom

    def evaluate(self, candidate_measurements: Any, conditions: Conditions) -> RawResult:
        length = measurement_value(candidate_measurements, "lwl_m")
        area = measurement_value(candidate_measurements, "wetted_area_m2")
        if not self.include_transom:
            transom = (candidate_measurements["transom_wetted_area_m2"]
                       if isinstance(candidate_measurements, dict)
                       else candidate_measurements.transom_wetted_area_m2)
            if not math.isfinite(transom) or transom < 0:
                raise ValueError("transom_wetted_area_m2 must be finite and nonnegative")
            area -= transom
        if area <= 0:
            raise ValueError("Friction reference area must be positive")
        reynolds, coefficients, forces = [], [], []
        for speed in conditions.speeds_kn:
            velocity = speed * KNOT_TO_M_S
            rn = velocity * length / conditions.nu_m2_s
            if rn <= 1e5:
                raise ValueError("ITTC57 turbulent friction requires Reynolds number above 1e5")
            cf = 0.075 / (math.log10(rn) - 2.0) ** 2
            reynolds.append(rn)
            coefficients.append(cf)
            forces.append(0.5 * conditions.rho_kg_m3 * velocity**2 * area * cf)
        return RawResult(
            backend=self.name, label="ITTC-57 smooth-surface friction component only",
            quantity="friction_force", is_physical_prediction=True,
            speeds_kn=conditions.speeds_kn, values=tuple(forces), units="N",
            warnings=("Friction only: excludes wave, form, air, appendage and correlation-allowance contributions; not total resistance.",),
            metadata={"length_m": length, "friction_area_m2": area,
                      "include_transom": self.include_transom, "reynolds_number": reynolds,
                      "friction_coefficient": coefficients, "knot_to_m_s": KNOT_TO_M_S},
        )
