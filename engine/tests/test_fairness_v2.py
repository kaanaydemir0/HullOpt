import unittest
import numpy as np
from hullopt.optimize.fairness import Curve,LineSet,FairnessPlan,kappa,inflections,resample,H,SIGMA,starboard

# A fixed plan for synthetic sections; the bilge window covers the circles at x = 17 m.
PLAN=FairnessPlan(stations=(17.0,),waterlines=(),buttocks=(),spacing_m=H,knuckle_curvature=15.0,end_mask_x=-1e9,
                  min_length_m=1.0,inflection_threshold=0.05,dk_max=0.5,ratio=(0.5,2.0),ratio_min_curvature=0.3,
                  energy_max=1.15,bilge_radius_min=0.75,bilge_x=(13.0,21.0),bilge_z_max=2.25)

def reference(points):
    k,ds=kappa(points[:,[1,2]])
    mask=np.ones(len(points),bool);mask[:25]=False;mask[-25:]=False
    return LineSet([Curve('st','st 17.000#0',(1,2),points,k,mask,0.05,ds)],PLAN)

class FairnessTests(unittest.TestCase):
    def test_closed_contour_start_does_not_split_starboard_side(self):
        t=np.linspace(0,2*np.pi,201)
        p=np.stack([np.cos(t),np.cos(t),np.sin(t)],axis=1)
        self.assertEqual(len(starboard([p])),1)
    def circle(self,r=2.):
        t=np.linspace(0.05,1.5,600)
        return np.stack([np.full_like(t,17),r*np.cos(t),r*np.sin(t)],axis=1)

    def test_analytic_circle_and_identity(self):
        p=self.circle();k,_=kappa(p[:,[1,2]])
        # Gaussian smoothing of sinusoidal circle coordinates reduces radius
        # by exp(-sigma_angle**2/2); check that analytic smoothed circle.
        expected=.5*np.exp(.5*(SIGMA*1.45/599)**2)
        np.testing.assert_allclose(k[30:-30],expected,atol=1e-6)
        self.assertTrue(reference(p).evaluate([p])['pass'])

    def test_new_large_curvature_is_not_masked(self):
        p=self.circle();q=p.copy();q[:,1:]*=.01
        r=reference(p).evaluate([q])
        self.assertFalse(r['pass_G2']);self.assertGreater(r['dk_max'],15)

    def test_bilge_radius_gate(self):
        p=self.circle();q=p.copy();q[:,1:]*=.70
        r=reference(p).evaluate([q])
        self.assertFalse(r['pass_G5'])
        self.assertAlmostEqual(r['bilge_radius_ratio_min'],.70,places=7)

    def test_new_energy_on_straight_reference_is_rejected(self):
        t=np.linspace(0,10,501)
        p=np.stack([np.full_like(t,17),t,np.ones_like(t)],axis=1)
        q=p.copy();q[:,2]+=.04*np.sin(2*t)
        r=reference(p).evaluate([q])
        self.assertFalse(r['pass_G3'])

    def test_inflection_persistence_and_masks(self):
        k=np.r_[np.ones(20),-np.ones(9),np.ones(20)]
        self.assertEqual(inflections(k,np.ones(len(k),bool),.05),0)
        k=np.r_[np.ones(20),-np.ones(12),np.ones(20)]
        self.assertEqual(inflections(k,np.ones(len(k),bool),.05),2)
        mask=np.ones(len(k),bool);mask[20:32]=False
        self.assertEqual(inflections(k,mask,.05),0)

    def test_missing_nonfinite_or_unmatched_lines_fail_closed(self):
        p=self.circle();ls=reference(p)
        for q in ([],[p[:-1]],[np.full_like(p,np.nan)]):
            with self.assertRaises(ValueError):ls.evaluate(q)
        with self.assertRaises(ValueError):LineSet().evaluate([])
        with self.assertRaises(ValueError):LineSet(ls.curves).evaluate([p])     # no plan

    def test_plan_rejects_invalid_limits(self):
        values=PLAN.to_dict()
        for key,bad in (('dk_max',0.),('energy_max',.9),('bilge_radius_min',1.5),('ratio',[1.5,2.]),('spacing_m',0.)):
            with self.assertRaises(ValueError):FairnessPlan.from_dict({**values,key:bad})
        self.assertEqual(FairnessPlan.from_dict(values),PLAN)

    def test_resampling_handles_duplicates_and_degeneracy(self):
        p=np.array([[0.,0,0],[0,0,0],[1,0,0]])
        self.assertGreater(len(resample(p)),10)
        with self.assertRaises(ValueError):resample(p[:2])

if __name__=='__main__':unittest.main()
