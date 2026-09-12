"""Q2 precision-first geometry study; public first observations select actions.

No HTTP, no real source/second observation enters selection. The grid is finite
and conservative response bounds do not imply global continuous optimality.
"""
from __future__ import annotations
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT/'vendor'))

import argparse
from concurrent.futures import ProcessPoolExecutor
import copy
import hashlib
import json
import math
import statistics
import time

from bsolver.geometry import (bearing_deg, distance, minimum_enclosing_circle,
    polygon_diameter, wrap_angle_deg, contains, max_distance)
from bsolver.knowledge import ChannelKnowledge
from bsolver.sensing import choose_measurement, direction_outcome_bound, candidate_points
from q2_geometry import generate_candidates, certify_joint_lens, radial_limit

EPSILON = 1.0051


def write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def freeze_sources():
    paths = [ROOT/'study.py', ROOT/'q2_geometry.py'] + sorted((ROOT/'vendor/bsolver').glob('*.py'))
    return {str(p.relative_to(ROOT)).replace('\\','/'): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def first_knowledge(position, report):
    k = ChannelKnowledge(1, 3, epsilon_deg=EPSILON)
    k.observe(tuple(position), {'measure_result':'direction', 'svd_deg':report})
    return k


def old_candidate_certificate(k, q):
    """Q_safe or a verified ray endpoint in Q_safe; alternative to the four lens."""
    first = k.first_direction.position
    lens = certify_joint_lens(q, first, k.first_direction.angle, k.epsilon_deg)
    if lens['certified']:
        return {'type':'four_disk_joint', **lens}
    maximum = max_distance(k.hull, q)
    if maximum <= 999.99999:
        return {'type':'outer_hull_Qsafe', 'certified':True, 'max_distance_m':maximum}
    dx, dy = q[0]-first[0], q[1]-first[1]
    vv = dx*dx+dy*dy
    if vv <= 0:
        return {'type':'first_station', 'certified':True}
    lo, hi = 1., math.inf
    for g in k.hull:
        wx, wy = g[0]-first[0], g[1]-first[1]
        projection = (wx*dx+wy*dy)/vv
        perpendicular2 = max(0., wx*wx+wy*wy-projection*projection*vv)
        if perpendicular2 > 999.99999**2:
            return {'certified':False}
        delta = math.sqrt((999.99999**2-perpendicular2)/vv)
        lo, hi = max(lo, projection-delta), min(hi, projection+delta)
        if lo > hi:
            return {'certified':False}
    endpoint = (first[0]+(lo+hi)/2*dx, first[1]+(lo+hi)/2*dy)
    return {'type':'first_visible_convexity', 'certified':max_distance(k.hull,endpoint)<=999.999991,
            'safe_endpoint':endpoint, 'lambda':2/(lo+hi)}


def select_from_first(position, report, *, angle_step=10., coarse_bin=1., fine_bin=.25):
    started = time.monotonic()
    k = first_knowledge(position, report)
    first = tuple(position)
    old = {}
    for mode in ('fixed','active'):
        point, _ = choose_measurement(k, first, mode, bin_width_deg=4.)
        if point is None:
            raise ValueError('no baseline point for this first observation')
        old[mode] = tuple(point)
    candidates = generate_candidates(first, report, EPSILON, angle_step_deg=angle_step,
                                     radial_fractions=(.4,.6,.8,1.))
    # Put exact movement budgets on the polar grid; fractions alone can miss
    # the useful 800m boundary simply because .8*t_max is slightly above 800.
    for index in range(-int((90-EPSILON)//angle_step),int((90-EPSILON)//angle_step)+1):
        relative=index*angle_step
        maximum=radial_limit(relative,EPSILON)
        angle=math.radians(report+relative)
        for budget in (400.,600.,800.):
            length=min(maximum-.02,budget)
            if length>=1:
                candidates.append((first[0]+length*math.cos(angle),first[1]+length*math.sin(angle)))
    candidates += list(old.values())
    candidates += [q for q, _ in candidate_points(k, first, 'active')]
    candidates = list(dict.fromkeys(tuple(q) for q in candidates))
    scores = []
    for point in candidates:
        cert = old_candidate_certificate(k, point)
        if not cert.get('certified'):
            continue
        radius = direction_outcome_bound(k.hull, point, EPSILON, coarse_bin)
        scores.append({'point':point, 'movement_m':distance(first,point),
                       'coarse_radius_upper_m':radius, 'safety':cert})
    # Fixed geometry-only selection. No experimental second responses are read.
    shortlisted = set(old.values())
    for budget in (400., 600., 800., math.inf):
        eligible = [r for r in scores if r['movement_m'] <= budget+1e-8]
        shortlisted.update(r['point'] for r in sorted(eligible, key=lambda r:r['coarse_radius_upper_m'])[:6])
    for row in scores:
        if row['point'] in shortlisted:
            row['fine_radius_upper_m'] = direction_outcome_bound(k.hull,row['point'],EPSILON,fine_bin)
    refined = [r for r in scores if 'fine_radius_upper_m' in r]
    def best(budget):
        return min((r for r in refined if r['movement_m']<=budget+1e-8),
                   key=lambda r:(r['fine_radius_upper_m'],r['movement_m']))
    selections = {name: next(r for r in refined if r['point']==point) for name,point in old.items()}
    selections['precision'] = best(math.inf)
    selections['precision_800'] = best(800.)
    frontier = [{'movement_budget_m':b, **best(b)} for b in (400.,600.,800.,1006.)
                if any(r['movement_m']<=b for r in refined)]
    return {'first_position':first, 'first_report_deg':report, 'epsilon_deg':EPSILON,
            'candidate_count':len(scores), 'refined_count':len(refined),
            'coarse_bin_deg':coarse_bin, 'fine_bin_deg':fine_bin,
            'selection_scope':'best refined conservative bound on a deterministic finite shortlist; not continuous minimax optimum',
            'selections':selections, 'frontier':frontier, 'candidates':scores,
            'selection_runtime_s':time.monotonic()-started}


def response(source, radius, position, error):
    d = distance(source,position)
    if d > radius+1e-8:
        return {'measure_result':'no_signal'}
    if d <= 5:
        return {'measure_result':'near'}
    # The attachment bounds the RETURNED angle's error, including its two
    # decimal representation. Move a rounded boundary report inward if needed.
    true_angle = bearing_deg(position,source)
    report = round((true_angle+error)%360,2)%360
    delta = wrap_angle_deg(report-true_angle)
    if abs(delta)>1.:
        report = round((report-math.copysign(.01,delta))%360,2)%360
    assert abs(wrap_angle_deg(report-true_angle))<=1.
    return {'measure_result':'direction','svd_deg':report}


def indistinguishable_pair(k, q):
    """A discrete physical witness: same two legal reports, different sources.

    This is a feasible-witness LOWER bound, not sampled worst-case certification.
    The first/second reports are actual multiples of .01 with physical error <=1.
    """
    first = k.first_direction
    states = []
    for grid_radius in [5.01]+list(range(25,1501,25)):
        radius = grid_radius-1e-5  # strictly inset radial endpoints
        for theta in (-.9999,-.5,0.,.5,.9999):
            angle = math.radians(first.angle+theta)
            g = first.position[0]+radius*math.cos(angle),first.position[1]+radius*math.sin(angle)
            r = 1500.  # one shared, legal radius for both observations
            if (math.hypot(*g)<=1800. and 5<distance(g,first.position)<=r
                    and abs(wrap_angle_deg(bearing_deg(first.position,g)-first.angle))<=1.
                    and 5<distance(g,q)<=r):
                states.append((g,r,bearing_deg(q,g)))
    best, witness = 0.,None
    for index,(g,r,a) in enumerate(states):
        for h,rh,b in states[index+1:]:
            delta = wrap_angle_deg(b-a)
            if abs(delta)>2.:
                continue
            z = round((a+delta/2)%360,2)%360
            if abs(wrap_angle_deg(z-a))>1. or abs(wrap_angle_deg(z-b))>1.:
                continue
            separation = distance(g,h)
            if separation>best:
                best = separation
                witness = {'sources':[g,h], 'radii':[r,rh], 'same_first_report':first.angle,
                           'same_second_report':z, 'source_separation_m':separation,
                           'mec_radius_lower_m':separation/2}
    return witness


def summarize(rows):
    result = {}
    for mode in sorted({r['mode'] for r in rows}):
        selected = [r for r in rows if r['mode']==mode]
        result[mode] = {'n':len(selected), 'no_signal':sum(r['second_response']=='no_signal' for r in selected),
                        'truth_retained':sum(r['truth_retained'] for r in selected),
                        'mean_movement_m':statistics.mean(r['movement_m'] for r in selected),
                        'mean_posterior_mec_radius_m':statistics.mean(r['posterior_mec_radius_m'] for r in selected),
                        'max_posterior_mec_radius_m':max(r['posterior_mec_radius_m'] for r in selected),
                        'mean_posterior_diameter_m':statistics.mean(r['posterior_diameter_m'] for r in selected),
                        'two_measurement_certified_fraction':statistics.mean(r['posterior_mec_radius_m']<=19.999 for r in selected)}
    return result


def _select_task(task):
    position,report,options=task
    return select_from_first(position,report,**options)


def bulk_select(contexts,options,workers):
    unique=list(dict.fromkeys((tuple(position),report) for position,report in contexts))
    result={}
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for index,selection in enumerate(executor.map(_select_task,[(p,r,options) for p,r in unique])):
            key=unique[index]
            result[key]=selection
            print(json.dumps({'phase':'first_only_geometry','done':index+1,'total':len(unique),
                              'runtime_s':round(selection['selection_runtime_s'],2)}),flush=True)
    return result


def synthetic(output, options,workers=2):
    scenarios, records, cache = [],[],{}
    templates = [((0.,0.),0.,(100.,300.,600.,1000.,1250.,1500.)),
                 ((1600.,0.),180.,(100.,300.,600.,1000.,1250.,1500.)),
                 ((2200.,0.),180.,(600.,1000.,1250.,1500.))]
    planned=bulk_select([(station,(angle+e)%360) for station,angle,_ in templates for e in (-1.,0.,1.)],options,workers)
    for station,true_angle,ranges in templates:
        for first_error in (-1.,0.,1.):
            first_report = (true_angle+first_error)%360
            key = f'{station}_{first_report}'
            selection = planned[(station,first_report)]
            cache[key] = selection
            print(json.dumps({'phase':'synthetic_geometry','first':key,
                              'runtime_s':round(selection['selection_runtime_s'],2),
                              'candidates':selection['candidate_count']}),flush=True)
            for r in ranges:
                # These templates are exactly horizontal. Avoid introducing a
                # tiny sin(pi) component at the strict 1500 m radial boundary.
                g = (station[0]+r*(1. if true_angle==0. else -1.),station[1])
                for error2 in (-1.,-.75,-.5,0.,.5,.75,1.):
                    case_id=f'synthetic-{len(scenarios):04d}'
                    scenarios.append({'id':case_id,'source':g,'radius':max(1000.,r),
                                      'first_position':station,'true_angle':true_angle,
                                      'first_error':first_error,'other_coordinate_error':error2})
                    for mode,selected in selection['selections'].items():
                        q = selected['point']
                        k = first_knowledge(station,first_report)
                        second = response(g,max(1000.,r),q,error2)
                        assert second['measure_result']!='no_signal'
                        k.observe(q,second)
                        assert contains(k.hull,g,tol=1e-4)
                        center,radius = minimum_enclosing_circle(k.hull)
                        if second['measure_result']=='near':
                            radius = 5.  # Exact reception-near disk bounds true feasible positions.
                        else:
                            assert radius<=selected['fine_radius_upper_m']+1e-5
                        records.append({'case_id':case_id,'mode':mode,'first_position':station,
                                        'source_distance_m':r,'first_error':first_error,'second_error':error2,
                                        'movement_m':selected['movement_m'],'point':q,
                                        'second_response':second['measure_result'],'truth_retained':True,
                                        'posterior_mec_radius_m':radius,'posterior_diameter_m':polygon_diameter(k.hull),
                                        'mec_center_error_m':distance(center,g),
                                        'certified_radius_upper_m':selected['fine_radius_upper_m']})
    frozen = {name:cache[next(iter(cache))]['selections'][name] for name in cache[next(iter(cache))]['selections']}
    # A source-pair lower bound and bin-width convergence check for canonical first=(0,0), report=0.
    canonical = cache['(0.0, 0.0)_0.0']
    k = first_knowledge((0.,0.),0.)
    convergence = {}
    for name,row in canonical['selections'].items():
        q = row['point']
        convergence[name] = {'point':q,'movement_m':row['movement_m'],
                             'bin_radius_upper_m':{str(width):direction_outcome_bound(k.hull,q,EPSILON,width)
                                                   for width in (4.,1.,.25,.0625)},
                             'physical_pair_lower_witness':indistinguishable_pair(k,q)}
    write(output/'synthetic_scenarios.json',scenarios)
    write(output/'synthetic_results.json',records)
    write(output/'synthetic_selections.json',cache)
    write(output/'bound_convergence.json',convergence)
    write(output/'synthetic_summary.json',{'distinct_worlds':len(scenarios),'rows':len(records),
          'public_first_contexts':len(cache),'interpretation':'deterministic boundary/sensitivity design; not official or an estimated source prior',
          'fixed_error_rule':'nominal error1 at the first coordinate, error2 elsewhere; two-decimal reports clamped inward to physical returned-error bound 1 degree; no resampling on repeated query',
          'summaries':summarize(records),
          'by_distance':{str(r):summarize([x for x in records if x['source_distance_m']==r])
                         for r in sorted({x['source_distance_m'] for x in records})}})


def official_contexts(output,path,options,workers=2):
    artifact = json.loads(Path(path).read_text(encoding='utf-8'))
    contexts = artifact['contexts']
    planned=bulk_select([(tuple(c['first']['position']),c['first']['report_deg']) for c in contexts],options,workers)
    rows, cache = [],{}
    for index,context in enumerate(contexts):
        first = context['first']
        station = tuple(first['position'])
        report = first['report_deg']
        key = str((station,report))
        if key not in cache:
            cache[key]=planned[(station,report)]
        selection = cache[key]
        # second was not provided to the selector. Evaluate observed geometry separately.
        actual = context.get('second')
        actual_geometry=None
        if actual is not None:
            k = first_knowledge(station,report)
            k.observe(tuple(actual['position']),{'measure_result':'direction','svd_deg':actual['report_deg']})
            actual_geometry={'posterior_outer_mec_radius_m':minimum_enclosing_circle(k.hull)[1],
                             'movement_between_two_positive_stations_m':distance(station,tuple(actual['position'])),
                             'warning':'actual survey second positive may follow intermediate no_signal; not Q2 policy evaluation'}
        rows.append({'context_id':context.get('context_id',index),'case_id':context['case_id'],
                     'first':first,'selection':selection['selections'], 'actual_observed_geometry':actual_geometry,
                     'counterfactual_second_response_available':False})
        if (index+1)%5==0:
            print(json.dumps({'phase':'official_first_context_geometry','done':index+1,'total':len(contexts)}),flush=True)
    write(output/'official_context_bounds.json',rows)
    write(output/'official_selection_records.json',cache)
    groups={}
    for mode in ('fixed','active','precision','precision_800'):
        # First average within case. Channels are not independent replications.
        case_ids=sorted({r['case_id'] for r in rows})
        per_case=[statistics.mean(r['selection'][mode]['fine_radius_upper_m'] for r in rows if r['case_id']==case)
                  for case in case_ids]
        groups[mode]={'case_units':len(case_ids),'contexts':len(rows),
                      'mean_per_case_certified_radius_upper_m':statistics.mean(per_case),
                      'max_context_certified_radius_upper_m':max(r['selection'][mode]['fine_radius_upper_m'] for r in rows)}
    write(output/'official_geometry_summary.json',{'summaries':groups,
          'scope':'offline conservative bounds on real public FIRST observations, not empirical performance at unvisited points',
          'no_ground_truth':True,'policy_selection_used_second':False,'new_official_calls':0})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--official-contexts',type=Path)
    parser.add_argument('--skip-synthetic',action='store_true')
    parser.add_argument('--angle-step',type=float,default=10.)
    parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error('use a fresh output directory')
    if not 1<=args.angle_step<=30:
        parser.error('angle-step must lie in [1,30]')
    if args.workers<1:
        parser.error('workers must be positive')
    sources=freeze_sources()
    write(args.output/'run_manifest.json',{'source_files':sources,'epsilon_deg':EPSILON,
          'selection':'precision-first bound / explicit 800m movement budget; no posterior target data in selection',
          'options':{'angle_step':args.angle_step,'coarse_bin':1.,'fine_bin':.25}})
    options={'angle_step':args.angle_step}
    start=time.monotonic()
    if not args.skip_synthetic:
        synthetic(args.output,options,args.workers)
    if args.official_contexts:
        official_contexts(args.output,args.official_contexts,options,args.workers)
    if freeze_sources()!=sources:
        raise RuntimeError('source changed during study')
    write(args.output/'completion.json',{'status':'complete','elapsed_s':time.monotonic()-start})


if __name__=='__main__':
    main()
