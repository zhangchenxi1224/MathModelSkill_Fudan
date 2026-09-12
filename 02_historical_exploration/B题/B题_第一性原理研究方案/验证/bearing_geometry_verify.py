"""Independent geometric checks, not official simulator tests.

Run: python bearing_geometry_verify.py
Writes bearing_geometry_results.json beside this script.
"""
from pathlib import Path
import json
import math

ROOT = Path(__file__).resolve().parent
SIDE = 950.0
REGION_RADIUS = 1800.0
EPS_DEG = 1.005  # Physical 1 degree plus nearest rounding to 0.01 degree.


def point(ij):
    i, j = ij
    return (SIDE * (i + j / 2), SIDE * math.sqrt(3) * j / 2)


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def segment_distance_origin(a, b):
    v = sub(b, a)
    t = max(0.0, min(1.0, -sum(x * y for x, y in zip(a, v)) / sum(x * x for x in v)))
    return math.hypot(a[0] + t * v[0], a[1] + t * v[1])


def triangle_distance_origin(ijk):
    p = [point(t) for t in ijk]
    if all(cross(sub(p[(k + 1) % 3], p[k]), (-p[k][0], -p[k][1])) >= -1e-8 for k in range(3)):
        return 0.0
    return min(segment_distance_origin(p[k], p[(k + 1) % 3]) for k in range(3))


triangles = []
vertices = set()
for i in range(-6, 7):
    for j in range(-6, 7):
        pair = [((i, j), (i + 1, j), (i, j + 1)),
                ((i + 1, j), (i + 1, j + 1), (i, j + 1))]
        for triangle in pair:
            if triangle_distance_origin(triangle) <= REGION_RADIUS + 1e-8:
                triangles.append(triangle)
                vertices.update(triangle)

# An explicit path certificate, rather than an optimality claim.
path = [(0, 0), (-1, 0), (-2, 0), (-3, 1), (-3, 2), (-2, 1),
        (-2, 2), (-2, 3), (-1, 3), (-1, 2), (-1, 1), (0, 1),
        (0, 2), (1, 2), (2, 1), (1, 1), (1, 0), (2, 0),
        (3, -1), (3, -2), (2, -1), (1, -1), (2, -2), (2, -3),
        (1, -3), (1, -2), (0, -1), (0, -2), (-1, -2), (-2, -1), (-1, -1)]
assert len(triangles) == 42
assert len(vertices) == 31
assert len(set(path)) == len(path) == 31 and set(path) == vertices
assert path[0] == (0, 0)
lengths = [math.dist(point(a), point(b)) for a, b in zip(path, path[1:])]
assert all(abs(d - SIDE) < 1e-8 for d in lengths)

epsilon = math.radians(EPS_DEG)
contraction = 1 / (2 * math.cos(epsilon))
radii = [1500 * contraction**k for k in range(1, 8)]
local_oneway = sum(radii)
q3_local_return_time = 2 * local_oneway / 5 + 6 * 5 + 5
q3_total = 9000 / 5 + 7 * 119 + 16 * q3_local_return_time
q4_clear_route_with_return = 14 + 1512 + 28 + 1512 + 14
q4_clear_actions = 109 * 3 + 5
q4_one_source = q4_clear_route_with_return / 5 + q4_clear_actions
q4_total = sum(lengths) / 5 + 31 * 119 + 16 * q4_one_source
q4_tree_total = 60 * SIDE / 5 + 31 * 119 + 16 * q4_one_source

# Check a literal counterexample compatible with three 2-degree wedges.
triangle_side = 40.0
counterexample_triangle = [(0.0, 0.0), (40.0, 0.0), (20.0, 20 * math.sqrt(3))]
sensor_rows = []
for k in range(3):
    a, b = counterexample_triangle[k], counterexample_triangle[(k + 1) % 3]
    u = ((b[0] - a[0]) / triangle_side, (b[1] - a[1]) / triangle_side)
    sensor = (a[0] - 1000 * u[0], a[1] - 1000 * u[1])
    theta = math.atan2(u[1], u[0])
    lower = u
    upper = (math.cos(theta + math.radians(2)), math.sin(theta + math.radians(2)))
    assert all(cross(lower, sub(g, sensor)) >= -1e-8 and cross(upper, sub(g, sensor)) <= 1e-8 for g in counterexample_triangle)
    assert all(math.dist(g, sensor) <= 1500 for g in counterexample_triangle)
    sensor_rows.append({'position_m': sensor, 'bearing_deg': (math.degrees(theta) + 1) % 360})

results = {
    'status': 'Independent geometric certificates; no official simulator run or measured performance.',
    'epsilon_effective_deg': EPS_DEG,
    'rounding_note': '1.005 degrees assumes rounding to nearest 0.01 degree. Using 1.01 is conservative if rounding mode is unspecified; both retain the stated 3.54h and 6.83h rounded bounds.',
    'triangular_coverage': {
        'triangle_count': len(triangles), 'vertex_count': len(vertices),
        'max_vertex_radius_m': max(math.hypot(*point(v)) for v in vertices),
        'route_lattice_indices_ij': path,
        'route_coordinates_m': [point(v) for v in path],
        'route_each_edge_m': SIDE, 'route_total_m': sum(lengths),
        'triangles_lattice_indices_ij': triangles,
    },
    'q1_counterexample': {
        'triangle_vertices_m': counterexample_triangle,
        'diameter_m': 40.0, 'minimum_enclosing_radius_m': 40 / math.sqrt(3),
        'three_sensor_bearings': sensor_rows,
    },
    'q3_omnidirectional_bisection': {
        'contraction_factor': contraction, 'radii_after_moves_m': radii,
        'new_measurements_after_first_bearing': 6, 'clear_attempts': 1,
        'movement_oneway_bound_m': local_oneway,
        'movement_with_return_bound_m': 2 * local_oneway,
        'local_with_return_virtual_s': q3_local_return_time,
        'seven_point_scan_route_m': 9000,
        'total_measurements_bound': 7 * 20 + 16 * 6,
        'total_clear_attempts_bound': 16,
        'total_action_requests_including_enter_exit_bound': 7 * 20 + 16 * 7 + 2,
        'full_virtual_s_bound': q3_total, 'full_virtual_h_bound': q3_total / 3600,
    },
    'q4_directional_fallback': {
        'sector_halfwidth_m': 1500 * math.sin(epsilon),
        'clear_points_local_m': [(28 * i, y) for y in (14, -14) for i in (range(55) if y == 14 else range(54, -1, -1))],
        'clear_cover_radius_m': 14 * math.sqrt(2),
        'local_movement_with_return_m': q4_clear_route_with_return,
        'local_clear_action_s': q4_clear_actions,
        'local_with_return_virtual_s': q4_one_source,
        'full_virtual_s_bound': q4_total, 'full_virtual_h_bound': q4_total / 3600,
        'tree_route_virtual_s_bound': q4_tree_total, 'tree_route_virtual_h_bound': q4_tree_total / 3600,
        'total_action_requests_including_enter_exit_bound': 31 * 20 + 16 * 110 + 2,
    },
}
out = ROOT / 'bearing_geometry_results.json'
out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({
    'output_path': str(out),
    'triangle_count': len(triangles), 'vertex_count': len(vertices),
    'hamiltonian_path_m': sum(lengths),
    'epsilon_deg': EPS_DEG, 'q3_radii_m': radii,
    'q3_local_oneway_m': local_oneway,
    'q3_full_virtual_h': q3_total / 3600,
    'q4_full_virtual_h': q4_total / 3600,
    'q4_tree_full_virtual_h': q4_tree_total / 3600,
}, ensure_ascii=False, indent=2))
