"""Safeguarded zero-trim displacement equilibrium within an explicit WL bracket."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .hydrostatics import Hydrostatics, measure


class EquilibriumError(ValueError):
    """The requested displacement cannot be attained within the permitted draft."""


@dataclass
class EquilibriumResult:
    z_wl_m: float
    hydrostatics: Hydrostatics
    iterations: int
    target_volume_m3: float
    relative_error: float

    def to_dict(self):
        return dict(z_wl_m=self.z_wl_m, iterations=self.iterations,
                    target_volume_m3=self.target_volume_m3, relative_error=self.relative_error,
                    hydrostatics=self.hydrostatics.to_dict())


def solve_equilibrium(mesh, target_volume_m3: float, reference_z_wl_m: float, *,
                      draft_change_limit_m: float = 0.05, relative_tolerance: float = 1e-6,
                      rho_kg_m3: float = 1025.0, max_iterations: int = 30,
                      section_count: int = 161) -> EquilibriumResult:
    """Newton update dV/dz=Awp with bisection whenever Newton leaves the bracket.

    reference_z_wl_m is the unchanged baseline waterline; limits must never be
    centred on a previous candidate, or repeated steps could evade D-4.
    """
    if not np.isfinite(target_volume_m3) or target_volume_m3 <= 0:
        raise EquilibriumError("Target volume must be finite and positive (m³).")
    if not np.isfinite(reference_z_wl_m):
        raise EquilibriumError("Reference waterline must be finite (m).")
    if not np.isfinite(draft_change_limit_m) or draft_change_limit_m <= 0:
        raise EquilibriumError("Draft change limit must be finite and positive (m).")
    if not np.isfinite(relative_tolerance) or not 0 < relative_tolerance < 1:
        raise EquilibriumError("Relative volume tolerance must lie between zero and one.")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise EquilibriumError("max_iterations must be a positive integer.")
    if not isinstance(section_count, int) or section_count < 3:
        raise EquilibriumError("section_count must be an integer >= 3.")
    lo, hi = reference_z_wl_m - draft_change_limit_m, reference_z_wl_m + draft_change_limit_m
    low = measure(mesh, lo, rho_kg_m3=rho_kg_m3, include_sections=False)
    high = measure(mesh, hi, rho_kg_m3=rho_kg_m3, include_sections=False)
    tolerance = target_volume_m3 * relative_tolerance
    if target_volume_m3 < low.volume_m3 - tolerance or target_volume_m3 > high.volume_m3 + tolerance:
        raise EquilibriumError(f"Target {target_volume_m3:.9g} m³ outside permitted draft range "
                               f"[{lo:.6g}, {hi:.6g}] m: volumes "
                               f"[{low.volume_m3:.9g}, {high.volume_m3:.9g}] m³.")
    for bound_z, bound_hydro in ((lo, low), (hi, high)):
        error = abs(bound_hydro.volume_m3 - target_volume_m3)
        if error <= tolerance:
            hydro = measure(mesh, bound_z, rho_kg_m3=rho_kg_m3, section_count=section_count)
            return EquilibriumResult(bound_z, hydro, 0, target_volume_m3, error / target_volume_m3)
    z = float(reference_z_wl_m)
    for iteration in range(1, max_iterations + 1):
        hydro = measure(mesh, z, rho_kg_m3=rho_kg_m3, include_sections=False)
        residual = hydro.volume_m3 - target_volume_m3
        if abs(residual) <= tolerance:
            hydro = measure(mesh, z, rho_kg_m3=rho_kg_m3, section_count=section_count)
            return EquilibriumResult(z, hydro, iteration, target_volume_m3, abs(residual) / target_volume_m3)
        if residual < 0:
            lo = z
        else:
            hi = z
        newton = z - residual / hydro.waterplane_area_m2
        z = float(newton if lo < newton < hi else 0.5 * (lo + hi))
    raise EquilibriumError(f"Equilibrium failed to converge in {max_iterations} iterations.")
