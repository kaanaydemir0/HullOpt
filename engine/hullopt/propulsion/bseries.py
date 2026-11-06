"""Wageningen B-series open-water polynomials (Oosterveld and van Oossanen 1975, Rn = 2e6).

K_T = sum C * J**s * (P/D)**t * (AE/A0)**u * Z**v over 39 terms; K_Q likewise over 47 terms.
The published range is Z 2-7, AE/A0 0.30-1.05, P/D 0.5-1.4; outside it the polynomials only
extrapolate. No Reynolds-number correction is applied.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import brentq

# (C, s: J, t: P/D, u: AE/A0, v: Z)
KT_TERMS = (
    (0.00880496, 0, 0, 0, 0),
    (-0.204554, 1, 0, 0, 0),
    (0.166351, 0, 1, 0, 0),
    (0.158114, 0, 2, 0, 0),
    (-0.147581, 2, 0, 1, 0),
    (-0.481497, 1, 1, 1, 0),
    (0.415437, 0, 2, 1, 0),
    (0.0144043, 0, 0, 0, 1),
    (-0.0530054, 2, 0, 0, 1),
    (0.0143481, 0, 1, 0, 1),
    (0.0606826, 1, 1, 0, 1),
    (-0.0125894, 0, 0, 1, 1),
    (0.0109689, 1, 0, 1, 1),
    (-0.133698, 0, 3, 0, 0),
    (0.00638407, 0, 6, 0, 0),
    (-0.00132718, 2, 6, 0, 0),
    (0.168496, 3, 0, 1, 0),
    (-0.0507214, 0, 0, 2, 0),
    (0.0854559, 2, 0, 2, 0),
    (-0.0504475, 3, 0, 2, 0),
    (0.010465, 1, 6, 2, 0),
    (-0.00648272, 2, 6, 2, 0),
    (-0.00841728, 0, 3, 0, 1),
    (0.0168424, 1, 3, 0, 1),
    (-0.00102296, 3, 3, 0, 1),
    (-0.0317791, 0, 3, 1, 1),
    (0.018604, 1, 0, 2, 1),
    (-0.00410798, 0, 2, 2, 1),
    (-0.000606848, 0, 0, 0, 2),
    (-0.0049819, 1, 0, 0, 2),
    (0.0025983, 2, 0, 0, 2),
    (-0.000560528, 3, 0, 0, 2),
    (-0.00163652, 1, 2, 0, 2),
    (-0.000328787, 1, 6, 0, 2),
    (0.000116502, 2, 6, 0, 2),
    (0.000690904, 0, 0, 1, 2),
    (0.00421749, 0, 3, 1, 2),
    (5.65229e-05, 3, 6, 1, 2),
    (-0.00146564, 0, 3, 2, 2),
)
KQ_TERMS = (
    (0.00379368, 0, 0, 0, 0),
    (0.00886523, 2, 0, 0, 0),
    (-0.032241, 1, 1, 0, 0),
    (0.00344778, 0, 2, 0, 0),
    (-0.0408811, 0, 1, 1, 0),
    (-0.108009, 1, 1, 1, 0),
    (-0.0885381, 2, 1, 1, 0),
    (0.188561, 0, 2, 1, 0),
    (-0.00370871, 1, 0, 0, 1),
    (0.00513696, 0, 1, 0, 1),
    (0.0209449, 1, 1, 0, 1),
    (0.00474319, 2, 1, 0, 1),
    (-0.00723408, 2, 0, 1, 1),
    (0.00438388, 1, 1, 1, 1),
    (-0.0269403, 0, 2, 1, 1),
    (0.0558082, 3, 0, 1, 0),
    (0.0161886, 0, 3, 1, 0),
    (0.00318086, 1, 3, 1, 0),
    (0.015896, 0, 0, 2, 0),
    (0.0471729, 1, 0, 2, 0),
    (0.0196283, 3, 0, 2, 0),
    (-0.0502782, 0, 1, 2, 0),
    (-0.030055, 3, 1, 2, 0),
    (0.0417122, 2, 2, 2, 0),
    (-0.0397722, 0, 3, 2, 0),
    (-0.00350024, 0, 6, 2, 0),
    (-0.0106854, 3, 0, 0, 1),
    (0.00110903, 3, 3, 0, 1),
    (-0.000313912, 0, 6, 0, 1),
    (0.0035985, 3, 0, 1, 1),
    (-0.00142121, 0, 6, 1, 1),
    (-0.00383637, 1, 0, 2, 1),
    (0.0126803, 0, 2, 2, 1),
    (-0.00318278, 2, 3, 2, 1),
    (0.00334268, 0, 6, 2, 1),
    (-0.00183491, 1, 1, 0, 2),
    (0.000112451, 3, 2, 0, 2),
    (-2.97228e-05, 3, 6, 0, 2),
    (0.000269551, 1, 0, 1, 2),
    (0.00083265, 2, 0, 1, 2),
    (0.00155334, 0, 2, 1, 2),
    (0.000302683, 0, 6, 1, 2),
    (-0.0001843, 0, 0, 2, 2),
    (-0.000425399, 0, 3, 2, 2),
    (8.69243e-05, 3, 3, 2, 2),
    (-0.0004659, 0, 6, 2, 2),
    (5.54194e-05, 1, 6, 2, 2),
)

_KT = np.array(KT_TERMS, dtype=float)
_KQ = np.array(KQ_TERMS, dtype=float)

BLADES_RANGE = (2, 7)
AREA_RATIO_RANGE = (0.30, 1.05)
PITCH_RATIO_RANGE = (0.5, 1.4)


def polynomial_in_j(terms: np.ndarray, pitch_ratio: float, area_ratio: float, blades: float) -> np.ndarray:
    """Coefficients c0..c3 of the open-water polynomial in J (ascending powers)."""
    weights = terms[:, 0] * pitch_ratio ** terms[:, 2] * area_ratio ** terms[:, 3] * blades ** terms[:, 4]
    coefficients = np.zeros(4)
    for power in range(4):
        coefficients[power] = float(np.sum(weights[terms[:, 1] == power]))
    return coefficients


def _newton(f, df, x0: float, low: float, high: float, iterations: int = 60) -> float | None:
    """Newton's method from x0; None unless it converges inside [low, high]."""
    x = x0
    for _ in range(iterations):
        slope = df(x)
        if slope == 0 or not math.isfinite(slope):
            return None
        step = f(x) / slope
        x -= step
        if not math.isfinite(x):
            return None
        if abs(step) <= 1e-15 * max(1.0, abs(x)):
            break
    else:
        return None
    return x if low <= x <= high else None


@dataclass(frozen=True)
class BSeriesPropeller:
    """One B-series propeller: blade number, expanded area ratio and pitch ratio."""

    blades: int
    area_ratio: float
    pitch_ratio: float
    kt_coefficients: np.ndarray = field(init=False, repr=False, compare=False)
    kq_coefficients: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.blades, int) or self.blades < 2:
            raise ValueError("A B-series propeller needs an integer blade number of at least 2")
        for name in ("area_ratio", "pitch_ratio"):
            value = getattr(self, name)
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"{name} must be positive and finite")
        object.__setattr__(self, "kt_coefficients",
                           polynomial_in_j(_KT, self.pitch_ratio, self.area_ratio, self.blades))
        object.__setattr__(self, "kq_coefficients",
                           polynomial_in_j(_KQ, self.pitch_ratio, self.area_ratio, self.blades))

    def kt(self, j: float) -> float:
        a = self.kt_coefficients
        return float(a[0] + a[1] * j + a[2] * j**2 + a[3] * j**3)

    def kq(self, j: float) -> float:
        b = self.kq_coefficients
        return float(b[0] + b[1] * j + b[2] * j**2 + b[3] * j**3)

    def eta0(self, j: float) -> float:
        return j * self.kt(j) / (2 * math.pi * self.kq(j))

    def outside_range(self) -> list[str]:
        """Parameters outside the published B-series range."""
        notes = []
        if not BLADES_RANGE[0] <= self.blades <= BLADES_RANGE[1]:
            notes.append(f"Z = {self.blades} is outside the B-series range {BLADES_RANGE[0]}-{BLADES_RANGE[1]}.")
        if not AREA_RATIO_RANGE[0] <= self.area_ratio <= AREA_RATIO_RANGE[1]:
            notes.append(f"AE/A0 = {self.area_ratio:.3f} is outside the B-series range "
                         f"{AREA_RATIO_RANGE[0]}-{AREA_RATIO_RANGE[1]}.")
        if not PITCH_RATIO_RANGE[0] <= self.pitch_ratio <= PITCH_RATIO_RANGE[1]:
            notes.append(f"P/D = {self.pitch_ratio:.3f} is outside the B-series range "
                         f"{PITCH_RATIO_RANGE[0]}-{PITCH_RATIO_RANGE[1]}.")
        return notes

    def j_zero_thrust(self) -> float:
        """Advance ratio where K_T = 0 (Newton from J = 1.2, else a bracketed search)."""
        a = self.kt_coefficients
        if a[0] <= 0:
            raise ValueError("K_T at J = 0 is not positive; the propeller gives no thrust")
        f = self.kt
        df = lambda j: float(a[1] + 2 * a[2] * j + 3 * a[3] * j**2)
        high = 1.0
        while f(high) > 0:
            high *= 1.5
            if high > 20:
                raise ValueError("K_T does not reach zero below J = 20")
        root = _newton(f, df, 1.2, 0.0, high)
        return root if root is not None else brentq(f, 0.0, high, xtol=1e-15, rtol=4 * np.finfo(float).eps)

    def solve_j(self, kt_over_j2: float) -> float:
        """Advance ratio where K_T(J) = c * J**2: the propeller delivers the required thrust."""
        if not (math.isfinite(kt_over_j2) and kt_over_j2 > 0):
            raise ValueError("K_T / J^2 must be positive and finite")
        a, c = self.kt_coefficients, kt_over_j2
        f = lambda j: float(a[3] * j**3 + (a[2] - c) * j**2 + a[1] * j + a[0])
        df = lambda j: float(3 * a[3] * j**2 + 2 * (a[2] - c) * j + a[1])
        high = self.j_zero_thrust()
        root = _newton(f, df, 1.0, 0.0, high)
        return root if root is not None else brentq(f, 0.0, high, xtol=1e-15, rtol=4 * np.finfo(float).eps)
