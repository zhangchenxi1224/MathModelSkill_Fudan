"""Implementation stress tests supplement the continuous proof, never replace it."""
from fractions import Fraction
import json
import math
import random
import unittest

from bsolver.coverage import directional_points, order_route, route_length
from bsolver.geometry import contains
from bsolver.protocol import RobotClient
from bsolver.refined_coverage import (
    COORDINATE_ERROR_BOUND_M, DOMAIN_RADIUS_M, OUTWARD_HALO_M, UNITS_PER_M,
    _construction, _float_points, _intersects_disk,
    refined_coverage_certificate, refined_directional_route,
)
from bsolver.simulator import LocalSimulator, Source
from bsolver.strategy import Solver, SolverConfig


def _hull(points):
    points=sorted(set(points))
    def turn(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def half(seq):
        h=[]
        for p in seq:
            while len(h)>1 and turn(h[-2],h[-1],p)<=0:h.pop()
            h.append(p)
        return h
    return half(points)[:-1]+half(reversed(points))[:-1]


class RefinedCoverageTests(unittest.TestCase):
    def test_frozen_candidate_has_fewer_stations_and_shorter_route(self):
        route=refined_directional_route()
        old=order_route(directional_points('triangular',950))
        cert=refined_coverage_certificate()
        self.assertEqual((len(route),cert['cell_count']),(25,33))
        self.assertEqual(len(old),31)
        self.assertLess(route_length(route),.81*route_length(old))
        self.assertAlmostEqual(route_length(route),24095.423821030687,places=6)
        self.assertLess(cert['reception_distance_upper_bound_m'],1000)
        self.assertGreater(cert['strict_projection_lower_bound_m'],.00499)
        self.assertLess(cert['route_minus_lower_bound_m'],.00001)
        self.assertLess(cert['all_20_channels_scan_time_upper_bound_s'],7820)
        self.assertFalse(cert['guarantees_localization'])

    def test_exact_closed_tangent_and_edge_intersections(self):
        u=UNITS_PER_M
        r=round((DOMAIN_RADIUS_M+OUTWARD_HALO_M)*u)
        tangent=((r,-u),(r+u,0),(r,u))
        self.assertTrue(all(x*x+y*y>r*r for x,y in tangent))
        self.assertTrue(_intersects_disk(tangent,r))
        self.assertFalse(_intersects_disk(tuple((x+1,y) for x,y in tangent),r))
        self.assertTrue(_intersects_disk(((r,0),(r+u,-u),(r+u,u)),r))
        self.assertTrue(_intersects_disk(((-u,-u),(u,-u),(0,u)),r))
        self.assertFalse(_intersects_disk(((r+1,0),(r+u,-u),(r+u,u)),r))

    def test_index_enumeration_and_complete_vertex_union(self):
        verts,cells,meta=_construction(995,3,18,24)
        side,height=meta['side_units'],meta['height_units']
        ox,oy=meta['offset_units']
        point=lambda p:(ox+side*p[0]+side//2*p[1],oy+height*p[1])
        self.assertEqual(set(verts),{point(p) for tri in cells for p in tri})
        larger=[]
        b=meta['index_bound']+4
        radius=round((DOMAIN_RADIUS_M+OUTWARD_HALO_M)*UNITS_PER_M)
        for i in range(-b,b+1):
            for j in range(-b,b+1):
                for tri in (((i,j),(i+1,j),(i,j+1)),((i+1,j),(i+1,j+1),(i,j+1))):
                    if _intersects_disk(tuple(point(p) for p in tri),radius):larger.append(tri)
        self.assertEqual(set(cells),set(larger))
        self.assertEqual(set(refined_directional_route()),set(_float_points(verts)))

    def test_float_coordinates_respect_declared_nominal_error(self):
        verts,_,_=_construction(995,3,18,24)
        for exact,submitted in zip(verts,_float_points(verts)):
            errors=[float(abs(Fraction(v)-Fraction(n,UNITS_PER_M))) for v,n in zip(submitted,exact)]
            self.assertLess(math.hypot(*errors),COORDINATE_ERROR_BOUND_M)
            self.assertLess(math.hypot(*submitted),3000)

    def test_boundary_edges_vertices_and_all_orientation_support(self):
        points=refined_directional_route()
        probes=[(0.,0.)]
        for degree in range(720):
            angle=math.radians(degree/2)
            for radius in (1800.,1799.99999999):
                probes.append((radius*math.cos(angle),radius*math.sin(angle)))
        inner=[p for p in points if math.hypot(*p)<=1800]
        probes+=inner
        probes += [((a[0]+b[0])/2,(a[1]+b[1])/2) for i,a in enumerate(inner) for b in inner[i+1:] if math.dist(a,b)<996]
        rng=random.Random(202609111801)
        for _ in range(300):
            r=1800*math.sqrt(rng.random());a=rng.random()*math.tau
            probes.append((r*math.cos(a),r*math.sin(a)))
        for source in probes:
            nearby=[(p[0]-source[0],p[1]-source[1]) for p in points if math.dist(p,source)<=1000]
            hull=_hull(nearby)
            self.assertGreaterEqual(len(hull),3,source)
            # Every outward unit normal's support is at least the nearest
            # convex-hull edge distance. This checks all orientations at each
            # finite probe, not merely sampled direction angles.
            margin=min((a[0]*b[1]-a[1]*b[0])/math.dist(a,b) for a,b in zip(hull,hull[1:]+hull[:1]))
            self.assertGreater(margin,.00499,source)

    def test_exact_boundary_vertex_keeps_outward_cells(self):
        points=_float_points(_construction(900,0,0,24)[0])
        self.assertIn((2700.,0.),points)
        self.assertTrue(any(x>1800 and math.dist((1800,0),(x,y))<1000 for x,y in points))

    def test_public_return_values_are_copies_and_json_serializable(self):
        original=refined_directional_route()
        changed=refined_directional_route();changed.pop()
        self.assertEqual(refined_directional_route(),original)
        cert=refined_coverage_certificate()
        self.assertEqual(json.loads(json.dumps(cert)),cert)
        cert['stations'].clear();cert['grid']['offset_units'][0]=0
        self.assertEqual(refined_directional_route(),original)
        self.assertEqual(refined_coverage_certificate()['point_count'],25)
        self.assertEqual(refined_coverage_certificate()['grid']['offset_units'][0],497500000)

    def test_invalid_grid_parameters_leave_no_ambiguous_margin(self):
        for spacing in (0,99,1000,999.999998,math.nan,math.inf):
            with self.assertRaises(ValueError):_construction(spacing,0,0,24)
        for u,v,den in ((-1,0,24),(24,0,24),(0,24,24),(0,0,0),(0,0,2.5)):
            with self.assertRaises(ValueError):_construction(995,u,v,den)

    def test_ledger_requires_every_new_station(self):
        client=RobotClient(transport=LocalSimulator([]),retry_delay=0)
        policy=Solver(client,SolverConfig(problem=4))
        policy.points=refined_directional_route()
        for k in policy.channels.values():k.coverage_indices.update(range(24))
        self.assertIsNone(policy._complete_evidence())
        for k in policy.channels.values():k.coverage_indices.add(24)
        evidence=policy._complete_evidence()
        self.assertEqual(evidence['required_point_count'],25)
        self.assertTrue(all(len(v)==25 for v in evidence['checked_indices'].values()))

    def _run_sources(self,sources):
        env=LocalSimulator(sources,seed=8611,error_mode='extreme',enforce_case_size=True)
        client=RobotClient(transport=env,retry_delay=0)
        policy=Solver(client,SolverConfig(problem=4,local_measure_limit=0,scheduling='joint'))
        policy.points=refined_directional_route()
        result=policy.run()
        self.assertEqual(result['status'],'complete',result.get('error'))
        self.assertTrue(env.summary()['all_cleared'])
        self.assertEqual(result['clear_successes'],len(sources))
        self.assertIsNone(result['source_total'])
        self.assertLess(result['total_virtual_time_s'],360000)
        truth={s.channel:s.position for s in sources}
        for decision in policy.decisions:
            k=decision.get('knowledge')
            if k and k['channel'] in truth:self.assertTrue(contains(k['hull'],truth[k['channel']],tol=1e-5))
        return result,policy

    def test_ten_minimum_radius_outward_boundary_sources_clear(self):
        sources=[]
        for j in range(10):
            angle=math.tau*j/10+.031
            sources.append(Source(j+1,(1800*math.cos(angle),1800*math.sin(angle)),1000,math.degrees(angle)))
        result,_=self._run_sources(sources)
        self.assertEqual(result['stop_evidence']['type'],'per_channel_coverage')
        self.assertEqual(result['stop_evidence']['required_point_count'],25)

    def test_hidden_outward_channel_does_not_disappear_after_easy_clears(self):
        sources=[Source(i,(0,0),1000) for i in range(1,10)]+[Source(20,(1800,0),1000,0)]
        result,policy=self._run_sources(sources)
        self.assertEqual(policy.channels[20].status,'cleared')
        self.assertEqual(result['stop_evidence']['required_point_count'],25)


if __name__=='__main__':
    unittest.main()
