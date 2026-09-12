"""Preregistered, observation-only survey followed by certified cleanup.

This module deliberately leaves the frozen baseline Solver unchanged. Every
coverage station measures channels 1..20 before any source is cleared. Additional
measurements are all planned from frozen *estimated* geometry, never ground truth.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, replace
import hashlib
import json
import math
import random
import time

from .geometry import bearing_deg, distance, minimum_enclosing_circle
from .protocol import normalize_position
from .strategy import BudgetExhausted, Solver, SolverConfig


SURVEY_PROTOCOL_VERSION = "standard-survey-v1"


def _json_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SurveySolver(Solver):
    """Fixed coverage, seeded target sampling, fixed extra probes, then cleanup.

    ``survey_seed`` is an integer fixed before entering a case. It selects a
    simple random sample without replacement from the sorted discovered channels.
    All selected references and plans are frozen before the first extra probe.
    ``sample_count=2`` is the standard protocol; other values identify variants.
    Only the supplied RobotClient crosses the environment boundary.
    """

    def __init__(self, client, config=None, *, survey_seed, sample_count=2,
                 decision_log=None):
        if isinstance(survey_seed, bool) or not isinstance(survey_seed, int):
            raise ValueError("survey_seed must be a preregistered integer")
        if (isinstance(sample_count, bool) or not isinstance(sample_count, int)
                or not 0 <= sample_count <= 20):
            raise ValueError("sample_count must be an integer from 0 through 20")
        # Copy so an external caller cannot inadvertently change a running plan.
        super().__init__(client, replace(config or SolverConfig()), decision_log)
        self.survey_seed = survey_seed
        self.sample_count = sample_count
        self.phase = "setup"
        self.station_id = None
        self._active_design = None
        self._has_run = False
        self.coverage_completed = False
        self.extra_survey_completed = False
        self.eligible_channels = []
        self.sampled_channels = []
        self.survey_plan = []
        self.plan_sha256 = None
        self.deferred_near = {}
        self.phase_stats = {phase: {"measures": 0, "clear_attempts": 0,
                                   "clear_successes": 0}
                            for phase in ("coverage", "survey", "cleanup")}
        self.frozen_coverage_points = tuple(tuple(point) for point in self.points)
        self.coverage_sha256 = _json_hash(self.frozen_coverage_points)

    def _record(self, event, **data):
        data.setdefault("phase", self.phase)
        data.setdefault("station_id", self.station_id)
        data.setdefault("survey_protocol_version", SURVEY_PROTOCOL_VERSION)
        data.setdefault("survey_seed", self.survey_seed)
        super()._record(event, **data)

    def _set_phase(self, phase):
        if self.phase in self.phase_stats:
            self.phase_stats[self.phase]["end_virtual_time_s"] = self.client.virtual_time
            self._record("phase_finish", counts=copy.deepcopy(self.phase_stats[self.phase]))
        self.phase = phase
        self.station_id = None
        self._active_design = None
        if phase in self.phase_stats:
            self.phase_stats[phase]["start_virtual_time_s"] = self.client.virtual_time
        self._record("phase_start")

    def _check_budget(self, destination, maximum_action_cost):
        super()._check_budget(destination, maximum_action_cost)
        # Respect a smaller limit advertised by /enter as well as the config.
        predicted = (self.client.virtual_time
                     + distance(self.client.position, destination) / 5
                     + maximum_action_cost)
        if predicted >= self.client.max_virtual_duration_s - 1:
            raise BudgetExhausted("server_virtual_time_reserve")

    def _accepted_measure_request_id(self, channel, point, response):
        # Public request history is optional. The confirmed response's time,
        # channel and position remain a fallback join key when history is off.
        for item in reversed(getattr(self.client, "history", ())):
            if item.get("outcome") != "accepted" or item.get("path") != "/measure":
                continue
            request = item["request"]
            p = request.get("position", {})
            if (request.get("channel") == channel
                    and (p.get("x"), p.get("y")) == tuple(point)
                    and item["response"].get("virtual_time_s") == response["virtual_time_s"]):
                return request.get("request_id")
            return None
        return None

    def _measure(self, channel, point, reason, station=None):
        point = normalize_position(point)
        if self.phase == "coverage":
            self.station_id = f"coverage:{station:03d}"
        elif self.phase == "cleanup":
            self.station_id = None
            self._active_design = None
        self._check_budget(point, 6)
        old_position, old_channel = self.client.position, self.client.current_channel
        response = self.client.measure(channel, point)
        if response.get("accepted") is not True:
            raise RuntimeError("measure was rejected")
        self.stats["walk_distance_m"] += distance(old_position, point)
        self.stats["switches"] += int(old_channel != channel)
        self.stats["measures"] += 1
        self.phase_stats[self.phase]["measures"] += 1
        knowledge = self.channels[channel]
        knowledge.observe(point, response, station)
        self._record("measure", reason=reason, response=response,
                     target_channel=channel,
                     request_id=self._accepted_measure_request_id(channel, point, response),
                     measurement_design=copy.deepcopy(self._active_design),
                     knowledge=knowledge.snapshot())
        if response["measure_result"] == "near":
            if self.phase == "cleanup":
                self._clear(channel, point, {"type": "near", "radius_m": 5.})
            else:
                # The 5 m disk already intersects the retained outer hull. After
                # the survey it admits the baseline's outer-hull clear proof,
                # even if this channel never produces a direction observation.
                self.deferred_near.setdefault(channel, point)
                self._record("near_deferred", target_channel=channel,
                             near_position=point, radius_m=5.)
        return response

    def _clear(self, channel, point, certificate):
        if self.phase != "cleanup":
            raise RuntimeError("standard survey prohibits clearing before cleanup")
        success = super()._clear(channel, point, certificate)
        self.phase_stats["cleanup"]["clear_attempts"] += 1
        self.phase_stats["cleanup"]["clear_successes"] += int(success)
        return success

    def _make_target_plan(self, channel):
        knowledge = self.channels[channel]
        center, radius = minimum_enclosing_circle(knowledge.hull)
        positive_index = next(i for i, obs in enumerate(knowledge.observations)
                              if obs.result in ("direction", "near"))
        anchor = knowledge.observations[positive_index].position
        reference = {
            "kind": "observation_outer_hull_mec",
            "estimate_is_ground_truth": False,
            "channel": channel,
            "estimate": tuple(center),
            "outer_radius_m": radius,
            "hull": copy.deepcopy(knowledge.hull),
            "observations": [asdict(obs) for obs in knowledge.observations],
            "anchor_observation_index": positive_index,
            "anchor_position": anchor,
            "angle_reference": "east=0 degrees, counterclockwise; no inferred source orientation",
        }
        probes = []

        def add(point, kind, **generation):
            point = normalize_position(point)
            nominal_distance = distance(center, point)
            probes.append({
                "station_id": f"survey:{channel:02d}:{len(probes):02d}",
                "plan_index": len(probes), "channel": channel,
                "position": point, "kind": kind,
                "nominal_bearing_deg": (bearing_deg(center, point) % 360
                                         if nominal_distance > 0. else None),
                "nominal_distance_m": nominal_distance,
                "source_distance_interval_m": [max(0., nominal_distance-radius),
                                               nominal_distance+radius],
                "generation": generation,
            })

        # Distance and angle schedules are fixed protocol constants. They are
        # not retuned after positive/negative responses at additional probes.
        radii = (600., 1100., 1450.) if self.config.problem == 3 else (600., 1200.)
        angles = range(0, 360, 90 if self.config.problem == 3 else 45)
        for radial_distance in radii:
            for angle in angles:
                radians = math.radians(angle)
                point = (center[0]+radial_distance*math.cos(radians),
                         center[1]+radial_distance*math.sin(radians))
                add(point, "radial", radial_distance_m=radial_distance,
                    radial_bearing_deg=angle)
        # This anchor was measured before any target removal. In problem 4 its
        # known positive observation is useful even if a radial anchor is dark.
        add(anchor, "same_point_repeat", anchor_observation_index=positive_index,
            offset_m=[0., 0.])
        add((anchor[0]+1., anchor[1]), "neighbor",
            anchor_observation_index=positive_index, offset_m=[1., 0.])
        add((anchor[0], anchor[1]+1.), "neighbor",
            anchor_observation_index=positive_index, offset_m=[0., 1.])
        if self.config.problem == 3:
            add(anchor, "same_point_repeat", anchor_observation_index=positive_index,
                offset_m=[0., 0.])
        return {"channel": channel, "reference": reference, "probes": probes}

    def _freeze_survey_plan(self):
        if not self.coverage_completed:
            raise RuntimeError("survey selection requires the entire fixed scan")
        self.eligible_channels = sorted(k.channel for k in self._detected())
        self.sampled_channels = random.Random(self.survey_seed).sample(
            self.eligible_channels, min(self.sample_count, len(self.eligible_channels)))
        # Freeze every selected target before taking any added measurement.
        self.survey_plan = [self._make_target_plan(channel) for channel in self.sampled_channels]
        self.plan_sha256 = _json_hash(self.survey_plan)
        self._record("survey_plan", sampling="random.Random(seed).sample(sorted_detected, k)",
                     sample_count_requested=self.sample_count,
                     eligible_channels=self.eligible_channels,
                     sampled_channels=self.sampled_channels,
                     plan_sha256=self.plan_sha256, plan=copy.deepcopy(self.survey_plan))

    def run(self):
        if self._has_run:
            raise RuntimeError("a SurveySolver instance may run only once")
        self._has_run = True
        start = time.monotonic()
        status, error = "incomplete", None
        try:
            if (self.client.exited or self.client.cleared_channels
                    or self.client.virtual_time != 0 or self.client.position != (0., 0.)
                    or self.client.current_channel != 1):
                raise ValueError("standard survey requires a fresh case at the initial robot state")
            if not self.client.entered:
                self.client.enter()
            self._record("start", protocol="survey", config=asdict(self.config),
                         sample_count=self.sample_count, coverage_points=self.frozen_coverage_points,
                         coverage_sha256=self.coverage_sha256,
                         channel_order=list(range(1, 21)),
                         requires_full_scan_before_any_clear=True)
            self._set_phase("coverage")
            for station, point in enumerate(self.frozen_coverage_points):
                for channel in range(1, 21):
                    self._measure(channel, point, "standard_survey_coverage", station=station)
            required = set(range(len(self.frozen_coverage_points)))
            if not all(k.coverage_indices == required for k in self.channels.values()):
                raise RuntimeError("fixed scan ledger is incomplete")
            self.coverage_completed = True
            self._set_phase("survey")
            self._freeze_survey_plan()
            for target_plan in self.survey_plan:
                for probe in target_plan["probes"]:
                    self.station_id = probe["station_id"]
                    self._active_design = {**copy.deepcopy(probe),
                        "reference_channel": target_plan["channel"],
                        "reference_estimate": target_plan["reference"]["estimate"],
                        "reference_outer_radius_m": target_plan["reference"]["outer_radius_m"],
                        "plan_sha256": self.plan_sha256}
                    self._measure(target_plan["channel"], probe["position"], "standard_survey_probe")
            self.extra_survey_completed = True
            self._set_phase("cleanup")
            self._joint_service(None, force=True)
            self.stop_evidence = self._complete_evidence()
            if self.stop_evidence:
                status = "complete"
            else:
                error = "missing_completion_certificate"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._record("failure", error=error)
        finally:
            if self.phase in self.phase_stats:
                self.phase_stats[self.phase]["end_virtual_time_s"] = self.client.virtual_time
                self._record("phase_finish", counts=copy.deepcopy(self.phase_stats[self.phase]))
            # Never create a new action after an unresolved executed request.
            if (self.client.entered and not self.client.exited
                    and not getattr(self.client, "pending_request", None)):
                try:
                    self.client.exit()
                except Exception as exc:
                    if status == "complete":
                        status = "complete_exit_unconfirmed"
                    error = (error+"; " if error else "")+f"exit: {type(exc).__name__}: {exc}"
        computed_time = (self.stats["walk_distance_m"]/5+self.stats["switches"]
                         +5*self.stats["measures"]+3*self.stats["clear_attempts"]
                         +2*self.stats["clear_successes"])
        result = {
            "status": status, "error": error, "problem": self.config.problem,
            "protocol": "survey", "survey_protocol_version": SURVEY_PROTOCOL_VERSION,
            "survey_seed": self.survey_seed, "sample_count": self.sample_count,
            "config": asdict(self.config), **self.stats,
            "coverage_completed": self.coverage_completed,
            "extra_survey_completed": self.extra_survey_completed,
            "survey_sampling_complete": self.coverage_completed and self.extra_survey_completed,
            "cleanup_complete": self.stop_evidence is not None,
            "eligible_channels": self.eligible_channels,
            "sampled_channels": self.sampled_channels,
            "coverage_sha256": self.coverage_sha256, "plan_sha256": self.plan_sha256,
            "phase_stats": copy.deepcopy(self.phase_stats),
            "deferred_near_channels": sorted(self.deferred_near),
            "total_virtual_time_s": self.client.virtual_time,
            "independently_accounted_time_s": computed_time,
            "timing_residual_s": self.client.virtual_time-computed_time,
            "average_clear_time_s": (self.client.virtual_time/self.stats["clear_successes"]
                                      if self.stats["clear_successes"] else None),
            "program_real_time_s": time.monotonic()-start,
            "stop_evidence": self.stop_evidence, "source_total": None, "clear_fraction": None,
        }
        self._record("finish", result=result)
        return result


def run_survey(client, config, survey_seed, decision_log=None, *, sample_count=2):
    """Run one case; returns the standard Solver result plus survey audit fields."""
    return SurveySolver(client, config, survey_seed=survey_seed,
                        sample_count=sample_count, decision_log=decision_log).run()
