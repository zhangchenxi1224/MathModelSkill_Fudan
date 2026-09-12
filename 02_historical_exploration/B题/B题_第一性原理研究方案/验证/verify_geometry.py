"""Independent geometry checks; no official simulator calls or official scores.

Run with Python 3 and numpy/matplotlib. Output remains next to this script.
The Q2 sweep is a finite illustrative sample, not a continuous minimax proof.
"""
from pathlib import Path
from itertools import combinations, product
import json
import math
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parent
EPS = math.radians(1.005)  # conservatively includes 0.005 degree rounding
WIDTH = 1500 * math.sin(EPS)


def clip(poly, n, c):
    """Clip a convex polygon by n dot x >= c."""
    ans = []
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        da, db = np.dot(n, a)-c, np.dot(n, b)-c
        ina, inb = da >= -1e-9, db >= -1e-9
        if ina:
            ans.append(a)
        if ina != inb:
            ans.append(a + da / (da-db) * (b-a))
    return np.asarray(ans, dtype=float).reshape(-1, 2)


def wedge(poly, s, theta):
    for th, sign in [(theta-EPS, 1), (theta+EPS, -1)]:
        n = sign * np.array([-math.sin(th), math.cos(th)])
        poly = clip(poly, n, np.dot(n, s))
    return poly


def mec(poly):
    """Exact candidate enumeration for the small polygons in this check."""
    candidates = [(p, 0.) for p in poly]
    for a, b in combinations(poly, 2):
        candidates.append(((a+b)/2, float(np.linalg.norm(a-b)/2)))
    for a, b, c in combinations(poly, 3):
        mat = 2 * np.array([b-a, c-a])
        if abs(np.linalg.det(mat)) < 1e-10:
            continue
        center = np.linalg.solve(mat, [np.dot(b, b)-np.dot(a, a), np.dot(c, c)-np.dot(a, a)])
        candidates.append((center, float(np.linalg.norm(center-a))))
    for center, r in sorted(candidates, key=lambda t: t[1]):
        if np.max(np.linalg.norm(poly-center, axis=1)) <= r+1e-7:
            return center, r
    raise AssertionError('No enclosing circle')


def initial_polygon():
    rect = np.array([[0., -WIDTH], [1500., -WIDTH], [1500., WIDTH], [0., WIDTH]])
    return wedge(rect, np.zeros(2), 0.)


def second_point_check():
    # Observed first bearing is 0 degrees. Truth and error both obey bounds.
    initial = initial_polygon()
    rows = []
    for a, b in [(550,150), (650,250), (750,400), (750,500), (750,600), (900,250)]:
        q = np.array([a, b], dtype=float)
        max_r = -1
        witness = None
        counts = 0
        for distance, true_angle, error2 in product([100,400,800,1200,1500], [-1,0,1], [-1,0,1]):
            th = math.radians(true_angle)
            g = distance * np.array([math.cos(th), math.sin(th)])
            theta2 = math.atan2(*(g-q)[::-1]) + math.radians(error2)
            p2 = wedge(initial, q, theta2)
            assert len(p2) >= 3
            center, radius = mec(p2)
            assert np.linalg.norm(g-center) <= radius+1e-7
            # New wedge cannot increase the feasible set's enclosing radius.
            assert radius <= mec(initial)[1]+1e-6
            counts += 1
            if radius > max_r:
                max_r = radius
                witness = dict(range_m=distance, true_first_bearing_deg=true_angle, second_error_deg=error2)
        safe_bound = math.sqrt(max(a*a, (1500-a)**2)+(abs(b)+WIDTH)**2)
        assert safe_bound < 1000
        rows.append(dict(a_m=a, b_m=b, safe_distance_bound_m=safe_bound,
                         travel_time_s=float(np.linalg.norm(q)/5),
                         sampled_max_enclosing_radius_m=max_r,
                         samples=counts, witness=witness))
    return rows


def main():
    triangle = np.array([[0.,0.],[40.,0.],[20.,20*math.sqrt(3)]])
    center, radius = mec(triangle)
    assert abs(radius-40/math.sqrt(3)) < 1e-9
    rows = second_point_check()
    band_bound = math.sqrt(800**2+(500+WIDTH)**2)
    omni_radii = [1000, 1800]
    omni_bounds = [math.sqrt(r*r+1500**2-3000*r*math.cos(math.pi/6)) for r in omni_radii]
    assert max(omni_bounds) < 1000
    # Same explicit 110-point construction as the route certificate:
    # x = 0, 28, ..., 1512; y = +/-14. Covers [0,1500] x [-WIDTH,WIDTH].
    assert WIDTH < 28
    clearance_cover_radius = 14 * math.sqrt(2)
    assert clearance_cover_radius < 20
    data = dict(description='Analytic checks and finite synthetic Q2 geometry sweep; not official simulator results.',
                epsilon_used_deg=1.005, q1_equilateral_diameter_m=40, q1_enclosing_radius_m=radius,
                q2_band_max_distance_m=band_bound, q2_sweep=rows,
                omni_7point_endpoint_bounds_m=omni_bounds,
                rectangle_110point_cell_radius_m=clearance_cover_radius)
    (OUT/'geometry_checks.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    # Original, reproducible technical diagrams; full numeric data above.
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10})
    fig, axes = plt.subplots(1,2,figsize=(12,4.8),constrained_layout=True)
    ax=axes[0]
    ax.fill(*triangle.T,facecolor='#e5e7eb',edgecolor='#111827')
    for c,r,color,label in [(np.array([20.,0.]),20,'#b91c1c','Diameter disk: radius 20'),(center,radius,'#1d4ed8','Minimum enclosing disk: radius 23.09')]:
        ax.add_patch(plt.Circle(c,r,fill=False,color=color,lw=1.8,label=label))
    ax.set(xlim=(-8,48),ylim=(-23,40),aspect='equal',title='Q1: diameter does not determine a covering disk',xlabel='x (m)',ylabel='y (m)')
    ax.legend(loc='lower left',fontsize=8)
    ax=axes[1]
    x=np.linspace(0,1500,200)
    ax.fill_between(x,-x*math.tan(EPS),x*math.tan(EPS),color='#d1d5db',label='Outer bearing strip')
    for sign in [-1,1]:
        ax.add_patch(plt.Rectangle((700,400 if sign>0 else -500),100,100,facecolor='#bfdbfe',edgecolor='#1d4ed8'))
    ax.scatter([r['a_m'] for r in rows],[r['b_m'] for r in rows],color='#111827',s=25,label='Compared candidates')
    ax.scatter([0],[0],marker='s',color='#b91c1c',label='First sensing point')
    ax.set(xlim=(-60,1560),ylim=(-650,750),aspect='equal',title='Q2: guaranteed reception and lateral baseline',xlabel='Along first bearing (m)',ylabel='Lateral offset (m)')
    ax.legend(loc='upper left',fontsize=8)
    fig.savefig(OUT/'geometry_principles.png',dpi=200)
    plt.close(fig)
    print(json.dumps(data,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
