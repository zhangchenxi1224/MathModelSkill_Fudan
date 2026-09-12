"""Experimental first-observation-only search on the complete bounded domain.

DIRECT and DE are search baselines, not global-optimality certificates. Every
returned finalist is evaluated by the same three-feedback objective.
"""
from __future__ import annotations
import math
import time
import numpy as np
from scipy.optimize import direct,differential_evolution,minimize

from evaluator import evaluate


def compact(result):
    return {k:result[k] for k in ('point_local','point_global','lower_m','upper_m','gap_m',
        'gap_reached','elapsed_s','upper_status') if k in result}


def optimize(context,baseline_points,budget=100,seed=20260911):
    started=time.monotonic()
    rectangle=context.search_rectangle()
    bounds=[rectangle[:2],rectangle[2:]]
    cache={}; trace=[]

    def objective(x,method='search'):
        key=tuple(round(float(v),8) for v in x)
        if key not in cache:
            result=evaluate(context,key,tolerance=3.,max_splits=70,circle_tolerance=.01)
            cache[key]=result
            trace.append({'method':method,**compact(result)})
        return cache[key]['upper_m']

    seeds=list(baseline_points.values())+[(850.,600.),(880.,620.),(880.,-620.)]
    # These are initial guesses, not constraints on the global search domain.
    for angle in (25.,35.,45.,55.,65.):
        for radius in (1000.,1050.,1100.,1150.):
            a=math.radians(angle)
            seeds.append((radius*math.cos(a),radius*math.sin(a)))
            seeds.append((radius*math.cos(a),-radius*math.sin(a)))
    for q in seeds:
        if all(lo<=v<=hi for v,(lo,hi) in zip(q,bounds)): objective(q,'seed')
    initial_best=min(cache,key=lambda k:cache[k]['upper_m'])
    direct_result=direct(lambda x:objective(x,'DIRECT'),bounds,maxfun=budget,maxiter=budget,
                         locally_biased=False,f_min=-1.)
    local_result=minimize(lambda x:objective(x,'Nelder-Mead'),np.array(initial_best),
                          method='Nelder-Mead',bounds=bounds,
                          options={'maxfev':budget,'xatol':.2,'fatol':.15})
    rng=np.random.default_rng(seed)
    population=rng.uniform(np.array([a for a,b in bounds]),np.array([b for a,b in bounds]),size=(20,2))
    ranked=sorted(cache,key=lambda k:cache[k]['upper_m'])
    population[:8]=ranked[:8]
    de_result=differential_evolution(lambda x:objective(x,'DE'),bounds,init=population,
                    maxiter=max(1,budget//20-1),polish=False,rng=rng,workers=1)
    # Refine several coarse winners: a coarse upper bound alone never proves
    # an omitted candidate inferior. Selection scope is explicitly finite.
    ranked=sorted(cache,key=lambda k:cache[k]['upper_m'])
    finalists=[]
    for key in ranked:
        if all(math.dist(key,tuple(r['point_local']))>3 for r in finalists):
            finalists.append(evaluate(context,key,tolerance=.25,max_splits=350))
        if len(finalists)>=5: break
    best=min(finalists,key=lambda r:r['upper_m'])
    return {'best':best,'finalists':finalists,'trace':trace,'coarse_evaluations':len(cache),
            'search_rectangle_local':rectangle,'seed':seed,'per_method_budget':budget,
            'methods':{'DIRECT':{'success':bool(direct_result.success),'message':str(direct_result.message)},
                       'Nelder-Mead':{'success':bool(local_result.success),'message':str(local_result.message)},
                       'DE':{'success':bool(de_result.success),'message':str(de_result.message)}},
            'global_optimality_claim':False,'global_lower_m':0.,
            'global_lower_scope':'trivial nonnegative-radius bound; no spatial optimality certificate',
            'elapsed_s':time.monotonic()-started}
