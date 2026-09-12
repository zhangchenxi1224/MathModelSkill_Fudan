"""Safety-critical ordering checks; no privileged scene data enters the mixin."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor"))
sys.path.insert(0, str(ROOT))

from bsolver.coverage import route_length
from bsolver.geometry import certify_clear
from bsolver.knowledge import InconsistentKnowledge
from methods.scheduling import RouteMixin, closest_hull_point


def knowledge(channel, hull, coverage=()):
    return SimpleNamespace(channel=channel, hull=hull, status="detected",
                           coverage_indices=set(coverage))


class Harness(RouteMixin):
    def __init__(self, channels, points=(), position=(0., 0.), options=None):
        self.channels = {k.channel: k for k in channels}
        self.points = list(points)
        self.client = SimpleNamespace(position=position, current_channel=1)
        self.config = SimpleNamespace(clear_radius=19.999, nearest_safe=True, joint_detour_m=800.)
        self.schedule_options = options or {}
        self.events = []
        self.serviced = []

    def _detected(self):
        return [k for k in self.channels.values() if k.status == "detected"]

    def _record(self, event, **kwargs):
        self.events.append((event, kwargs))

    def _localize(self, channel):
        self.serviced.append(channel)
        k = self.channels[channel]
        self.client.position = self._route_geometry(k)[0]
        k.status = "cleared"

    def _complete_evidence(self):
        return None


@pytest.mark.parametrize("hull,point,expected", [
    ([(1., 2.)], (0., 0.), (1., 2.)),
    ([(1., 0.), (1., 4.)], (0., 3.), (1., 3.)),
    ([(0., 0.), (2., 0.), (2., 2.), (0., 2.)], (1., 1.), (1., 1.)),
    ([(0., 0.), (2., 0.), (2., 2.), (0., 2.)], (3., 1.), (2., 1.)),
])
def test_hull_projection(hull, point, expected):
    assert closest_hull_point(hull, point) == pytest.approx(expected)


def test_reorder_preserves_completed_prefix_and_never_increases_length():
    k = knowledge(1, [(0., 0.)], coverage=(0,))
    k.status = "unknown"
    solver = Harness([k], [(0., 0.), (100., 0.), (10., 0.), (20., 0.)])
    original = list(solver.points)
    first = solver._route_reorder_suffix(1)
    assert solver.points[0] == original[0]
    assert set(solver.points[1:]) == set(original[1:])
    assert k.coverage_indices == {0}
    assert first == solver.points[1]
    assert route_length(solver.points[1:]) <= route_length(original[1:])


def test_reorder_refuses_any_observed_suffix_index():
    solver = Harness([knowledge(1, [(0., 0.)], coverage=(0, 2))],
                     [(0., 0.), (100., 0.), (10., 0.)])
    with pytest.raises(InconsistentKnowledge, match="observed"):
        solver._route_reorder_suffix(1)


def test_reorder_rejects_station_loss_from_duplicate_input():
    solver = Harness([], [(0., 0.), (10., 0.), (10., 0.)])
    with pytest.raises(InconsistentKnowledge, match="changed coverage"):
        solver._route_reorder_suffix(1)


def test_cached_circle_invalidated_by_hull_change():
    k = knowledge(1, [(0., 0.), (10., 0.)])
    solver = Harness([k])
    assert solver._route_geometry(k)[0] == (5., 0.)
    k.hull[:] = [(20., 0.), (30., 0.)]
    assert solver._route_geometry(k)[0] == (25., 0.)


def test_clear_proxy_is_certified_and_uses_nearest_safe_point():
    k = knowledge(1, [(100., -1.), (100., 1.)])
    solver = Harness([k])
    point, cost, kind = solver._route_destination(k, "hull")
    assert certify_clear(k.hull, point, radius=20., margin=1e-5)
    assert point[0] < 100.
    assert cost == 5. and kind == "certified_clear_destination"


def test_noncertified_hull_proxy_is_never_claimed_as_clear():
    k = knowledge(1, [(100., -80.), (100., 80.), (200., 0.)])
    solver = Harness([k])
    point, _, kind = solver._route_destination(k, "hull")
    assert point == (100., 0.)
    assert kind == "hull_proxy"
    assert not certify_clear(k.hull, point, radius=20., margin=1e-5)


def test_service_threshold_then_force_and_finite_nonprogress():
    k = knowledge(1, [(2000., 0.)])
    solver = Harness([k], options={"reorder": False})
    solver._joint_service(None)
    assert not solver.serviced
    solver._joint_service(None, force=True)
    assert solver.serviced == [1]
    k.status = "detected"
    solver.serviced.clear()
    solver._localize = lambda channel: solver.serviced.append(channel)
    solver._joint_service(None, force=True)
    assert solver.serviced == [1]


def test_inherited_enumeration_visits_every_reordered_point_once():
    k = knowledge(1, [(0., 0.)])
    k.status = "unknown"
    solver = Harness([k], [(0., 0.), (100., 0.), (10., 0.), (20., 0.)])
    original = set(solver.points)
    seen = []
    for i, point in enumerate(solver.points):
        seen.append(point)
        k.coverage_indices.add(i)
        next_point = solver.points[i + 1] if i + 1 < len(solver.points) else None
        solver._joint_service(next_point, force=next_point is None)
    assert set(seen) == original and len(seen) == len(original)
    assert k.coverage_indices == set(range(len(solver.points)))


@pytest.mark.parametrize("options", [{"service_score": "bogus"}, {"shared": True}, {"reorder": 1}])
def test_invalid_options_fail_closed(options):
    with pytest.raises(ValueError):
        Harness([], options=options)._joint_service(None)
