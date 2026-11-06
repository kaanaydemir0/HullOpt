"""Published numerical benchmarks and risks in the 1984 resistance model."""

from __future__ import annotations

import dataclasses
import json
import math
import unittest

from hullopt.resistance.base import Conditions
from hullopt.resistance.friction import KNOT_TO_M_S
from hullopt.resistance.holtrop import (
    GRAVITY_M_S2, Holtrop1984, HoltropAppendage, HoltropParameters,
)


def published_1985_parameters() -> HoltropParameters:
    # JNAMT 97 (1985), printed pp18,20; independent author calculation, not
    # towing-tank data. Exact input/output transcription in HOLTROP_SOURCES.md.
    return HoltropParameters(
        lwl_m=50, bwl_m=12, draft_m=3.2, volume_m3=900, wetted_area_m2=584.9,
        cp=0.60096, cm=0.78, cwp=0.8, lcb_percent_lwl=-4.5,
        entrance_half_angle_deg=25, transom_area_m2=10,
        forward_draft_m=3.1, aft_draft_m=3.3,
        appendages=(HoltropAppendage("published appendages", 50, 3),),
    )


def reference_conditions(speeds=(25, 27, 29, 31, 33, 35)) -> Conditions:
    # Water properties are explicitly assumed for the independent 1985
    # example; the exact speed conversion differs from older rounded factors.
    return Conditions(tuple(speeds), rho_kg_m3=1025, nu_m2_s=1.18831e-6)


class PublishedBenchmarks(unittest.TestCase):
    def test_independent_1985_high_speed_wave_and_total(self):
        result = Holtrop1984(
            published_1985_parameters(), gravity_m_s2=9.81,
            entrance_angle_policy="measured",
        ).evaluate(None, reference_conditions())
        components = result.metadata["components_N"]
        for actual, published in zip(components["RW"], (475, 512, 539, 564, 590, 618)):
            self.assertAlmostEqual(actual / 1000, published, delta=1.0)
        for actual, published in zip(components["RAPP"], (21, 24, 28, 31, 35, 39)):
            self.assertAlmostEqual(actual / 1000, published, delta=0.6)
        for actual, published in zip(components["RTR"], (25, 16, 2, 0, 0, 0)):
            self.assertAlmostEqual(actual / 1000, published, delta=0.6)
        # RT tolerance is 1% because water viscosity is not stated in this
        # published example. Wave coefficients/force are checked separately
        # at the source's printed precision, not relaxed to this tolerance.
        for actual, published in zip(result.values, (662, 715, 756, 807, 864, 925)):
            self.assertLess(abs(actual / (published * 1000) - 1), 0.01)
        c = result.metadata["coefficients"]
        for name, expected, tolerance in (
            ("run_length_m", 14.1728, 0.0001),
            ("form_factor_1_plus_k1", 1.297, 0.00005),
            ("c17", 1.4133, 0.00005), ("m3", -2.0298, 0.00005),
            ("c2", 1, 1e-12), ("c5", 0.7329, 0.00005),
            ("lambda", 0.7440, 0.00005), ("c15", -1.69385, 1e-10),
            ("CA", 0.00064, 0.000005),
        ):
            self.assertAlmostEqual(c[name], expected, delta=tolerance, msg=name)

    def test_original_1982_unchanged_components(self):
        # Original Holtrop/Mennen 1982 p170. LCB is -0.75 percent LWL;
        # the -2.02 percent LPP input in the same table has another origin.
        p = HoltropParameters(
            lwl_m=205, bwl_m=32, draft_m=10, volume_m3=37500,
            wetted_area_m2=7381.45, cp=37500 / (0.98 * 32 * 10 * 205),
            cm=0.98, cwp=0.75, lcb_percent_lwl=-0.75,
            transom_area_m2=16, bulb_area_m2=20, bulb_height_m=4,
            stern_parameter=10, appendages=(HoltropAppendage("published", 50, 1.5),),
        )
        result = Holtrop1984(p, gravity_m_s2=9.81).evaluate(None, reference_conditions((25,)))
        c, force = result.metadata["coefficients"], result.metadata["components_N"]
        self.assertAlmostEqual(c["run_length_m"], 81.385, delta=0.001)
        self.assertAlmostEqual(c["entrance_half_angle_deg"], 12.08, delta=0.005)
        self.assertAlmostEqual(c["c3"], 0.02119, delta=0.000005)
        self.assertAlmostEqual(c["CA"], 0.000352, delta=0.0000006)
        self.assertAlmostEqual(force["RF"][0] / 1000, 869.63, delta=0.03)
        self.assertAlmostEqual(force["RAPP"][0] / 1000, 8.83, delta=0.02)
        self.assertAlmostEqual(force["RB"][0] / 1000, 0.049, delta=0.001)
        self.assertEqual(force["RTR"][0], 0)
        # The paper's printed RA=221.98 kN is inconsistent with its own
        # S, speed, density and CA (recomputed RA=220.57 kN). Do not use it
        # as an exact target. 1984 also changed k1/RW, hence no 1982 RT test.
        self.assertAlmostEqual(force["RA"][0] / 1000, 221.98, delta=1.5)


class PhysicalAndNumericalRisks(unittest.TestCase):
    def setUp(self):
        self.p = dataclasses.replace(published_1985_parameters(), appendages=(), transom_area_m2=0)

    def at_froude(self, *values):
        speeds = tuple(fn * math.sqrt(GRAVITY_M_S2 * self.p.lwl_m) / KNOT_TO_M_S for fn in values)
        return Holtrop1984(self.p).evaluate(None, reference_conditions(speeds))

    def test_wave_transition_continuity_and_linear_middle(self):
        r = self.at_froude(0.4 - 1e-9, 0.4, 0.4 + 1e-9, 0.475, 0.55 - 1e-9, 0.55, 0.55 + 1e-9)
        wave = r.metadata["components_N"]["RW"]
        for i, j in ((0, 1), (1, 2), (4, 5), (5, 6)):
            self.assertLess(abs(wave[i] / wave[j] - 1), 1e-6)
        self.assertAlmostEqual(wave[3], (wave[1] + wave[5]) / 2, delta=1e-8)
        self.assertEqual(r.metadata["wave_branch"][3], "linear_0.4_to_0.55")

    def test_absent_bulb_transom_appendage_exact_zero(self):
        r = self.at_froude(0.15, 0.4, 0.6)
        for key in ("RB", "RTR", "RAPP"):
            self.assertEqual(r.metadata["components_N"][key], [0.0] * 3)
        self.assertEqual(r.metadata["coefficients"]["c2"], 1)
        self.assertFalse(r.metadata["component_flags"]["bulb_specified"])
        self.assertFalse(r.metadata["component_flags"]["appendages_specified"])
        self.assertTrue(any("ABT=0" in warning for warning in r.warnings))
        json.dumps(r.to_dict(), allow_nan=False)

    def test_transom_switch_changes_skin_friction_and_CA_only(self):
        p = dataclasses.replace(published_1985_parameters(), transom_wetted_area_m2=10.5)
        a = Holtrop1984(p, include_transom=True).evaluate(None, reference_conditions())
        b = Holtrop1984(p, include_transom=False).evaluate(None, reference_conditions())
        for key in ("RW", "RTR", "RB", "RAPP"):
            self.assertEqual(a.metadata["components_N"][key], b.metadata["components_N"][key])
        self.assertLess(b.values[0], a.values[0])
        ratio = (p.wetted_area_m2 - 10.5) / p.wetted_area_m2
        for key in ("RF", "Rform", "RA"):
            for va, vb in zip(a.metadata["components_N"][key], b.metadata["components_N"][key]):
                self.assertAlmostEqual(vb / va, ratio)

    def test_transom_pressure_vanishes_continuously_at_fnt5(self):
        p = published_1985_parameters()
        critical_velocity = 5 * math.sqrt(2 * GRAVITY_M_S2 * p.transom_area_m2 / (p.bwl_m * (1 + p.cwp)))
        speeds = tuple(critical_velocity * factor / KNOT_TO_M_S for factor in (1 - 1e-8, 1, 1 + 1e-8))
        r = Holtrop1984(p).evaluate(None, reference_conditions(speeds))
        tr = r.metadata["components_N"]["RTR"]
        self.assertGreater(tr[0], 0)
        self.assertLess(tr[0], 0.01)
        self.assertAlmostEqual(tr[1], 0, delta=1e-8)
        self.assertEqual(tr[2], 0)

    def test_roughness_changes_CA_only(self):
        rough = dataclasses.replace(self.p, roughness_m=300e-6)
        a = Holtrop1984(self.p).evaluate(None, reference_conditions())
        b = Holtrop1984(rough).evaluate(None, reference_conditions())
        self.assertGreater(b.metadata["coefficients"]["CA"], a.metadata["coefficients"]["CA"])
        for key in ("RF", "Rform", "RW", "RTR", "RB", "RAPP"):
            self.assertEqual(a.metadata["components_N"][key], b.metadata["components_N"][key])

    def test_force_scale_under_Froude_and_Reynolds_similarity(self):
        # Scale by four, preserve Fn and Re with V'=2V and nu'=8nu.
        # CA is an empirical full-scale correction and intentionally excluded.
        a = Holtrop1984(self.p).evaluate(None, reference_conditions((12,)))
        scale = 4
        p = dataclasses.replace(self.p, lwl_m=self.p.lwl_m * scale,
                                bwl_m=self.p.bwl_m * scale, draft_m=self.p.draft_m * scale,
                                forward_draft_m=self.p.forward_draft_m * scale,
                                aft_draft_m=self.p.aft_draft_m * scale,
                                volume_m3=self.p.volume_m3 * scale**3,
                                wetted_area_m2=self.p.wetted_area_m2 * scale**2)
        b = Holtrop1984(p).evaluate(None, Conditions((24,), 1025, 1.18831e-6 * 8))
        for key in ("RF", "Rform", "RW"):
            self.assertAlmostEqual(b.metadata["components_N"][key][0] / a.metadata["components_N"][key][0], scale**3)

    def test_component_sum_is_total_and_form_is_increment(self):
        r = self.at_froude(0.3, 0.45, 0.65)
        c = r.metadata["components_N"]
        factor = r.metadata["coefficients"]["form_factor_1_plus_k1"]
        for i, total in enumerate(r.values):
            self.assertAlmostEqual(total, sum(v[i] for v in c.values()), delta=1e-9)
            self.assertAlmostEqual((c["RF"][i] + c["Rform"][i]) / c["RF"][i], factor)

    def test_measured_proxy_is_opt_in_regression_default(self):
        values = dataclasses.asdict(self.p)
        values["cx"] = values.pop("cm")
        values["entrance_half_angle_deg"] = 25
        r = Holtrop1984().evaluate(values, reference_conditions((15,)))
        self.assertEqual(r.metadata["entrance_angle_policy"], "regression")
        self.assertNotEqual(r.metadata["coefficients"]["entrance_half_angle_deg"], 25)
        self.assertTrue(any("maximum-section" in w for w in r.warnings))
        measured = Holtrop1984(entrance_angle_policy="measured").evaluate(values, reference_conditions((15,)))
        self.assertEqual(measured.metadata["coefficients"]["entrance_half_angle_deg"], 25)
        self.assertTrue(any("forward-10%" in w for w in measured.warnings))
        self.assertEqual(measured.metadata["parameters"]["lcb_percent_lwl"], -4.5)

    def test_domain_and_missing_data_are_rejected(self):
        for change in ({"cp": 0.25}, {"cp": 1}, {"cwp": 1}, {"bulb_area_m2": 1, "bulb_height_m": 2.0},
                       {"transom_area_m2": 1000}, {"roughness_m": 0}, {"lwl_m": math.nan},
                       {"entrance_half_angle_deg": 90}, {"aft_draft_m": 4}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                dataclasses.replace(self.p, **change)
        with self.assertRaisesRegex(ValueError, "entrance_half_angle_deg"):
            Holtrop1984(dataclasses.replace(self.p, entrance_half_angle_deg=None), entrance_angle_policy="measured").evaluate(None, reference_conditions())
        with self.assertRaisesRegex(ValueError, "measured cp"):
            HoltropParameters.from_measurements({"lwl_m": 50, "bwl_m": 12, "draft_m": 3.2,
                                                "volume_m3": 900, "wetted_area_m2": 584.9, "cp": None})
        with self.assertRaisesRegex(ValueError, "Reynolds"):
            Holtrop1984(self.p).evaluate(None, Conditions((0.0001,)))


if __name__ == "__main__":
    unittest.main()
