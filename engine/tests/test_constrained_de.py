import numpy as np
from hullopt.optimize.constrained_de import deb_key,differential_evolution


def test_deb_prefers_feasibility_then_objective_and_normalized_violation():
    feasible={'feasible':True,'J':100.,'violation':0.}
    invalid={'feasible':False,'J':0.,'violation':.01}
    assert deb_key(feasible)<deb_key(invalid)
    assert deb_key(dict(invalid,violation=.001))<deb_key(invalid)
    assert deb_key(dict(feasible,J=1.))<deb_key(feasible)


def test_de_is_reproducible_bounded_and_solves_constraint():
    def evaluate(x):
        assert np.all(np.abs(x)<=1)
        violation=max(0.,.5-x.sum())/.5
        return {'feasible':violation<=1e-12,'violation':violation,'J':float(np.sum((x-[.2,.4])**2)),'x':x.copy()}
    results=[differential_evolution(evaluate,[[-1,1],[-1,1]],seeds=[[.8,.1],[0,0]],population_size=30,max_evaluations=400,rng_seed=17) for _ in range(2)]
    assert results[0]['evaluations']==400
    assert results[0]['best']['feasible']
    assert results[0]['best']['J']<.005
    np.testing.assert_array_equal(results[0]['best']['x'],results[1]['best']['x'])
