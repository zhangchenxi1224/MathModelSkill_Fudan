"""Scientific figures from explicit geometry and saved local experiment records."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle
from bsolver.coverage import directional_points, order_route, route_length
from bsolver.refined_coverage import refined_directional_route, refined_coverage_certificate

OUT = ROOT / "results/p4_refinement_diagnostics"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    routes = [order_route(directional_points("triangular")), refined_directional_route()]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5), constrained_layout=True)
    for ax, route, name, color in zip(axes, routes,
                                    ("Current: original coverage", "Refined: certified coverage"),
                                    ("#48617B", "#00846E")):
        path = [(0., 0.)] + route
        ax.add_patch(Circle((0, 0), 1800, color="#E9EEF4", ec="#56616D", lw=1))
        ax.plot([p[0] for p in path], [p[1] for p in path], color=color, lw=1.4, alpha=.85)
        ax.scatter([p[0] for p in route], [p[1] for p in route], s=22, color=color, zorder=3)
        ax.scatter([0], [0], marker="*", s=120, color="#D08620", zorder=5, label="Robot start")
        for i, point in enumerate(route):
            ax.annotate(str(i+1), point, xytext=(4, 4), textcoords="offset points", fontsize=7)
        ax.set(title=f"{name}\n{len(route)} stations | {route_length(route)/1000:.2f} km",
               xlabel="x (m)", ylabel="y (m)", xlim=(-2900,2900), ylim=(-2900,2900))
        ax.set_aspect("equal")
        ax.grid(alpha=.15)
        ax.legend(loc="upper right", frameon=False)
    fig.savefig(OUT / "coverage_routes.png", dpi=170)
    fig.savefig(OUT / "coverage_routes.pdf")
    plt.close(fig)
    data = json.loads((OUT / "official_cost_diagnosis.json").read_text(encoding="utf-8"))
    fig, ax = plt.subplots(figsize=(9, 3.8), constrained_layout=True)
    arms = ("baseline", "candidate")
    left = [0., 0.]
    for key, label, color in (("movement_s", "Movement", "#42627E"),
                              ("measurement_s", "Measurement", "#4B9D8B"),
                              ("switch_s", "Channel switch", "#C7A34A"),
                              ("clear_call_s", "Clear calls", "#BD735D")):
        values = [data["aggregates"][a]["mean"][key]/60 for a in arms]
        ax.barh(arms, values, left=left, label=label, color=color)
        left = [x+y for x, y in zip(left, values)]
    ax.set(xlabel="Mean virtual minutes per complete case",
           title="Existing official practice evidence: P4, 30 cases per arm")
    ax.legend(ncol=4, loc="upper center", bbox_to_anchor=(.5,-.18), frameon=False)
    for i, total in enumerate(left):
        ax.text(total+1, i, f"{total:.1f}", va="center")
    ax.set_xlim(0, max(left)*1.12)
    fig.savefig(OUT / "official_costs.png", dpi=170)
    plt.close(fig)
    source = dict(original=dict(stations=routes[0], route_length_m=route_length(routes[0])),
                  refined=dict(stations=routes[1], certificate=refined_coverage_certificate()),
                  official_cost_source="official_cost_diagnosis.json",
                  interpretation="Route diagrams describe static geometry, not an official refined-policy run.")
    (OUT / "figure_sources.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
    print(json.dumps({"figures": ["coverage_routes.png", "official_costs.png"], "output":str(OUT)}))


if __name__ == "__main__":
    main()
