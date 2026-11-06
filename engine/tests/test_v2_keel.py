import unittest

import numpy as np

from hullopt.geometry import tessellate
from hullopt.optimize.mode_frame import derive_frame
from hullopt.optimize.shape_modes_v2 import mode_displacement_v2
from tests.hydro_fixtures import geometry_from_hgeo, sheer_hull_hgeo


class KeelTest(unittest.TestCase):
    def test_deadrise_is_smooth_through_both_sides_of_keel(self):
        mesh = tessellate(geometry_from_hgeo(sheer_hull_hgeo(40.0, 8.0, 4.0)), min_grid=40, max_grid=60, grid_factor=3)
        f = derive_frame(mesh, 2.2)
        self.assertGreater(f.deadrise_core_m, 0.0)
        x = 0.5 * (f.x_mid_a + f.x_mid_b)                      # middle of the midbody window
        z = f.z_keel + 0.25 * (f.z_wl - f.mid_wl_gap - f.z_keel)  # low on the section, where the mode acts
        slopes = []
        for h in (1e-4, 5e-5):
            p = np.array([[x, -h, z], [x, 0.0, z], [x, h, z]])
            d = mode_displacement_v2("deadrise", p, f)[:, 2]
            self.assertAlmostEqual(d[0], d[2], places=14)
            slopes.append(abs((d[2] - d[1]) / h))
        self.assertGreater(slopes[0], 0.0)
        self.assertLess(slopes[1] / slopes[0], .51)


if __name__ == "__main__":
    unittest.main()
