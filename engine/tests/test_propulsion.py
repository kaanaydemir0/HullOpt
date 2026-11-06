"""Brake power: B-series open water, settings checks, margins and the service call."""
import math
import unittest
from pathlib import Path

from hullopt.config import PropulsionConfig
from hullopt.propulsion import BSeriesPropeller, HullPropulsionInputs, evaluate_propulsion

# A generic twin-screw case (not any project's hull): a 40 m displacement hull and a smooth resistance curve.
SPEEDS_KN = [10.0, 12.0, 14.0, 16.0]
RESISTANCE_N = [18.0e3, 28.0e3, 42.0e3, 62.0e3]
LWL, NU, RHO, Z_WL = 40.0, 1.18831e-6, 1025.9, 2.2


def ittc57(speed_kn):
    re = speed_kn * 1852.0 / 3600.0 * LWL / NU
    return 0.075 / (math.log10(re) - 2.0) ** 2


def synthetic_case(**overrides):
    hull = HullPropulsionInputs(lwl_m=LWL, bwl_m=8.0, draft_m=2.2, cb=0.45, cp=0.62, lcb_percent_lwl=-2.0,
                                form_factor_total=1.25, correlation_allowance=0.0004,
                                friction_coefficients=tuple(ittc57(v) for v in SPEEDS_KN))
    settings = dict(diameter_m=1.4, shaft_z_m=0.8, design_speed_kn=14.0, propellers=2, blades=5, wake="given",
                    wake_fraction=0.05, appendage_margin=0.05, air_margin=0.02, sea_margin=0.15, engines=2,
                    engine_mcr_kw=1000.0)
    settings.update(overrides)
    return evaluate_propulsion(SPEEDS_KN, RESISTANCE_N, hull, PropulsionConfig(**settings),
                               rho_kg_m3=RHO, gravity_m_s2=9.81, z_wl_m=Z_WL)


class PropulsionBasicsTests(unittest.TestCase):
    def test_open_water_point_solves_the_thrust_equation(self):
        prop = BSeriesPropeller(4, 0.55, 0.9)
        for c in (0.1, 0.5, 2.0):
            j = prop.solve_j(c)
            self.assertAlmostEqual(prop.kt(j), c * j * j, places=13)
            self.assertTrue(0 < j < prop.j_zero_thrust())
            self.assertTrue(0 < prop.eta0(j) < 1)

    def test_outside_range_is_named(self):
        self.assertEqual(BSeriesPropeller(4, 0.55, 0.9).outside_range(), [])
        notes = BSeriesPropeller(4, 1.2, 1.6).outside_range()
        self.assertEqual(len(notes), 2)

    def test_settings_must_be_consistent(self):
        base = dict(diameter_m=1.5, shaft_immersion_m=1.7, design_speed_kn=12.0)
        PropulsionConfig(**base)
        for bad in (dict(wake="given"), dict(wake_fraction=0.1), dict(propellers=1), dict(shaft_z_m=0.9),
                    dict(propellers=1, wake="taylor"), dict(blades=8), dict(sea_margin=-0.1)):
            with self.assertRaises(ValueError, msg=str(bad)):
                PropulsionConfig(**{**base, **bad})
        PropulsionConfig(**{**base, "propellers": 1, "wake": "taylor", "thrust_deduction": "given",
                            "thrust_deduction_fraction": 0.18, "relative_rotative": "given",
                            "relative_rotative_efficiency": 1.0})

    def test_shaft_must_be_below_the_waterline(self):
        with self.assertRaisesRegex(ValueError, "not below the waterline"):
            synthetic_case(shaft_z_m=3.0)

    def test_design_speed_must_be_computed(self):
        with self.assertRaisesRegex(ValueError, "design speed"):
            synthetic_case(design_speed_kn=16.1)

    def test_power_grows_with_margins(self):
        plain = synthetic_case(appendage_margin=0.0, air_margin=0.0)["per_speed"]["pb_W"]
        margin = synthetic_case()["per_speed"]["pb_W"]
        self.assertTrue(all(m > p for m, p in zip(margin, plain)))
        self.assertTrue(all(math.isfinite(v) for v in margin))



class PropulsionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from tests.test_morph import write_hull
        cls.directory = tempfile.TemporaryDirectory()
        cls.path = str(write_hull(Path(cls.directory.name)))

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def call(self, **extra):
        from hullopt.service import Session
        from tests.test_morph import Z
        from tests.test_service import call
        params = {"hgeo": self.path, "z_wl_m": Z, "speeds_kn": [8.0, 10.0, 12.0], "methods": ["holtrop", "friction"],
                  "tessellation": {"min_grid": 40, "max_grid": 60},
                  "propulsion": {"diameter_m": 1.4, "shaft_z_m": 0.7, "design_speed_kn": 11.0,
                                 "blades": 4, "engine_mcr_kw": 800.0}}
        params.update(extra)
        return call(Session(), "resistance.evaluate", params)[0]

    def test_brake_power_at_every_speed_and_the_design_speed(self):
        response = self.call()
        self.assertNotIn("error", response, response.get("error"))
        r = response["result"]
        self.assertEqual(r["conditions"]["speeds_kn"], [8.0, 10.0, 11.0, 12.0])
        self.assertEqual(r["conditions"]["added_speeds_kn"], [11.0])
        prop = r["propulsion"]
        self.assertEqual(prop["speeds_kn"], [8.0, 10.0, 11.0, 12.0])
        self.assertEqual(prop["design"]["speed_kn"], 11.0)
        pb = prop["per_speed"]["pb_W"]
        self.assertTrue(all(b > a > 0 for a, b in zip(pb, pb[1:])))
        holtrop = next(x for x in r["results"] if x["backend"] == "PY-HOLTROP-1984")
        for pe, rt, v in zip(prop["per_speed"]["pe_W"], holtrop["values"], prop["speeds_kn"]):
            self.assertAlmostEqual(pe, rt * v * 1852 / 3600, delta=1e-6 * pe)
        self.assertGreater(prop["design"]["area_ratio"], 0)
        self.assertIn(prop["design"]["pitch_ratio"], [round(0.5 + 0.025 * i, 3) for i in range(37)])

    def test_bad_propulsion_requests(self):
        from hullopt.service import INVALID_PARAMS
        for extra in ({"methods": ["friction"]},
                      {"propulsion": {"diameter_m": 1.4, "shaft_immersion_m": 1.5}},
                      {"propulsion": {"diameter_m": -1.0, "shaft_immersion_m": 1.5, "design_speed_kn": 10.0}},
                      {"propulsion": {"diameter_m": 1.4, "shaft_immersion_m": 1.5, "design_speed_kn": 10.0,
                                      "propellers": 1}}):
            response = self.call(**extra)
            self.assertEqual(response["error"]["code"], INVALID_PARAMS, extra)


if __name__ == "__main__":
    unittest.main()
