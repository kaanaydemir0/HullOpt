"""Published Michell benchmark, independent of the production quadrature.

Tuck (1989), p.371 corrects Michell's original scalar integral to 0.6157;
p.377 reproduces the cosine hull and its normalization. The source is saved
under validation/sources/tuck_1989_michell.pdf. This is verification of the
linear mathematical model, not validation against a towing-tank experiment.
"""

from __future__ import annotations

import math
import unittest

import numpy as np
from scipy.integrate import quad

from hullopt.resistance.base import Conditions
from hullopt.resistance.michell import MichellThinShip, OffsetHull


# Exact SI conversion of Michell's stated foot-second example. The historical
# gravitational acceleration is explicitly 32 ft/s^2 in this reconstruction.
FOOT_M = 0.3048
GRAVITY_M_S2 = 32 * FOOT_M
VELOCITY_M_S = 20 * FOOT_M
A_PER_M = math.pi / (100 * FOOT_M)
B_PER_M = math.pi / (20 * FOOT_M)
C_M = 4 * FOOT_M
K_M = VELOCITY_M_S**2 / GRAVITY_M_S2
FORCE_NORMALIZER_PER_DENSITY = (
    16 * GRAVITY_M_S2 / math.pi * A_PER_M**4 * C_M**2 * K_M**5
)


def cosine_hull_amplitude(lam: float) -> float:
    """Imaginary amplitude from elementary separated analytic integrals.

    This does not import the production panel integration. Positive depth d is
    used here; production coordinates use z=-d, as required by their contract.
    """
    wave_number = lam / K_M
    attenuation = lam**2 / K_M
    half_length = math.pi / A_PER_M
    draft = math.pi / B_PER_M
    # Integral of -a*c*sin(a*x)*sin(k*x) over +/-pi/a.
    if math.isclose(wave_number, A_PER_M, rel_tol=1e-10, abs_tol=0):
        longitudinal = -math.pi * C_M
    else:
        longitudinal = (-2 * A_PER_M**2 * C_M
                        * math.sin(wave_number * half_length)
                        / (A_PER_M**2 - wave_number**2))
    # Integral of (1+cos(b*d))*exp(-attenuation*d), d=0..pi/b.
    depth = (-math.expm1(-attenuation * draft) / attenuation
             + attenuation * (1 + math.exp(-attenuation * draft))
             / (attenuation**2 + B_PER_M**2))
    return longitudinal * depth


def analytic_original_integral() -> float:
    """Independent adaptive theta integral, lambda=sec(theta)."""
    def integrand(theta: float) -> float:
        lam = 1 / math.cos(theta)
        return cosine_hull_amplitude(lam)**2 * lam**3

    value, error = quad(integrand, 0, math.pi / 2,
                        epsabs=1e-12, epsrel=1e-10, limit=2000)
    if error > 1e-9:
        raise AssertionError(f"Independent reference quadrature error {error:g}")
    force_per_density = (4 * GRAVITY_M_S2**2
                         / (math.pi * VELOCITY_M_S**2) * value)
    return force_per_density / FORCE_NORMALIZER_PER_DENSITY


def original_offsets(nx: int, nz: int) -> OffsetHull:
    x = np.linspace(-100 * FOOT_M, 100 * FOOT_M, nx)
    z = np.linspace(-20 * FOOT_M, 0, nz)
    halfbreadths = (C_M * (1 + np.cos(A_PER_M * x)[None, :])
                   * (1 + np.cos(B_PER_M * z)[:, None]))
    return OffsetHull(x, z, halfbreadths, 0)


class PublishedMichellReferenceTests(unittest.TestCase):
    def test_original_cosine_hull_matches_tuck_and_refines_in_offset_space(self):
        reference = analytic_original_integral()
        # Tuck prints four decimal places. Do not use Michell's older 0.620.
        self.assertAlmostEqual(reference, 0.6157, delta=0.00005)
        conditions = Conditions((VELOCITY_M_S * 3600 / 1852,), rho_kg_m3=1)
        results = []
        for nx, nz in ((241, 81), (481, 161), (961, 321)):
            backend = MichellThinShip(
                original_offsets(nx, nz), gravity_m_s2=GRAVITY_M_S2,
                lambda_max=32, check_convergence=False,
            )
            result = backend.evaluate(None, conditions)
            results.append(result.values[0] / FORCE_NORMALIZER_PER_DENSITY)
        errors = [abs(value - reference) for value in results]
        self.assertLess(errors[1], errors[0] / 3)
        self.assertLess(errors[2], errors[1] / 3)
        self.assertAlmostEqual(results[-1], 0.6157, delta=0.00005)

    def test_cosine_hull_complex_amplitude_matches_analytic_inner_integrals(self):
        backend = MichellThinShip(
            original_offsets(961, 321), gravity_m_s2=GRAVITY_M_S2,
            lambda_max=32, check_convergence=False,
        )
        lambdas = np.array([1, 1.5, 2, 3, 5])
        actual = backend.amplitudes(lambdas, VELOCITY_M_S)
        expected = np.array([cosine_hull_amplitude(value) for value in lambdas])
        # The exact analytic surface is sampled into bilinear panels; at
        # lambda=5 its steep near-surface attenuation gives 0.06% grid error.
        np.testing.assert_allclose(actual.imag, expected, rtol=0.001, atol=1e-8)
        np.testing.assert_allclose(actual.real, 0, rtol=0, atol=1e-11)


if __name__ == "__main__":
    unittest.main()
