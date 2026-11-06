import unittest
import numpy as np
from hullopt.optimize.material_lines import curvature_3d,energy,MaterialLineSet
from hullopt.geometry import tessellate,evaluate_surface
from tests.hydro_fixtures import geometry_from_hgeo,box_hgeo,semicircle_prism_geometry


class MaterialGeometryTests(unittest.TestCase):
    def test_uv_metadata_reproduces_trimmed_triangle_vertices(self):
        for g in [geometry_from_hgeo(box_hgeo()),semicircle_prism_geometry()]:
            m=tessellate(g,min_grid=6,max_grid=12,include_uv=True)
            for fid in np.unique(m.face_ids):
                mask=m.face_ids==fid;uv=m.uv_triangles[mask]
                q=evaluate_surface(g.faces[int(fid)].surface,uv[:,:,0],uv[:,:,1])
                np.testing.assert_allclose(q,m.triangles[mask],atol=2e-13)

    def test_3d_circle_curvature_is_invariant_under_out_of_plane_rotation(self):
        t=np.linspace(.05,1.5,601)
        p=np.stack([2*np.cos(t),2*np.sin(t),0*t],axis=1)
        angle=.8;rotation=np.array([[1,0,0],[0,np.cos(angle),-np.sin(angle)],[0,np.sin(angle),np.cos(angle)]])
        k,s=curvature_3d(p,[0,0,1]);k1,s1=curvature_3d(p@rotation.T,[0,0,1])
        np.testing.assert_allclose(k[80:-80],.5,rtol=.002)
        np.testing.assert_allclose(k1,k,atol=2e-11)
        np.testing.assert_allclose(s1,s,atol=2e-13)
        mask=(np.arange(len(k))>80)&(np.arange(len(k))<len(k)-80)
        self.assertAlmostEqual(energy(k,s,mask),energy(k1,s1,mask),places=9)

    def test_addresses_evaluate_actual_nurbs_not_triangle_interpolation(self):
        g=semicircle_prism_geometry();uv=np.array([[.27,.37],[.64,1.42]])
        p=MaterialLineSet.evaluate_addresses(g,np.zeros(2,int),uv)
        np.testing.assert_allclose(np.sum(p[:,1:]**2,axis=1),1.,atol=2e-14)
        np.testing.assert_allclose(p[:,0],4*uv[:,0],atol=2e-14)

    def test_degenerate_or_nonfinite_material_lines_fail(self):
        for p in [np.zeros((10,3)),np.full((10,3),np.nan),np.zeros((4,3))]:
            with self.assertRaises(ValueError):curvature_3d(p,[0,0,1])
