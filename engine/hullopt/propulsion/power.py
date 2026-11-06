"""Brake power from a total-resistance curve: propulsion factors, B-series propeller, machinery.

The chain follows the usual design-office method: design resistance with margins, effective
power, wake and thrust deduction, the propeller's operating point on its open-water curves,
quasi-propulsive efficiency, delivered and brake power, sea margin and engine load.

Formulas:
  * Holtrop (1984), twin screw:  w = 0.3095 CB + 10 CV CB - 0.23 D/sqrt(BT),
    t = 0.325 CB - 0.1885 D/sqrt(BT),  eta_R = 0.9737 + 0.111 (CP - 0.0225 lcb) - 0.06325 P/D,
    with CV = (1 + k) CF + CA, (1 + k) the hull-plus-appendage form factor.
  * Taylor wake: single screw 0.50 CB - 0.05, twin screw 0.55 CB - 0.20.
  * Keller: AE/A0 = (1.3 + 0.3 Z) T / ((p0 - pv) D^2) + K, p0 = p_atm + rho g h.
  * B-series open water: hullopt.propulsion.bseries.

The pitch ratio, when not given, is the one with the highest open-water efficiency at the
design point among P/D = 0.5, 0.525, ..., 1.4; the area ratio, when not given, is Keller's
minimum rounded up to 0.05. Speeds where a method is outside its range keep their values and
carry a note.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import PropulsionConfig
from .bseries import BSeriesPropeller

KNOT_TO_M_S = 1852.0 / 3600.0
PITCH_SWEEP = tuple(round(0.5 + 0.025 * i, 3) for i in range(37))
AREA_RATIO_STEP = 0.05


@dataclass(frozen=True)
class HullPropulsionInputs:
    """Hull quantities the propulsion factors need, at the resistance calculation's speeds."""

    lwl_m: float
    bwl_m: float
    draft_m: float
    cb: float
    cp: float
    lcb_percent_lwl: float
    form_factor_total: float          # 1 + k of hull and appendages together
    correlation_allowance: float      # CA
    friction_coefficients: tuple[float, ...]

    @classmethod
    def from_holtrop(cls, result: Any) -> "HullPropulsionInputs":
        """From a Holtrop1984 result: its parameters, coefficients and per-speed C_F."""
        p, c = result.metadata["parameters"], result.metadata["coefficients"]
        k1 = c["form_factor_1_plus_k1"]
        appendages = p.get("appendages") or ()
        s_app = sum(a["wetted_area_m2"] for a in appendages)
        k_total = k1
        if s_app > 0:
            k2 = sum(a["wetted_area_m2"] * a["form_factor_1_plus_k2"] for a in appendages) / s_app
            k_total = k1 + (k2 - k1) * s_app / (p["wetted_area_m2"] + s_app)
        return cls(lwl_m=p["lwl_m"], bwl_m=p["bwl_m"], draft_m=p["draft_m"], cb=c["cb"], cp=p["cp"],
                   lcb_percent_lwl=p["lcb_percent_lwl"], form_factor_total=k_total,
                   correlation_allowance=c["CA"],
                   friction_coefficients=tuple(result.metadata["friction_coefficient"]))


def _crossing(speeds: list[float], values: list[float | None], level: float) -> float | None:
    """Speed where an increasing curve reaches a level, by linear interpolation; None outside."""
    points = [(v, y) for v, y in zip(speeds, values) if y is not None]
    for (v0, y0), (v1, y1) in zip(points, points[1:]):
        if y0 <= level <= y1 and y1 > y0:
            return v0 + (level - y0) / (y1 - y0) * (v1 - v0)
    return None


def _interpolate(speeds: list[float], values: list[float], speed: float) -> float:
    return float(np.interp(speed, speeds, values))


def _wake(cfg: PropulsionConfig, hull: HullPropulsionInputs, cv: float) -> float:
    if cfg.wake == "given":
        return cfg.wake_fraction
    if cfg.wake == "taylor":
        if cfg.propellers == 1:
            return 0.50 * hull.cb - 0.05
        if cfg.propellers == 2:
            return 0.55 * hull.cb - 0.20
        raise ValueError("Taylor's wake formula covers single and twin screws only; give the wake fraction")
    d_bt = cfg.diameter_m / math.sqrt(hull.bwl_m * hull.draft_m)
    return 0.3095 * hull.cb + 10 * cv * hull.cb - 0.23 * d_bt


def _thrust_deduction(cfg: PropulsionConfig, hull: HullPropulsionInputs) -> float:
    if cfg.thrust_deduction == "given":
        return cfg.thrust_deduction_fraction
    return 0.325 * hull.cb - 0.1885 * cfg.diameter_m / math.sqrt(hull.bwl_m * hull.draft_m)


def _relative_rotative(cfg: PropulsionConfig, hull: HullPropulsionInputs, pitch_ratio: float) -> float:
    if cfg.relative_rotative == "given":
        return cfg.relative_rotative_efficiency
    return 0.9737 + 0.111 * (hull.cp - 0.0225 * hull.lcb_percent_lwl) - 0.06325 * pitch_ratio


def evaluate_propulsion(speeds_kn: list[float] | tuple[float, ...], total_resistance_n: list[float] | tuple[float, ...],
                        hull: HullPropulsionInputs, cfg: PropulsionConfig, *, rho_kg_m3: float,
                        gravity_m_s2: float = 9.80665, z_wl_m: float | None = None) -> dict:
    """Brake power at every speed of a total-resistance curve (N); the design speed must be one of them.

    With ``cfg.shaft_z_m`` the shaft immersion is ``z_wl_m - shaft_z_m`` (waterline z required).
    """
    if cfg.shaft_immersion_m is None:
        if z_wl_m is None:
            raise ValueError("The waterline z is needed to turn the shaft z into an immersion")
        immersion = z_wl_m - cfg.shaft_z_m
        if not immersion > 0:
            raise ValueError(f"The shaft centre (z = {cfg.shaft_z_m:g} m) is not below the waterline "
                             f"(z = {z_wl_m:g} m)")
        cfg = cfg.model_copy(update={"shaft_immersion_m": immersion})
    speeds = [float(v) for v in speeds_kn]
    resistance = [float(r) for r in total_resistance_n]
    if len(speeds) != len(resistance) or len(hull.friction_coefficients) != len(speeds):
        raise ValueError("One resistance value and one friction coefficient are required per speed")
    if speeds != sorted(set(speeds)):
        raise ValueError("Speeds must be strictly increasing")
    matches = [i for i, v in enumerate(speeds) if abs(v - cfg.design_speed_kn) <= 1e-9]
    if not matches:
        raise ValueError(f"The design speed {cfg.design_speed_kn:g} kn must be one of the computed speeds")
    i_des = matches[0]
    warnings: list[str] = []
    notes: list[list[str]] = [[] for _ in speeds]

    margin = 1 + cfg.appendage_margin + cfg.air_margin
    cv = [hull.form_factor_total * cf + hull.correlation_allowance for cf in hull.friction_coefficients]
    wake = [_wake(cfg, hull, value) for value in cv]
    t = _thrust_deduction(cfg, hull)
    n_props, diameter = cfg.propellers, cfg.diameter_m
    if not all(0 <= w < 1 for w in wake) or not 0 <= t < 1:
        raise ValueError("Wake fraction and thrust deduction must lie in [0, 1); check the inputs")

    # Design point: thrust per propeller, Keller's blade area, pitch-ratio choice.
    v_des = speeds[i_des] * KNOT_TO_M_S
    r_des = resistance[i_des] * margin
    thrust_factor = 1 + cfg.sea_margin if cfg.design_thrust == "service" else 1.0
    thrust_des = r_des / (n_props * (1 - t)) * thrust_factor
    va_des = v_des * (1 - wake[i_des])
    p0 = cfg.atmospheric_pressure_pa + rho_kg_m3 * gravity_m_s2 * cfg.shaft_immersion_m
    if p0 <= cfg.vapour_pressure_pa:
        raise ValueError("The static pressure at the shaft must exceed the vapour pressure")
    keller_k = cfg.keller_constant if cfg.keller_constant is not None else (0.2 if n_props == 1 else 0.1)

    def keller(thrust: float) -> float:
        return (1.3 + 0.3 * cfg.blades) * thrust / ((p0 - cfg.vapour_pressure_pa) * diameter**2) + keller_k

    keller_min = keller(thrust_des)
    if cfg.area_ratio is None:
        area_ratio = round(math.ceil(keller_min / AREA_RATIO_STEP - 1e-12) * AREA_RATIO_STEP, 12)
        area_source = "keller"
    else:
        area_ratio, area_source = cfg.area_ratio, "given"
        if area_ratio < keller_min:
            warnings.append(f"AE/A0 = {area_ratio:.3f} is below Keller's minimum {keller_min:.3f} "
                            "at the design point; cavitation risk.")
    c_des = thrust_des / (rho_kg_m3 * diameter**2 * va_des**2)
    sweep = []
    for pitch in PITCH_SWEEP:
        prop = BSeriesPropeller(cfg.blades, area_ratio, pitch)
        try:
            j = prop.solve_j(c_des)
            sweep.append({"pitch_ratio": pitch, "j": j, "eta0": prop.eta0(j)})
        except ValueError:
            sweep.append({"pitch_ratio": pitch, "j": None, "eta0": None})
    if cfg.pitch_ratio is None:
        valid = [s for s in sweep if s["eta0"] is not None]
        if not valid:
            raise ValueError("No pitch ratio in 0.5-1.4 delivers the design thrust")
        best = max(valid, key=lambda s: s["eta0"])
        pitch_ratio, pitch_source = best["pitch_ratio"], "best_eta0"
    else:
        pitch_ratio, pitch_source = cfg.pitch_ratio, "given"
    prop = BSeriesPropeller(cfg.blades, area_ratio, pitch_ratio)
    warnings += prop.outside_range()
    eta_r = _relative_rotative(cfg, hull, pitch_ratio)

    # Operating points: calm water, clean hull, the chosen propeller.
    rows: dict[str, list] = {key: [] for key in (
        "resistance_total_N", "resistance_design_N", "pe_W", "wake", "thrust_deduction", "eta_h", "va_m_s",
        "thrust_per_propeller_N", "j", "kt", "kq", "eta0", "eta_r", "eta_d", "pd_W", "pb_W",
        "pb_per_engine_W", "pb_service_W", "mcr_fraction", "required_installed_W", "rpm",
        "torque_per_propeller_Nm")}
    installed = cfg.engines * cfg.engine_mcr_kw * 1000 if cfg.engine_mcr_kw else None
    for i, speed in enumerate(speeds):
        v = speed * KNOT_TO_M_S
        r_design = resistance[i] * margin
        pe = r_design * v
        va = v * (1 - wake[i])
        thrust = r_design / (n_props * (1 - t))
        eta_h = (1 - t) / (1 - wake[i])
        values: dict[str, Any] = {"resistance_total_N": resistance[i], "resistance_design_N": r_design,
                                  "pe_W": pe, "wake": wake[i], "thrust_deduction": t, "eta_h": eta_h,
                                  "va_m_s": va, "thrust_per_propeller_N": thrust, "eta_r": eta_r}
        try:
            j = prop.solve_j(thrust / (rho_kg_m3 * diameter**2 * va**2))
        except ValueError as exc:
            notes[i].append(f"No B-series operating point: {exc}")
            j = None
        if j is not None and j > 0:
            kt, kq = prop.kt(j), prop.kq(j)
            eta0 = j * kt / (2 * math.pi * kq)
            eta_d = eta0 * eta_h * eta_r
            pd = pe / eta_d
            pb = pd / cfg.shaft_efficiency
            n = va / (j * diameter)
            values.update(j=j, kt=kt, kq=kq, eta0=eta0, eta_d=eta_d, pd_W=pd, pb_W=pb,
                          pb_per_engine_W=pb / cfg.engines, pb_service_W=pb * (1 + cfg.sea_margin),
                          mcr_fraction=pb * (1 + cfg.sea_margin) / installed if installed else None,
                          required_installed_W=(pb * (1 + cfg.sea_margin) + cfg.pto_kw * 1000) / cfg.mcr_fraction,
                          rpm=60 * n, torque_per_propeller_Nm=kq * rho_kg_m3 * n**2 * diameter**5 / eta_r)
            if installed and values["pb_service_W"] > installed:
                notes[i].append("Service power above the installed power")
        for key in rows:
            rows[key].append(values.get(key))

    design: dict[str, Any] = {
        "speed_kn": speeds[i_des], "resistance_total_N": resistance[i_des], "resistance_design_N": r_des,
        "thrust_factor": thrust_factor, "thrust_per_propeller_N": thrust_des, "wake": wake[i_des],
        "thrust_deduction": t, "va_m_s": va_des, "static_pressure_Pa": p0, "keller_constant": keller_k,
        "keller_area_ratio_min": keller_min, "area_ratio": area_ratio, "area_ratio_source": area_source,
        "kt_over_j2": c_des, "pitch_sweep": sweep, "pitch_ratio": pitch_ratio, "pitch_ratio_source": pitch_source,
        "eta0": rows["eta0"][i_des], "eta_r": eta_r, "j_zero_thrust": prop.j_zero_thrust(),
        "kt_coefficients": list(prop.kt_coefficients), "kq_coefficients": list(prop.kq_coefficients),
        "pb_W": rows["pb_W"][i_des], "pb_service_W": rows["pb_service_W"][i_des],
        "required_installed_W": rows["required_installed_W"][i_des], "installed_W": installed,
        "installed_sufficient": (rows["required_installed_W"][i_des] <= installed
                                 if installed and rows["required_installed_W"][i_des] is not None else None),
        "gear_ratio": (cfg.engine_rpm / rows["rpm"][i_des]
                       if cfg.engine_rpm and rows["rpm"][i_des] else None),
    }
    if installed:
        v_calm = _crossing(speeds, rows["pb_W"], installed)
        v_service = _crossing(speeds, rows["pb_service_W"], installed)
        v_fraction = _crossing(speeds, rows["pb_service_W"], installed * cfg.mcr_fraction)
        design.update(max_speed_calm_kn=v_calm, max_speed_service_kn=v_service,
                      speed_at_mcr_fraction_kn=v_fraction)
        for label, speed, factor in (("calm", v_calm, 1.0), ("service", v_service, 1 + cfg.sea_margin)):
            if speed is None:
                design[f"keller_at_mcr_{label}"] = None
                warnings.append(f"Installed power is not reached within the computed speeds ({label}); "
                                "widen the speed range to find the maximum speed.")
                continue
            r = _interpolate(speeds, rows["resistance_design_N"], speed)
            need = keller(r * factor / (n_props * (1 - t)))
            design[f"keller_at_mcr_{label}"] = need
            if area_ratio < need:
                warnings.append(f"At 100% MCR ({label}, {speed:.2f} kn) Keller asks for AE/A0 = {need:.3f}; "
                                f"the chosen {area_ratio:.3f} risks cavitation.")
        if design["installed_sufficient"] is False:
            warnings.append("Installed power is below the power required at the design speed.")
    methods = {"wake": {"holtrop": "Holtrop 1984 twin screw (effective wake)", "taylor": "Taylor",
                        "given": "given"}[cfg.wake],
               "thrust_deduction": {"holtrop": "Holtrop 1984 twin screw", "given": "given"}[cfg.thrust_deduction],
               "relative_rotative": {"holtrop": "Holtrop 1984 twin screw", "given": "given"}[cfg.relative_rotative],
               "propeller": "Wageningen B-series, Oosterveld and van Oossanen 1975, no Reynolds correction",
               "blade_area": "Keller" if area_source == "keller" else "given"}
    warnings.append("Brake power is a Python estimate from the chosen resistance curve, B-series open-water "
                    "curves and empirical propulsion factors; it is not validated by model tests or CFD.")
    return {"speeds_kn": speeds, "design": design, "per_speed": rows, "speed_notes": notes,
            "warnings": warnings, "methods": methods, "settings": cfg.model_dump()}
