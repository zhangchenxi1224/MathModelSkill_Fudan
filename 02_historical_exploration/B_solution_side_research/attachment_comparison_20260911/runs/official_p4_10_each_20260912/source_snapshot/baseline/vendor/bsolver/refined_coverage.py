"""Static directional discovery cover with exact closed-cell selection.

This module never reads a scenario or observations. See docs/refined_coverage.md.
The original coverage and policy implementations remain unchanged.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import math

from .coverage import order_route, route_length

Point = tuple[float, float]
IntPoint = tuple[int, int]
UNITS_PER_M = 1_000_000
DOMAIN_RADIUS_M = 1800.0
MIN_RECEPTION_RADIUS_M = 1000.0
OUTWARD_HALO_M = 0.01
COORDINATE_ERROR_BOUND_M = 1e-8


def _round_ratio(numerator: int, denominator: int) -> int:
    """Round a rational coordinate to the nearest integer micro-meter."""
    sign = -1 if numerator < 0 else 1
    return sign * ((abs(numerator) + denominator // 2) // denominator)


def _intersects_disk(triangle: tuple[IntPoint, IntPoint, IntPoint], radius: int) -> bool:
    """Exact intersection of a closed integer-coordinate CCW triangle and disk."""
    xmin = min(p[0] for p in triangle)
    xmax = max(p[0] for p in triangle)
    ymin = min(p[1] for p in triangle)
    ymax = max(p[1] for p in triangle)
    dx, dy = max(xmin, 0, -xmax), max(ymin, 0, -ymax)
    radius_sq = radius * radius
    if dx * dx + dy * dy > radius_sq:
        return False
    edges = tuple(zip(triangle, triangle[1:] + triangle[:1]))
    if all((b[0]-a[0])*(-a[1])-(b[1]-a[1])*(-a[0]) >= 0 for a, b in edges):
        return True
    for a, b in edges:
        ex, ey = b[0]-a[0], b[1]-a[1]
        denom = ex*ex + ey*ey
        projection = -(a[0]*ex + a[1]*ey)
        if projection <= 0:
            if a[0]*a[0]+a[1]*a[1] <= radius_sq:
                return True
        elif projection >= denom:
            if b[0]*b[0]+b[1]*b[1] <= radius_sq:
                return True
        else:
            cross = a[0]*b[1]-a[1]*b[0]
            if cross*cross <= radius_sq*denom:
                return True
    return False


def _construction(spacing_m: float, phase_u: int, phase_v: int, phase_denominator: int):
    """Return all selected micro-meter vertices and triangle indices.

    The near-equilateral lattice is exact in integer coordinates. Its height
    is rounded once; all shared vertices thereafter use integer arithmetic.
    """
    if not math.isfinite(spacing_m) or not 100 <= spacing_m < 1000:
        raise ValueError("spacing_m must be finite and in [100,1000)")
    if not isinstance(phase_denominator,int) or phase_denominator <= 0:
        raise ValueError("phase_denominator must be a positive integer")
    if not all(isinstance(v,int) and 0 <= v < phase_denominator for v in (phase_u,phase_v)):
        raise ValueError("phase indices must be integers in [0, denominator)")
    side = round(spacing_m * UNITS_PER_M)
    if side % 2:
        raise ValueError("spacing requires an even integer micro-meter count")
    height = round(side * math.sqrt(3) / 2)
    edge_sq=max(side*side,(side//2)**2+height*height)
    conservative_limit=round((MIN_RECEPTION_RADIUS_M-OUTWARD_HALO_M)*UNITS_PER_M)
    if edge_sq >= conservative_limit*conservative_limit:
        raise ValueError("Rounded cell diameter leaves insufficient reception margin")
    offset = (_round_ratio(side*(2*phase_u+phase_v),2*phase_denominator),
              _round_ratio(height*phase_v,phase_denominator))
    radius = round((DOMAIN_RADIUS_M+OUTWARD_HALO_M)*UNITS_PER_M)
    # |j| <= (R+d+|offset_y|)/height; then |i| <=
    # (R+d+|offset_x|)/side + |j|/2. This intentionally wider common bound
    # includes each potentially intersecting closed triangle and its halo.
    bound = math.ceil(2*(DOMAIN_RADIUS_M+OUTWARD_HALO_M)/spacing_m)+5
    def vertex(index):
        i,j=index
        return offset[0]+side*i+(side//2)*j, offset[1]+height*j
    indices=set()
    cells=[]
    for i in range(-bound,bound+1):
        for j in range(-bound,bound+1):
            a,b,c,d=(i,j),(i+1,j),(i,j+1),(i+1,j+1)
            for tri in ((a,b,c),(b,d,c)):
                if _intersects_disk(tuple(vertex(p) for p in tri),radius):
                    cells.append(tri)
                    indices.update(tri)
    vertices=tuple(sorted(vertex(p) for p in indices))
    return vertices,tuple(cells),{"side_units":side,"height_units":height,"offset_units":offset,"index_bound":bound,"phase_u":phase_u,"phase_v":phase_v,"phase_denominator":phase_denominator}


def _float_points(vertices):
    return [(x/UNITS_PER_M,y/UNITS_PER_M) for x,y in vertices]


# Frozen after a geometry-only finite design search; no case is involved.
_SPACING_M = 995.0
_PHASE_U = 3
_PHASE_V = 18
_PHASE_DENOMINATOR = 24


@lru_cache(maxsize=1)
def _refined_construction():
    vertices,cells,metadata=_construction(_SPACING_M,_PHASE_U,_PHASE_V,_PHASE_DENOMINATOR)
    points=_float_points(vertices)
    route=tuple(order_route(points))
    return route,vertices,cells,metadata


def refined_directional_route() -> list[Point]:
    """Return a fresh copy of every retained station, in deterministic order."""
    return list(_refined_construction()[0])


def refined_coverage_certificate() -> dict:
    """Serializable analytic construction metadata, not a sampled certificate."""
    route,vertices,cells,meta=_refined_construction()
    diameter=max(meta['side_units'],math.hypot(meta['side_units']/2,meta['height_units']))/UNITS_PER_M
    distance=route_length(route)
    nearest=min(math.hypot(*p) for p in route)
    separation=min(math.dist(p,q) for i,p in enumerate(route) for q in route[i+1:])
    lower_bound=max(0.0,nearest-COORDINATE_ERROR_BOUND_M)+(len(route)-1)*max(0.0,separation-COORDINATE_ERROR_BOUND_M)
    route_json=json.dumps(route,separators=(',',':'),ensure_ascii=True)
    return {"certificate_type":"exact_closed_cell_selection_with_analytic_directional_cover",
            "proof_path":"docs/refined_coverage.md","kind":"refined_directional_triangular",
            "domain_radius_m":DOMAIN_RADIUS_M,"minimum_reception_radius_m":MIN_RECEPTION_RADIUS_M,
            "outward_halo_m":OUTWARD_HALO_M,"coordinate_error_bound_m":COORDINATE_ERROR_BOUND_M,
            "max_cell_diameter_m":diameter,"point_count":len(route),"cell_count":len(cells),
            "strict_projection_lower_bound_m":OUTWARD_HALO_M/2-COORDINATE_ERROR_BOUND_M,
            "reception_distance_upper_bound_m":diameter+OUTWARD_HALO_M/2+COORDINATE_ERROR_BOUND_M,
            "open_route_length_m":distance,"integer_units_per_m":UNITS_PER_M,
            "fixed_station_route_lower_bound_m":lower_bound,
            "route_minus_lower_bound_m":distance-lower_bound,
            "all_20_channels_scan_time_upper_bound_s":distance/5+120*len(route),
            "scan_bound_scope":"Move at 5m/s; every station scans20 channels at5s plus <=1s switch each. Excludes localization and clearance detours.",
            "guarantees_localization":False,"requires_scan_of_each_unresolved_channel":True,
            "includes_all_incident_cells_at_edges_and_vertices":True,"scenario_independent":True,
            "exact_intersection_predicates":True,"coordinate_magnitude_upper_bound_m":3000,
            "ordered_station_sha256":hashlib.sha256(route_json.encode('ascii')).hexdigest(),
            "stations":[list(p) for p in route],
            "grid":{**meta,"offset_units":list(meta['offset_units'])},
            "finite_search":{
                "spacing_values_m":[950,960,970,980,990,995,999,900,925,940,945,985,991,992,993,994,996,997,998],
                "phase_denominator":24,"phase_pairs_per_spacing":576,"constructed_grids":10944,
                "route_method":"frozen order_route: nearest-neighbor then open2-opt, fixed origin",
                "rotation_scope":"Global rotations preserve this circular domain, origin and route cost; orientation0 used without losing any rotated copy of an ordered route.",
                "selection_scope":"Geometry-only engineering choice among reviewed routes, comparing length/5+120*station_count. Not a proof of minimum for this cost across all10944 grids, all translations, all routes or all possible covers.",
                "route_pruning":"Only routes with (n-1)*(spacing-1e-6)+nearest_origin <= current shortest route were ordered during the search. This is valid for pure length, not a complete search for the later scan-time objective."
            }}
