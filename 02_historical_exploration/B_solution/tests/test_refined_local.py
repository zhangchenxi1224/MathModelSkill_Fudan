"""Geometric safety, finite control flow and public-transport integration tests."""
import ast
import copy
from fractions import Fraction
import math
from pathlib import Path
import random
import unittest
from unittest.mock import patch

from bsolver.geometry import contains, distance
from bsolver.knowledge import ChannelKnowledge
from bsolver.nosignal_sensing import NoSignalSolver
from bsolver.protocol import RobotClient
from bsolver.refined_local import (RefinedLocalConfig, RefinedLocalSolver,
    hull_cell_cover, measurement_cost_decision, remaining_cost_bound)
from bsolver.simulator import LocalSimulator, Source, generate_sources
from bsolver.strategy import SolverConfig


def knowledge_fixture(problem=4):
    k = ChannelKnowledge(1, problem)
    k.observe((0., 0.), {"measure_result": "direction", "svd_deg": 0.})
    return k


def assert_cover(test, hull, points, start=(0., 0.), failed=()):
    plan = hull_cell_cover(hull, start, failed_positions=failed)
    for point in points:
        if all(distance(point, f) > 20 for f in failed):
            test.assertTrue(plan.cells)
            test.assertLess(min(distance(point, c.center) for c in plan.cells), 20.)
    return plan


class RefinedCoverTests(unittest.TestCase):
    def test_closed_cell_edges_and_vertices_are_covered(self):
        hull = [(0., 0.), (84., 0.), (84., 56.), (0., 56.)]
        points = [(x, y) for x in (0., 28., 56., 84.) for y in (0., 28., 56.)]
        points += [(28.-1e-10, 28.+1e-10), (28.+1e-10, 28.-1e-10)]
        assert_cover(self, hull, points)

    def test_point_segment_and_clockwise_input(self):
        assert_cover(self, [(28., 28.)], [(28., 28.)])
        assert_cover(self, [(-100., -100.), (100., 100.)],
                     [(x, x) for x in range(-100, 101)])
        square = [(-30., -30.), (-30., 30.), (30., 30.), (30., -30.)]
        assert_cover(self, square, square+[(0., 0.)])

    def test_rotated_long_thin_hulls_include_all_boundary_points(self):
        for angle in (0., 1e-10, 45., 89.999999, 137., 270.):
            a = math.radians(angle)
            u, v = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
            def p(x, y):
                return (311.+x*u[0]+y*v[0], -271.+x*u[1]+y*v[1])
            hull = [p(0., -20.), p(1400., -20.), p(1400., 20.), p(0., 20.)]
            probes = [p(x, y) for x in range(0, 1401, 20) for y in (-20., 0., 20.)]
            with self.subTest(angle=angle):
                plan = assert_cover(self, hull, probes, start=(1000., 1000.))
                self.assertLess(len(plan.cells), 170)

    def test_convex_interiors_are_covered_not_just_vertices_or_an_inner_circle(self):
        rng = random.Random(92117001)
        hull = [(-80., -10.), (65., -55.), (125., 60.), (-20., 95.)]
        probes = list(hull)
        for _ in range(800):
            weights = [rng.random() for _ in hull]
            total = sum(weights)
            probes.append(tuple(sum(w*p[k] for w, p in zip(weights, hull))/total for k in (0, 1)))
        assert_cover(self, hull, probes)

    def test_each_padded_support_is_inside_actual_submitted_center_radius(self):
        plan = hull_cell_cover(knowledge_fixture().hull, (200., 80.))
        for cell in plan.cells:
            center = tuple(Fraction(x) for x in cell.center)
            for vertex in cell.support:
                exact_squared = sum((vertex[k]-center[k])**2 for k in (0, 1))
                self.assertLessEqual(exact_squared, Fraction('19.999')**2)

    def test_failed_clear_exclusion_never_subtracts_an_uncovered_hole_as_a_halfplane(self):
        hull = [(0., 0.), (56., 0.), (56., 28.), (0., 28.)]
        points = [(float(x), float(y)) for x in range(57) for y in range(29)]
        plan = assert_cover(self, hull, points, failed=((14., 14.),))
        self.assertGreater(plan.excluded_cells, 0)
        self.assertTrue(any(distance((55., 27.), cell.center) < 20 for cell in plan.cells))

    def test_failure_disk_boundary_is_not_rounded_inward(self):
        outside = (20.+1e-10, 0.)
        plan = hull_cell_cover([outside], (0., 0.), failed_positions=((0., 0.),))
        self.assertTrue(plan.cells)
        inside = hull_cell_cover([(20.-2e-6, 0.)], (0., 0.), failed_positions=((0., 0.),))
        self.assertFalse(inside.cells)

    def test_cost_bound_covers_actual_first_success_with_failure_skips(self):
        hull = [(0., 0.), (140., 0.), (140., 45.), (0., 45.)]
        start = (-100., 110.)
        plan = hull_cell_cover(hull, start)
        for target in [(0., 0.), (140., 45.), (75., 33.), (28., 28.)]:
            position, cost, failures = start, 0., []
            for cell in plan.cells:
                if cell.excluded_by_failures(failures):
                    continue
                cost += distance(position, cell.center)/5+3
                position = cell.center
                if distance(target, position) <= 20:
                    cost += 2
                    break
                failures.append(position)
            else:
                self.fail('a feasible target was skipped by failure filtering')
            self.assertLessEqual(cost, plan.cost_bound_s+1e-8)

    def test_nearest_safe_single_clear_is_retained(self):
        hull = [(90., -3.), (100., -3.), (100., 3.), (90., 3.)]
        cost, detail = remaining_cost_bound(hull, (0., 0.))
        self.assertEqual(detail['kind'], 'certified_clear')
        self.assertLess(cost, distance((0., 0.), (95., 0.))/5+5.)
        self.assertLessEqual(max(distance(p, detail['point']) for p in hull), 20.-1e-5)

    def test_optical_bound_includes_per_move_microsecond_quantization(self):
        hull = [(0., 0.), (140., 0.), (140., 56.), (0., 56.)]
        start = (.12123, -.127861)
        plan = hull_cell_cover(hull, start)
        positions = [start]+[c.center for c in plan.cells]
        quantized = sum(round(distance(a, b)/5*1e6)/1e6
                        for a, b in zip(positions, positions[1:]))+3*len(plan.cells)+2
        self.assertLessEqual(quantized, plan.cost_bound_s)
        self.assertEqual(plan.summary()['timing_quantization_guard_s'], len(plan.cells)*1e-6)

    def test_invalid_geometry_and_configuration_fail_closed(self):
        for hull in ([], [(float('nan'), 0.)], [(0., float('inf'))]):
            with self.assertRaises(ValueError):
                hull_cell_cover(hull, (0., 0.))
        for kwargs in ({'cell_side_m': 28.4}, {'max_extra_measures': 6},
                       {'max_extra_measures': True}, {'direction_bin_width_deg': 0},
                       {'min_bound_gain_s': -1}):
            with self.assertRaises(ValueError):
                RefinedLocalConfig(**kwargs)


class RefinedAdaptiveTests(unittest.TestCase):
    def test_uncertain_visibility_cannot_exploit_incomparable_route_bound_slack(self):
        k = knowledge_fixture(4)
        q, selector = (100., 100.), {'selection_changed_from_baseline': False}
        # Even an artificially attractive bound at q must not manufacture VOI.
        with patch('bsolver.refined_local.choose_measurement_nosignal', return_value=(q, selector)), \
             patch('bsolver.refined_local.remaining_cost_bound', side_effect=[(1000., {}), (1., {})]):
            chosen, decision = measurement_cost_decision(k, (0., 0.), 2,
                                                       solver_config=SolverConfig(problem=4))
        self.assertIsNone(chosen)
        self.assertFalse(decision['take_measurement'])
        self.assertEqual(decision['reason'], 'visibility_not_certified_no_information_value_claim')
        self.assertEqual(decision['measurement_cost_terms_s']['switch'], 1)
        self.assertAlmostEqual(decision['measurement_cost_s'], math.sqrt(20000.)/5+6)

    def test_visible_branch_costs_include_measure_movement_switch_and_near_clear(self):
        k = knowledge_fixture(4)
        k.positive_positions = [(-200., 0.), (200., 0.)]
        k.hull = [(-10., 100.), (10., 100.), (10., 400.), (-10., 400.)]
        selector = {'selection_changed_from_baseline': False}
        def bound(hull, start, **kwargs):
            return (180. if start == (-200., 0.) else 20.), {'kind': 'test_bound'}
        with patch('bsolver.refined_local.choose_measurement_nosignal', return_value=((0., 0.), selector)), \
             patch('bsolver.refined_local.remaining_cost_bound', side_effect=bound):
            q, decision = measurement_cost_decision(k, (-200., 0.), 2,
                                                    solver_config=SolverConfig(problem=4))
        self.assertEqual(q, (0., 0.))
        self.assertEqual(decision['measurement_cost_s'], 46.)
        self.assertEqual(decision['measure_then_continue_upper_bound_s'], 66.000001)
        self.assertEqual(decision['branch_cost_upper_bounds_s']['near'], 5.)
        self.assertIsNone(decision['branch_cost_upper_bounds_s']['no_signal'])
        self.assertEqual(decision['direction_full_circle_bins'], 12)
        self.assertEqual(decision['no_signal_impossible_certificate'], 'convex_hull_of_positive_stations')

    def test_adaptive_rejects_non_improving_bounds_even_with_visibility(self):
        k = knowledge_fixture(3)
        selector = {'selection_changed_from_baseline': False}
        with patch('bsolver.refined_local.choose_measurement_nosignal', return_value=((750., 100.), selector)), \
             patch('bsolver.refined_local.remaining_cost_bound', return_value=(10., {'kind': 'test_bound'})):
            q, info = measurement_cost_decision(k, (0., 0.), 1,
                                                solver_config=SolverConfig(problem=3))
        self.assertIsNone(q)
        self.assertEqual(info['reason'], 'no_strict_upper_bound_reduction')

    def test_cover_and_adaptive_preserve_observation_ledger_during_planning(self):
        k = knowledge_fixture(4)
        before = copy.deepcopy(k)
        hull_cell_cover(k.hull, (0., 0.), failed_positions=k.failed_clear_positions)
        measurement_cost_decision(k, (0., 0.), 1, solver_config=SolverConfig(problem=4))
        self.assertEqual(k, before)

    def test_five_total_local_measurements_is_a_hard_limit(self):
        class DummyClient:
            position = (0., 0.)
            current_channel = 1
            virtual_time = 0.
        solver = RefinedLocalSolver(DummyClient(), SolverConfig(problem=4, local_measure_limit=1), mode='adaptive')
        solver.channels[1] = knowledge_fixture(4)
        calls = []
        info = {'selection_changed_from_baseline': False}
        with patch.object(solver, '_try_certified_clear', return_value=False), \
             patch.object(solver, '_measure', side_effect=lambda *args: calls.append(args)), \
             patch.object(solver, '_clear', return_value=True), \
             patch('bsolver.refined_local.choose_measurement_nosignal', return_value=((700., 400.), info)), \
             patch('bsolver.refined_local.measurement_cost_decision', return_value=((700., 400.), {'selector': info})):
            solver._localize(1)
        self.assertEqual(len(calls), 5)
        self.assertEqual(solver.stats['refined_extra_measures'], 4)


class RefinedIntegrationTests(unittest.TestCase):
    def test_cover_uses_the_same_first_local_measurement_as_frozen_nosignal(self):
        selections = []
        for cls in (NoSignalSolver, RefinedLocalSolver):
            env = LocalSimulator([Source(1, (1250., 10.), 1500., 180.)], error_mode='positive')
            client = RobotClient(transport=lambda path, body: env(path, body), retry_delay=0)
            solver = cls(client, SolverConfig(problem=4, local_measure_limit=1))
            client.enter()
            solver._measure(1, (0., 0.), 'test_initial_observation')
            solver._localize(1)
            selections.append([e['selection'] for e in solver.decisions if e['event'] == 'choose_measurement'][0])
            self.assertTrue(env.summary()['all_cleared'])
            client.exit()
        self.assertEqual(selections[0], selections[1])

    def test_end_to_end_full_clear_in_extreme_and_boundary_directional_worlds(self):
        cases = [(generate_sources(92117, 10, directional_fraction=.5), 'extreme', 'cover')]
        boundary = [Source(i+1, (1800.*math.cos(i*.6283+.071), 1800.*math.sin(i*.6283+.071)),
                           1000., math.degrees(i*.6283+.071)) for i in range(10)]
        cases.append((boundary, 'correlated', 'adaptive'))
        for sources, error_mode, mode in cases:
            with self.subTest(mode=mode):
                env = LocalSimulator(sources, seed=381, error_mode=error_mode, enforce_case_size=True)
                client = RobotClient(transport=lambda path, body: env(path, body), retry_delay=0)
                solver = RefinedLocalSolver(client, SolverConfig(problem=4, local_measure_limit=1), mode=mode)
                result = solver.run()
                self.assertEqual(result['status'], 'complete', result['error'])
                self.assertTrue(env.summary()['all_cleared'])
                self.assertEqual(result['clear_successes'], 10)
                self.assertLess(abs(result['timing_residual_s']), .002)
                self.assertEqual(result['local_refinement_mode'], mode)
                truth = {s.channel: s.position for s in sources}
                for event in solver.decisions:
                    state = event.get('knowledge')
                    if state and state['channel'] in truth:
                        self.assertTrue(contains(state['hull'], truth[state['channel']], tol=1e-5))
                    if event['event'] == 'clear' and event['certificate']['type'] in ('near', 'outer_hull'):
                        self.assertLessEqual(distance(event['position'], truth[state['channel']]), 20.)
                self.assertIsNotNone(result['stop_evidence'])

    def test_no_simulator_or_hidden_state_import_and_only_client_public_actions(self):
        path = Path(__file__).resolve().parents[1]/'src/bsolver/refined_local.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertFalse(any(name and any(token in name for token in ('simulator', 'experiments')) for name in imports))
        forbidden = {'__sources', '_LocalSimulator__sources', '__field', '_LocalSimulator__field', 'summary'}
        self.assertFalse(any(isinstance(n, ast.Attribute) and n.attr in forbidden-{'summary'} for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main()
