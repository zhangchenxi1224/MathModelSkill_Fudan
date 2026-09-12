import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest
import zipfile

MODULE = Path(__file__).resolve().parents[1] / "scripts/audit_round_integrity.py"
spec = importlib.util.spec_from_file_location("round_integrity_audit", MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def state(position=(0., 0.), channel=1, t=0., cleared=(), entered=True, exited=False):
    return {"position": {"x": position[0], "y": position[1]}, "current_channel": channel,
            "virtual_time_s": t, "cleared_count": len(cleared), "cleared_channels": list(cleared),
            "entered": entered, "exited": exited}


def request(identifier, path, before, after, *, channel=None, position=None, outcome=None, accepted=True):
    body = {"request_id": identifier}
    if channel is not None:
        body["channel"] = channel
    if position is not None:
        body["position"] = {"x": position[0], "y": position[1]}
    response = {"accepted": accepted, "virtual_time_s": after["virtual_time_s"] if accepted else 0.0}
    if path == "/measure":
        response["measure_result"] = outcome or "no_signal"
    if path == "/clear":
        response["clear_result"] = outcome
    return {"path": path, "request": body, "response": response,
            "state_before": copy.deepcopy(before), "state_after": copy.deepcopy(after)}


def manual_trace():
    initial = state(entered=False)
    s0 = state()
    s7 = state((3, 4), 2, 7)
    s11 = state((6, 8), 2, 11)
    s16 = state((6, 8), 2, 16, (3,))
    s21 = state((6, 8), 2, 21, (3,))
    return [request("enter", "/enter", initial, s0),
            request("m1", "/measure", s0, s7, channel=2, position=(3, 4)),
            request("c1", "/clear", s7, s11, channel=3, position=(6, 8), outcome="no_target_in_range"),
            request("c2", "/clear", s11, s16, channel=3, position=(6, 8), outcome="success"),
            request("m2", "/measure", s16, s21, channel=2, position=(6, 8)),
            request("exit", "/exit", s21, state((6, 8), 2, 21, (3,), exited=True))]


class RoundIntegrityTests(unittest.TestCase):
    def test_independent_cost_clear_moves_but_does_not_tune(self):
        result = audit.replay_requests(manual_trace())
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["computed_virtual_time_s"], 21.)
        self.assertEqual(result["counts"]["walk_distance_m"], 10.)
        self.assertEqual(result["counts"]["switches"], 1)
        self.assertEqual(result["counts"]["clear_attempts"], 2)
        self.assertEqual(result["counts"]["clear_successes"], 1)
        self.assertEqual(result["state"]["current_channel"], 2)

    def test_rejection_timeout_and_cached_response_do_not_double_apply(self):
        rows = manual_trace()
        rejected = request("rejected", "/measure", rows[1]["state_after"], rows[1]["state_after"],
                           channel=9, position=(999, 999), accepted=False)
        timeout = copy.deepcopy(rows[2])
        timeout.pop("response")
        timeout["state_after"] = copy.deepcopy(timeout["state_before"])
        duplicate = copy.deepcopy(rows[2])
        duplicate["state_before"] = copy.deepcopy(duplicate["state_after"])
        rows[2:2] = [rejected, timeout]
        rows.insert(5, duplicate)
        result = audit.replay_requests(rows)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["computed_virtual_time_s"], 21.)
        self.assertEqual(result["counts"]["duplicate_accepted_responses"], 1)
        self.assertEqual(result["counts"]["rejected_response_rows"], 1)
        self.assertEqual(result["counts"]["unconfirmed_transport_rows"], 1)

    def test_corrupt_time_and_receiver_state_are_detected(self):
        rows = manual_trace()
        rows[2]["response"]["virtual_time_s"] = 99
        rows[2]["state_after"]["current_channel"] = 3
        codes = {x["code"] for x in audit.replay_requests(rows)["issues"]}
        self.assertIn("response_time_mismatch", codes)
        self.assertIn("state_mismatch", codes)

    def test_rejected_id_is_not_consumed_and_can_be_corrected(self):
        rows = manual_trace()
        rejected = request("m1", "/measure", rows[0]["state_after"], rows[0]["state_after"],
                           channel=9, position=(999, 999), accepted=False)
        rows.insert(1, rejected)
        result = audit.replay_requests(rows)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["computed_virtual_time_s"], 21.)
        self.assertEqual(result["counts"]["unique_accepted_actions"], 6)

    def test_rejected_placeholder_zero_does_not_reset_nonzero_state(self):
        rows = manual_trace()
        rejected = request("reject-at-seven", "/measure", rows[1]["state_after"], rows[1]["state_after"],
                           channel=9, position=(999, 999), accepted=False)
        rows.insert(2, rejected)
        result = audit.replay_requests(rows)
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["action_audit"][2]["computed_response_virtual_time_s"], 0.)
        self.assertEqual(result["computed_virtual_time_s"], 21.)
        rejected["response"]["virtual_time_s"] = 7.
        self.assertIn("response_time_mismatch", {r["code"] for r in audit.replay_requests(rows)["issues"]})

    def test_repeated_success_with_new_id_is_forbidden(self):
        rows = manual_trace()
        rows[4] = request("bad-repeat", "/clear", rows[3]["state_after"], rows[4]["state_after"],
                          channel=3, position=(6, 8), outcome="success")
        self.assertIn("repeat_success_same_channel", {x["code"] for x in audit.replay_requests(rows)["issues"]})

    def test_unresolved_response_requires_same_id_and_body(self):
        rows = manual_trace()
        lost = copy.deepcopy(rows[1])
        lost.pop("response")
        lost["state_after"] = lost["state_before"]
        rows.insert(1, lost)
        rows[2]["request"]["request_id"] = "different-after-loss"
        self.assertIn("new_id_while_prior_response_unresolved", {x["code"] for x in audit.replay_requests(rows)["issues"]})

    @staticmethod
    def survey_fixture():
        points = [[float(i), 0.] for i in range(7)]
        seed = 73
        sampled = random.Random(seed).sample([1, 2], 2)
        plan = [{"channel": channel, "probes": [
            {"station_id": f"survey:{channel:02d}:{i:02d}", "position": [10. + i, float(channel)]}
            for i in range(16)]} for channel in sampled]
        ph, ch = audit.digest(plan), audit.digest(points)
        assignment = {"problem": 3, "protocol": "survey", "survey_seed": seed}
        result = {"survey_seed": seed, "sample_count": 2, "coverage_sha256": ch, "plan_sha256": ph,
                  "eligible_channels": [1, 2], "sampled_channels": sampled, "phase_stats": {
                      "coverage": {"measures": 140, "clear_attempts": 0, "clear_successes": 0},
                      "survey": {"measures": 32, "clear_attempts": 0, "clear_successes": 0},
                      "cleanup": {"measures": 0, "clear_attempts": 0, "clear_successes": 0}}}
        decisions = [{"event": "start", "survey_seed": seed, "coverage_points": points, "coverage_sha256": ch}]
        plan_record = {"event": "survey_plan", "survey_seed": seed, "plan": plan, "plan_sha256": ph,
                       "eligible_channels": [1, 2], "sampled_channels": sampled}
        accepted = []
        design = [(position, channel, "coverage", {}) for position in points for channel in range(1, 21)]
        design += [(probe["position"], target["channel"], "survey", {**probe, "plan_sha256": ph})
                   for target in plan for probe in target["probes"]]
        for i, (position, channel, phase, extra) in enumerate(design, 1):
            if i == 141:
                decisions.append(plan_record)
            response = {"accepted": True, "virtual_time_s": i * 5., "measure_result": "near" if channel in (1, 2) else "no_signal"}
            body = {"request_id": str(i), "channel": channel, "position": dict(zip(("x", "y"), position))}
            accepted.append({"line": i, "request": body, "path": "/measure", "response": response})
            decisions.append({"event": "measure", "request_id": str(i), "position": position, "target_channel": channel,
                              "phase": phase, "survey_seed": seed, "response": response, "measurement_design": extra,
                              "station_id": extra.get("station_id")})
        return assignment, result, decisions, accepted

    def test_fixed_all20_scan_seed_and_plan_are_certified(self):
        result = audit.audit_survey(*self.survey_fixture())
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["actual_coverage_measures"], 140)
        self.assertTrue(result["all20_every_actual_station_certificate"])

    def test_missing_channel_and_early_clear_are_detected(self):
        assignment, result, decisions, accepted = self.survey_fixture()
        accepted[1]["request"]["channel"] = 1
        accepted.insert(3, {"line": 3, "path": "/clear", "request": {"channel": 1}, "response": {"clear_result": "success"}})
        result["survey_seed"] += 1
        codes = {x["code"] for x in audit.audit_survey(assignment, result, decisions, accepted)["issues"]}
        self.assertIn("survey_all20_every_station_order_certificate_failed", codes)
        self.assertIn("clear_before_full_survey_finished", codes)
        self.assertIn("survey_seed_mismatch", codes)

    def test_ten_core_current_and_archive_hashes_checked(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            root = Path(temp) / "round"
            project.mkdir()
            root.mkdir()
            files = {}
            for i in range(10):
                path = project / f"core{i}.py"
                path.write_text(str(i))
                files[path.name] = audit.sha(path)
            aggregate = hashlib.sha256()
            for name in sorted(files):
                aggregate.update(name.encode())
                aggregate.update((project / name).read_bytes())
            (root / "baseline_freeze.json").write_text(json.dumps({"files": files, "source_sha256": aggregate.hexdigest()}))
            with zipfile.ZipFile(root / "baseline_source.zip", "w") as archive:
                for name in files:
                    archive.write(project / name, name)
            survey = project / "src/bsolver/survey.py"
            survey.parent.mkdir(parents=True)
            survey.write_text("frozen survey")
            (root / "survey_freeze.json").write_text(json.dumps({"sha256": audit.sha(survey)}))
            self.assertEqual(audit.check_freeze(root, project)["issues"], [])
            (project / "core0.py").write_text("changed")
            self.assertIn("frozen_core_hash_mismatch", {x["code"] for x in audit.check_freeze(root, project)["issues"]})

    def test_round2_freeze_checks_plan_candidate_runtime_without_survey(self):
        with tempfile.TemporaryDirectory() as temp:
            project, root = Path(temp) / "project", Path(temp) / "round2"
            project.mkdir()
            root.mkdir()
            files, aggregate = {}, hashlib.sha256()
            for i in range(10):
                name = f"core{i}.py"
                (project / name).write_text(str(i))
                files[name] = audit.sha(project / name)
                aggregate.update(name.encode())
                aggregate.update((project / name).read_bytes())
            baseline = root / "baseline_freeze.json"
            baseline.write_text(json.dumps({"files": files, "source_sha256": aggregate.hexdigest()}))
            with zipfile.ZipFile(root / "baseline_source.zip", "w") as archive:
                for name in files:
                    archive.write(project / name, name)
            candidate = project / "candidate.json"
            candidate.write_text("{}")
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps({"candidate_file": str(candidate), "candidate_sha256": audit.sha(candidate),
                                            "formal_authorized": False, "cases": []}))
            validation = {"plan_sha256": audit.sha(plan_path), "candidate_sha256": audit.sha(candidate),
                          "baseline_freeze_sha256": audit.sha(baseline), "baseline_source_sha256": aggregate.hexdigest(),
                          "candidate_dependency_hashes": {"core0.py": files["core0.py"]},
                          "runtime_hashes": {"core1.py": files["core1.py"]}, "case_count": 0, "formal_authorized": False}
            (root / "validation_freeze.json").write_text(json.dumps(validation))
            result = audit.check_freeze(root, project)
            self.assertEqual(result["issues"], [])
            self.assertEqual(result["freeze_kind"], "independent_arm_validation")
            self.assertIsNone(result["survey_sha256"])
            candidate.write_text('{"changed":true}')
            self.assertIn("validation_candidate_hash_mismatch", {x["code"] for x in audit.check_freeze(root, project)["issues"]})


if __name__ == "__main__":
    unittest.main()
