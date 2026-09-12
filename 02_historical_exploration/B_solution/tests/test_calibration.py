import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from bsolver.calibration import (assign_split, bootstrap_mean, calibrate, extract_case,
                                fit_composition, joint_prior, unique_accepted_actions, visibility_matrices,
                                channel_visibility_patterns, mechanism_checks, _compare_scalar,
                                distinct_mechanism_candidates)


def action(request_id, path="/measure", *, channel=1, point=(0, 0), outcome="direction", angle=359., accepted=True, t=5):
    body = {"request_id": request_id}
    response = {"accepted": accepted, "virtual_time_s": t}
    if path in ("/measure", "/clear"):
        body.update(channel=channel, position={"x": point[0], "y": point[1]})
    if path == "/measure":
        response["measure_result"] = outcome
        if outcome == "direction":
            response["svd_deg"] = angle
    elif path == "/clear":
        response["clear_result"] = outcome
    return {"path": path, "request": body, "response": response}


def write_case(path, *, split="fit", n=13, ndir=4, status="complete", problem=4, actions=None, decisions=None,
               case_code="TEST-AAAA-BBBB-CCCC", environment="official_practice", protocol="survey"):
    path.mkdir(parents=True, exist_ok=True)
    assignment = {"case_id": path.name, "problem": problem, "protocol": protocol,
                  "environment": environment, "split": split, "survey_seed": 7}
    result = {"problem": problem, "environment": environment, "status": status,
              "total_virtual_time_s": 100 if status == "complete" else 0,
              "program_real_time_s": .1, "clear_successes": n if status == "complete" else 0}
    audit = {"case_code": case_code, "source_total_post_exit": n, "directional_total_post_exit": ndir}
    for name, value in (("assignment.json", assignment), ("result.json", result), ("post_exit_audit.json", audit)):
        (path / name).write_text(json.dumps(value), encoding="utf-8")
    actions = actions if actions is not None else [action("enter", "/enter", t=0), action("m"), action("exit", "/exit", t=100)]
    (path / "requests.jsonl").write_text("\n".join(json.dumps(x) for x in actions), encoding="utf-8")
    (path / "decisions.jsonl").write_text("\n".join(json.dumps(x) for x in decisions or []), encoding="utf-8")


class CalibrationTests(unittest.TestCase):
    def test_split_stable_independent_of_action_count(self):
        self.assertEqual(assign_split("whole-case"), assign_split("whole-case"))
        self.assertIn(assign_split("whole-case")["split"], ("fit", "development"))
        with self.assertRaises(ValueError):
            assign_split("case", fit_fraction=1)

    def test_joint_distribution_retains_dependence(self):
        cases = [{"problem": 4, "split": "fit", "source_kind": "official", "N": n, "Ndir": d,
                  "case_group_id": str(i)} for i, (n, d) in enumerate([(10, 0), (10, 0), (16, 16), (16, 16)])]
        model = fit_composition(cases, 4, bootstrap_repeats=30)
        empirical = model["composition_candidates"]["empirical_joint"]["count_distribution"]
        self.assertEqual({(x["n"], x["n_directed"]) for x in empirical}, {(10, 0), (16, 16)})
        self.assertAlmostEqual(sum(x["probability"] for x in empirical), 1)
        self.assertAlmostEqual(sum(x["probability"] for x in model["count_distribution"]), 1)
        self.assertTrue(all(0 <= x["n_directed"] <= x["n"] for x in model["count_distribution"]))

    def test_unknown_directional_count_not_imputed_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case"
            write_case(path, ndir=None)
            case = extract_case(path)["case"]
            self.assertIsNone(case["Ndir"])
            self.assertEqual(fit_composition([case], 4)["n_fit_known_joint"], 0)

    def test_q3_directional_zero_is_explicit_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case"
            write_case(path, ndir=None, problem=3)
            case = extract_case(path)["case"]
            self.assertEqual(case["Ndir"], 0)
            self.assertIn("Q3_omnidirectional_fact", case["composition_source"])

    def test_idempotent_retry_and_rejected_actions(self):
        accepted = action("same")
        rejected = action("same", accepted=False)
        rows = list(unique_accepted_actions([rejected, accepted, copy.deepcopy(accepted)]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 1)
        issues = []
        list(unique_accepted_actions([accepted, action("same", point=(1, 2))], issues))
        self.assertTrue(issues)

    def test_measure_after_clear_is_separate_condition(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case"
            rows = [action("m1"), action("clear", "/clear", outcome="success"),
                    action("m2", outcome="no_signal", t=10)]
            write_case(path, actions=rows)
            data = extract_case(path)
            self.assertFalse(data["measurements"][0]["cleared_before"])
            self.assertTrue(data["measurements"][1]["cleared_before"])
            self.assertEqual(data["measurements"][1]["existence_before"], "known_existing")

    def test_unmeasured_matrix_cells_are_null_not_negative(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case"
            write_case(path, actions=[action("m", outcome="no_signal")])
            matrix = visibility_matrices(extract_case(path)["measurements"])[0]
            self.assertTrue(matrix["observed_mask"][0])
            self.assertEqual(matrix["first_outcomes"][0], "no_signal")
            self.assertFalse(matrix["observed_mask"][1])
            self.assertIsNone(matrix["first_outcomes"][1])
            self.assertFalse(matrix["all_20_observed"])

    def test_circular_repeat_is_observable_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case"
            write_case(path, actions=[action("a", angle=359.), action("b", angle=1., t=10)])
            proxy = extract_case(path)["proxies"][0]
            self.assertEqual(proxy["reported_angle_delta_deg"], 2.)
            self.assertIn("NOT true", proxy["interpretation"])

    def test_failure_retained_and_does_not_train_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root / "fit", n=10, ndir=0, case_code="FIT0-AAAA-BBBB-CCCC")
            write_case(root / "held", split="development", n=16, ndir=16, case_code="HELD-AAAA-BBBB-CCCC")
            write_case(root / "failed", status="incomplete", n=None, ndir=None, case_code=None, actions=[])
            model = calibrate([root], root / "output", bootstrap_repeats=20)
            self.assertEqual(model["problems"]["4"]["n_fit_known_joint"], 1)
            cases = [json.loads(x) for x in (root / "output/features/cases.jsonl").read_text().splitlines()]
            self.assertEqual(len(cases), 3)
            self.assertEqual(sum(r["completed"] for r in cases), 2)
            empirical = model["problems"]["4"]["composition_candidates"]["empirical_joint"]["count_distribution"]
            self.assertEqual(len(empirical), 1)
            self.assertEqual(empirical[0]["n"], 10)
            costs = json.loads((root / "output/cost_summaries.json").read_text())
            fit = next(r for r in costs if r["split"] == "fit")
            self.assertEqual(fit["all_attempts_observed_virtual_s"]["mean"], 50.)
            self.assertEqual(fit["complete_only_virtual_s"]["mean"], 100.)

    def test_duplicate_case_cannot_cross_splits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root / "a", split="fit")
            write_case(root / "b", split="development")
            model = calibrate([root], root / "output", bootstrap_repeats=10)
            self.assertEqual(model["problems"]["4"]["n_fit_known_joint"], 0)

    def test_local_cases_never_enter_official_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_case(root / "local", environment="self_built_fixed_scenario")
            model = calibrate([root], root / "output", bootstrap_repeats=10)
            self.assertEqual(model["problems"]["4"]["n_fit_known_joint"], 0)

    def test_empty_prior_and_single_case_intervals_honest(self):
        self.assertAlmostEqual(sum(joint_prior(4).values()), 1)
        self.assertAlmostEqual(sum(joint_prior(3).values()), 1)
        self.assertEqual(bootstrap_mean([1.])["ci95"], [None, None])
        self.assertEqual(fit_composition([], 4)["composition_candidates"]["empirical_joint"]["status"], "unavailable")

    def test_planned_assignment_is_not_an_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planned = root / "planned"
            planned.mkdir()
            (planned / "assignment.json").write_text(json.dumps({"case_id": "planned", "problem": 4,
                "environment": "official_practice", "protocol": "survey", "split": "fit"}))
            model = calibrate([root], root / "output", bootstrap_repeats=10)
            self.assertEqual(model["provenance"]["n_official_attempts"], 0)
            self.assertEqual(model["provenance"]["n_planned_unstarted"], 1)
            self.assertEqual(json.loads((root / "output/cost_summaries.json").read_text()), [])

    def test_local_model_instances_do_not_collide(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = []
            for candidate in ("radius_min", "radius_max"):
                path = Path(tmp) / candidate
                write_case(path, environment="local")
                assignment = json.loads((path / "assignment.json").read_text())
                assignment.update(scenario_id="shared-scenario", common_case_id="shared-seed", model_candidate=candidate)
                (path / "assignment.json").write_text(json.dumps(assignment))
                cases.append(extract_case(path)["case"])
            self.assertNotEqual(cases[0]["case_instance_id"], cases[1]["case_instance_id"])
            self.assertEqual(cases[0]["resampling_group_id"], cases[1]["resampling_group_id"])

    @staticmethod
    def joint_fixture(identity="official-0", source="official", crossed=False, missing=False):
        case = {"case_group_id": identity, "resampling_group_id": identity, "problem": 4, "protocol": "survey",
                "split": "development", "source_kind": source, "model_candidate": "test_mechanism",
                "attempt_started": True, "completed": True, "N": 10, "Ndir": 2,
                "clear_attempts": 10, "clear_successes": 10, "walk_distance_m": 100.,
                "total_virtual_time_s": 200., "measures": 40,
                "planned_coverage_points": [(0., 0.), (1., 0.)], "confirmed_existing_channels": [1, 2]}
        rows = []
        for station in range(2):
            for channel in range(1, 21):
                if missing and station == 1 and channel == 20:
                    continue
                visible = (channel == station + 1 if crossed else channel == 1)
                rows.append({"case_group_id": identity, "problem": 4, "protocol": "survey", "phase": "coverage",
                             "station_id": str(station), "x": float(station), "y": 0., "channel": channel,
                             "visible": visible, "outcome": "direction" if visible else "no_signal",
                             "cleared_before": False, "any_cleared_before": False, "existence_before": "unknown",
                             "sequence": len(rows)})
        return case, rows

    def test_joint_pattern_masks_and_case_denominator(self):
        case, rows = self.joint_fixture()
        feature = channel_visibility_patterns([case], rows)
        self.assertEqual(len(feature["channel_patterns"]), 20)
        summary = feature["case_summaries"][0]
        self.assertEqual(summary["visible_station_count_distribution"], [
            {"visible_stations": 0, "channels": 1, "probability": .5},
            {"visible_stations": 1, "channels": 0, "probability": 0.},
            {"visible_stations": 2, "channels": 1, "probability": .5}])
        self.assertEqual(feature["adjacent_pairs"][0]["both_visible_probability"], .5)
        self.assertEqual(feature["adjacent_pairs"][0]["discordant_probability"], 0.)
        case, rows = self.joint_fixture(missing=True)
        missing = channel_visibility_patterns([case], rows)
        self.assertIsNone(missing["channel_patterns"][19]["visibility"][1])
        self.assertIsNone(missing["case_summaries"][0]["visible_station_count_distribution"])
        self.assertEqual(missing["adjacent_pairs"], [])

    def test_equal_station_marginals_do_not_hide_joint_difference(self):
        cases, measurements = [], []
        for source in ("official", "local"):
            for i in range(12):
                case, rows = self.joint_fixture(f"{source}-{i}", source, crossed=source == "local")
                cases.append(case)
                measurements.extend(rows)
        checks = mechanism_checks(cases, measurements, [], [], visibility_matrices(measurements), repeats=20)
        report = checks["candidate_checks"][0]
        self.assertTrue(report["screen_flag"])
        self.assertFalse(report["joint_visibility_evidence_gate"])
        self.assertNotEqual(report["status"], "matched")
        self.assertTrue(any("discordant_probability" in name for name in report["screen_flag_metrics"]))
        self.assertTrue(all(c["status"] == "matched" for c in report["metrics"] if c["metric"].startswith("coverage_station_")))

    def test_five_zero_or_one_case_rates_do_not_claim_equivalence(self):
        for value in (0., 1.):
            result = _compare_scalar([value] * 5, [value] * 80, metric="survey_no_signal_fraction", repeats=20)
            self.assertEqual(result["bootstrap_difference_ci95"], [0., 0.])
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertTrue(result["degenerate_case_rate_boundary"])
            self.assertGreater(result["difference_ci95"][1] - result["difference_ci95"][0], .1)

    def test_two_count_priors_do_not_double_weight_same_physical_mechanism(self):
        evidence = [{"id": prior + "__mixed_hash", "radius_mode": "mixed", "error_mode": "deterministic",
                     "correlation_length": 150., "status": "unresolved", "screen_flag": False,
                     "composition_model_checked": prior, "evidence_groups": []} for prior in ("smoothed_joint", "broad_joint")]
        handoff = distinct_mechanism_candidates(evidence)
        self.assertEqual(len(handoff), 1)
        self.assertEqual(len(handoff[0]["evidence_model_candidates"]), 2)
        self.assertEqual(handoff[0]["composition_models_checked"], ["broad_joint", "smoothed_joint"])

    def test_cost_screen_alone_cannot_reject_or_missing_joint_match(self):
        cases = []
        for source in ("official", "local"):
            for i in range(6):
                case, _ = self.joint_fixture(f"{source}-{i}", source)
                case["walk_distance_m"] = 100. if source == "official" else 10000.
                cases.append(case)
        report = mechanism_checks(cases, [], [], [], [], repeats=20)["candidate_checks"][0]
        self.assertTrue(report["screen_flag"])
        self.assertEqual(report["status"], "insufficient_evidence")
        self.assertFalse(report["joint_visibility_evidence_gate"])


if __name__ == "__main__":
    unittest.main()
