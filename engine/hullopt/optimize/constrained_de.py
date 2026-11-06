"""Deterministic differential evolution with Deb feasibility-first selection.

Objectives and normalized constraint violations remain separate. This avoids
large discontinuous penalty values obscuring improvements near gate boundaries.
"""
import numpy as np


def deb_key(result):
    if result['feasible']:return (0,float(result['J']),0.)
    return (1,float(result['violation']),float(result.get('J',np.inf)))


def differential_evolution(evaluate,bounds,seeds=(),*,population_size=36,max_evaluations=2000,rng_seed=170026,callback=None,spread=.07):
    bounds=np.asarray(bounds,float)
    if bounds.ndim!=2 or bounds.shape[1]!=2 or np.any(bounds[:,1]<=bounds[:,0]):raise ValueError('Invalid DE bounds')
    if population_size<30 or max_evaluations<population_size:raise ValueError('Need population >=30 and a complete initial generation')
    lo,hi=bounds.T;span=hi-lo;dim=len(lo);rng=np.random.default_rng(rng_seed)
    pop=np.empty((population_size,dim));seeds=[np.clip((np.asarray(x)-lo)/span,0,1) for x in seeds]
    # Seeds come first. With seeds and a spread, each other member of the first generation is a
    # randomly chosen seed plus Gaussian noise (sigma = spread, as a fraction of each bound's span),
    # clipped to the bounds; without seeds, or with spread=None, the rest is uniform over the bounds.
    for i in range(population_size):
        if i<len(seeds):pop[i]=seeds[i]
        elif seeds and spread is not None:
            base=seeds[rng.integers(len(seeds))]
            pop[i]=np.clip(base+rng.normal(0,spread,dim),0,1)
        else:pop[i]=rng.random(dim)
    results=[];count=0;generation=0
    for x in pop:
        results.append(evaluate(lo+x*span));count+=1
    if callback:callback(generation,count,results)
    while count<max_evaluations:
        # Snapshot generation gives repeatable DE updates independent of
        # candidate runtime or thread ordering.
        old=pop.copy();rank=sorted(range(population_size),key=lambda i:deb_key(results[i]))
        for i in range(population_size):
            if count>=max_evaluations:break
            pool=[j for j in range(population_size) if j!=i]
            r1,r2,r3=rng.choice(pool,3,replace=False);F=rng.uniform(.35,.9)
            if rng.random()<.7:
                pbest=rank[int(rng.integers(max(2,population_size//5)))]
                mutant=old[i]+F*(old[pbest]-old[i])+F*(old[r1]-old[r2])
            else:mutant=old[r1]+F*(old[r2]-old[r3])
            mask=rng.random(dim)<rng.uniform(.5,.95);mask[rng.integers(dim)]=True
            trial=np.where(mask,mutant,old[i])
            trial=np.where(trial<0,-trial,trial);trial=np.where(trial>1,2-trial,trial);trial=np.clip(trial,0,1)
            result=evaluate(lo+trial*span);count+=1
            if deb_key(result)<deb_key(results[i]):pop[i]=trial;results[i]=result
        generation+=1
        if callback:callback(generation,count,results)
    best=min(results,key=deb_key)
    return {'best':best,'evaluations':count,'generations':generation,'population_results':results,
            'population_vectors':lo+pop*span,'rng_seed':rng_seed,'method':'DE current-to-pbest/1 + rand/1/bin; Deb feasibility-first'}
