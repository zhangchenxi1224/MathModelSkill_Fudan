"""Isolated Q3 coverage experiment; never changes the parent solver or HTTP state."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent.parent / "B_solution"
sys.path.insert(0, str(BASE / "src"))

from bsolver.strategy import Solver

DOMAIN = 1800.0
RECEPTION = 1000.0
ORIGINAL_RING = 900.0 * math.sqrt(3.0)
MINIMUM_RING = DOMAIN * math.cos(math.pi / 6) - math.sqrt(
    RECEPTION ** 2 - DOMAIN ** 2 * math.sin(math.pi / 6) ** 2)
MAXIMUM_RING = math.sqrt(3.0) * RECEPTION


def covering_radius(ring: float) -> float:
    """Exact real-arithmetic covering radius, origin + regular hexagon.

    The formula applies for 0 <= ring <= sqrt(3) * DOMAIN. Computation uses
    ordinary floats; certification below adds a coordinate-scale guard.
    """
    if not math.isfinite(ring) or not 0 <= ring <= math.sqrt(3) * DOMAIN:
        raise ValueError("ring outside formula domain")
    return math.sqrt(max(ring * ring / 3,
                         DOMAIN * DOMAIN + ring * ring - math.sqrt(3) * DOMAIN * ring))


def certificate(ring: float) -> dict:
    bound = covering_radius(ring) + 1e-6
    if bound >= RECEPTION:
        raise ValueError("coverage requires a strictly positive numerical margin")
    return {
        "type": "analytic_regular_hexagon_and_origin",
        "domain_radius_m": DOMAIN,
        "minimum_reception_radius_m": RECEPTION,
        "ring_radius_m": ring,
        "covering_radius_upper_m": bound,
        "distance_margin_m": RECEPTION - bound,
        "proof": "PROOF.md",
        "requires_each_unresolved_channel_at_all_seven_points": True,
        "applies_to": "problem_3_omnidirectional_only",
        "guarantees_localization_alone": False,
    }


def parent_source_hashes() -> dict:
    paths = sorted((BASE / "src" / "bsolver").glob("*.py"))
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


class ContractedCoverageSolver(Solver):
    """Change coverage coordinates only; retain the baseline's route order.

    Scaling the *ordered* baseline avoids floating-point tie breaks rotating
    an otherwise symmetric route and confounding the radius comparison.
    No target truth, source count, or scenario seed enters this class.
    """
    def __init__(self, client, config=None, decision_log=None, *, ring=1125.0):
        super().__init__(client, config, decision_log)
        if self.config.problem != 3:
            raise ValueError("This optimization is proved for Q3 only")
        self.coverage_proof = certificate(ring)
        if ring != ORIGINAL_RING:
            scale = ring / ORIGINAL_RING
            self.points = [(x * scale, y * scale) for x, y in self.points]
        self._record("coverage_optimization", proof=self.coverage_proof,
                     ordered_points=self.points)

    def _complete_evidence(self):
        result = super()._complete_evidence()
        if result is not None and result["type"] == "per_channel_coverage":
            result["construction"] = "origin_and_contracted_regular_hexagon"
            result["analytic_certificate"] = self.coverage_proof
        return result
