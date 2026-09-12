"""Evidence-preserving archive tests, including non-identifiability examples."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from bsolver.geometry import contains, distance
from bsolver.knowledge import ChannelKnowledge
from bsolver.posterior_archive import archive_requests, build_archive, compatible_candidate
from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source, generate_sources
from bsolver.strategy import SolverConfig
from bsolver.survey import SurveySolver


def action(identity, path, channel=1, point=(0., 0.), *, result=None, angle=None):
    request = {"request_id": identity, "robot_id": "fixture", "arena_id": "default"}
    response = {"accepted": True, "virtual_time_s": 100., "real_timestamp_ms": 1700000000000}
    if path in ("/measure", "/clear"):
        request.update(channel=channel, position={"x": point[0], "y": point[1]})
    if path == "/measure":
        response["measure_result"] = result
        if result == "direction":
            response["svd_deg"] = angle
    if path == "/clear":
        response["clear_result"] = result
    if path == "/exit":
        response["exit_reason"] = "user_exit"
    return {"event": "request", "outcome": "accepted", "path": path,
            "request": request, "response": response}


class ArchiveTests(unittest.TestCase):
    def test_clear_success_is_twenty_meter_disk_not_ground_truth(self):
        archive = build_archive([action("c1", "/clear", point=(100., 0.), result="success")], problem=4)
        state = archive["channels"]["1"]
        self.assertFalse(state["clear_points_are_ground_truth"])
        self.assertIsNone(state["position_point_estimate"])
        for g in ((100., 0.), (119., 0.), (85., 5.)):
            self.assertTrue(contains(state["position_outer_polygon"], g, tol=1e-5))
            self.assertTrue(compatible_candidate(state, g, 1000, 0))
        self.assertFalse(compatible_candidate(state, (121., 0.), 1000, 0))
        self.assertEqual(state["orientation_constraints"]["positive_halfplanes"], [])

    def test_q4_no_signal_does_not_imply_outside_receive_radius(self):
        rows = [action("m1", "/measure", point=(-100., 0.), result="no_signal"),
                action("m2", "/measure", point=(100., 0.), result="direction", angle=180.)]
        archive = build_archive(rows, problem=4)
        state = archive["channels"]["1"]
        self.assertTrue(compatible_candidate(state, (0., 0.), 1000., 0.))
        self.assertFalse(compatible_candidate(state, (0., 0.), 1000., None))
        self.assertTrue(contains(state["position_outer_polygon"], (0., 0.)))
        self.assertFalse(state["negative_regions_removed_from_convex_hull"])
        self.assertIn(" OR ", state["orientation_constraints"]["negative_disjunctions"][0]["inequality"])

    def test_q3_no_signal_is_strict_radius_bound_with_one_shared_radius(self):
        rows = [action("m1", "/measure", point=(1000., 0.), result="direction", angle=180.),
                action("m2", "/measure", point=(1100., 0.), result="no_signal")]
        state = build_archive(rows, problem=3)["channels"]["1"]
        self.assertTrue(compatible_candidate(state, (0., 0.), 1050.))
        self.assertFalse(compatible_candidate(state, (0., 0.), 1100.))
        self.assertFalse(compatible_candidate(state, (0., 0.), 1200.))
        self.assertIn("R<min", state["radius_constraints"]["negative_upper_function"])

    def test_ndir_count_does_not_identify_individual_channel_types(self):
        rows = [action(f"m{channel}", "/measure", channel, (0., 0.), result="direction", angle=0.)
                for channel in (1, 2)]
        archive = build_archive(rows, problem=4, post_exit_counts={"N": 2, "Ndir": 1})
        self.assertFalse(archive["counts_identify_per_channel_types"])
        self.assertEqual(len(archive["global_count_constraints"]), 2)
        # Either channel can be the sole directional source; the aggregate
        # Ndir=1 cannot decide which of these two assignments is correct.
        for orientation1, orientation2 in ((None, 180.), (180., None)):
            self.assertTrue(compatible_candidate(archive["channels"]["1"], (100., 0.), 1000., orientation1))
            self.assertTrue(compatible_candidate(archive["channels"]["2"], (200., 0.), 1000., orientation2))
        for state in (archive["channels"]["1"], archive["channels"]["2"]):
            self.assertEqual(state["type_hypotheses"], ["omnidirectional", "directional"])
            self.assertIsNone(state["orientation_constraints"]["direction_point_estimate_deg"])

    def test_nonempty_outer_hull_does_not_certify_joint_fixed_direction(self):
        # Near fixes g at an at-most-5m disk. Four dark observations 100m away
        # cannot all lie in the rear open half-plane of any one unit direction.
        rows = [action("n", "/measure", result="near")]
        for j, p in enumerate(((100., 0.), (0., 100.), (-100., 0.), (0., -100.))):
            rows.append(action(f"d{j}", "/measure", point=p, result="no_signal"))
        archive = build_archive(rows, problem=4)
        state = archive["channels"]["1"]
        self.assertFalse(state["position_outer_empty"])
        self.assertEqual(state["joint_feasibility"], "not_certified")
        self.assertFalse(archive["nonempty_outer_hull_implies_joint_feasible"])
        self.assertFalse(compatible_candidate(state, (0., 0.), 1000., None))
        for direction in range(360):
            self.assertFalse(compatible_candidate(state, (0., 0.), 1000., direction))

    def test_after_clear_no_signal_and_failed_clear_do_not_restrict_original_source(self):
        rows = [action("m1", "/measure", result="near"),
                action("c1", "/clear", result="success"),
                action("m2", "/measure", result="no_signal"),
                action("c2", "/clear", result="no_target_in_range")]
        state = build_archive(rows, problem=3)["channels"]["1"]
        self.assertEqual(len(state["post_removal_actions"]), 2)
        self.assertEqual(len(state["active_observations"]), 1)
        self.assertEqual(len(state["active_clear_constraints"]), 1)
        self.assertTrue(compatible_candidate(state, (3., 0.), 1000.))
        self.assertEqual(state["radius_constraints"]["negative_positions"], [])

    def test_failure_hole_is_retained_symbolically(self):
        state = build_archive([action("c1", "/clear", result="no_target_in_range")], problem=3)["channels"]["1"]
        self.assertTrue(contains(state["position_outer_polygon"], (0., 0.)))
        self.assertFalse(compatible_candidate(state, (0., 0.), 1000.))
        self.assertTrue(compatible_candidate(state, (30., 0.), 1000.))
        self.assertTrue(compatible_candidate(state, (0., 0.), 1000., exists=False))

    def test_unique_accepted_ids_not_retries_or_rejections(self):
        accepted = action("m1", "/measure", result="near")
        timeout = {"path": "/measure", "request": copy.deepcopy(accepted["request"]),
                   "outcome": "transport_error"}
        rejected = action("m2", "/measure", result="direction", angle=90.)
        rejected["response"]["accepted"] = False
        rejected["outcome"] = "rejected"
        archive = build_archive([timeout, accepted, copy.deepcopy(accepted), rejected], problem=3)
        self.assertEqual(archive["unique_accepted_row_count"], 1)
        self.assertEqual(len(archive["channels"]["1"]["active_observations"]), 1)
        self.assertEqual(archive["status"], "archived")

    def test_conflicting_accepted_id_and_same_point_changes_raise_audit_issues(self):
        first = action("m1", "/measure", result="near")
        conflict = action("m1", "/measure", point=(100., 0.), result="direction", angle=180.)
        archive = build_archive([first, conflict], problem=3)
        self.assertEqual(archive["status"], "audit_issues")
        changed = action("m2", "/measure", result="no_signal")
        archive = build_archive([first, changed], problem=3)
        self.assertEqual(archive["status"], "audit_issues")
        self.assertFalse(compatible_candidate(archive["channels"]["1"], (0., 0.), 1000.))

    def test_radius_lower_function_range_contains_real_lower_requirement(self):
        rows = [action("m1", "/measure", point=(1200., 0.), result="direction", angle=180.),
                action("c1", "/clear", point=(0., 0.), result="success")]
        state = build_archive(rows, problem=3)["channels"]["1"]
        lower, upper = state["radius_constraints"]["lower_function_range_over_outer_hull_m"]
        self.assertGreater(lower, 1179.)
        self.assertLess(lower, 1180.01)
        self.assertGreaterEqual(upper, 1220.)
        for g in ((0., 0.), (10., 0.), (-10., 0.)):
            requirement = max(1000., distance(g, (1200., 0.)))
            self.assertLessEqual(lower, requirement)
            self.assertGreaterEqual(upper, requirement)
        self.assertIsNone(state["radius_constraints"]["radius_point_estimate"])

    def test_local_survey_truth_survives_archive_and_original_knowledge(self):
        sources = generate_sources(557104, count=10, directional_fraction=.7)
        env = LocalSimulator(sources, seed=917, error_mode="extreme", enforce_case_size=True)
        client = RobotClient(transport=env, retry_delay=0)
        solver = SurveySolver(client, SolverConfig(problem=4, local_measure_limit=0), survey_seed=6181)
        result = solver.run()
        self.assertEqual(result["status"], "complete", result["error"])
        archive = build_archive(client.history, problem=4,
                                post_exit_counts={"N": 10, "Ndir": sum(s.direction_deg is not None for s in sources)})
        self.assertEqual(archive["status"], "archived", archive["issues"])
        self.assertTrue(archive["exit_confirmed"])
        for source in sources:
            state = archive["channels"][str(source.channel)]
            self.assertTrue(contains(state["position_outer_polygon"], source.position, tol=1e-5))
            self.assertTrue(compatible_candidate(state, source.position, source.radius, source.direction_deg))
            self.assertTrue(solver.channels[source.channel].compatible_hidden_state(
                source.position, source.radius, source.direction_deg))

    def test_cli_reads_only_public_files_and_writes_case_archive(self):
        script = Path(__file__).resolve().parents[1]/"scripts"/"archive_posteriors.py"
        spec = importlib.util.spec_from_file_location("archive_cli_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)/"case-a"
            case.mkdir()
            request_path = case/"requests.jsonl"
            rows = [action("m1", "/measure", result="near"), action("c1", "/clear", result="success")]
            request_path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            (case/"assignment.json").write_text(json.dumps({"case_id": "case-a", "problem": 3}), encoding="utf-8")
            (case/"case.json").write_text("THIS HIDDEN FILE MUST NEVER BE READ", encoding="utf-8")
            output = Path(tmp)/"out"
            self.assertEqual(module.main(["--input", str(case), "--output", str(output)]), 0)
            written = json.loads((output/"case-a"/"posterior_archive.json").read_text(encoding="utf-8"))
            self.assertEqual(written["case_id"], "case-a")
            self.assertEqual(len(written["requests_sha256"]), 64)
            self.assertEqual(written["channels"]["1"]["existence"], "confirmed_present")

    def test_multiple_cases_must_not_be_merged(self):
        with self.assertRaisesRegex(ValueError, "multiple accepted enter"):
            build_archive([action("e1", "/enter"), action("e2", "/enter")], problem=3)

    def test_cli_supports_actual_collector_count_schema_without_defaulting_unknown_dir(self):
        script = Path(__file__).resolve().parents[1]/"scripts"/"archive_posteriors.py"
        spec = importlib.util.spec_from_file_location("archive_count_cli_test", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            for suffix, ndir in (("known", 4), ("unknown", None)):
                case = Path(tmp)/suffix
                case.mkdir()
                (case/"assignment.json").write_text(json.dumps({"case_id": suffix, "problem": 4}), encoding="utf-8")
                (case/"requests.jsonl").write_text(json.dumps(action("m1", "/measure", result="near")), encoding="utf-8")
                (case/"post_exit_audit.json").write_text(json.dumps({
                    "source_total_post_exit": 13, "directional_total_post_exit": ndir}), encoding="utf-8")
                output = Path(tmp)/(suffix+"-archive")
                self.assertEqual(module.main(["--input", str(case), "--output", str(output)]), 0)
                archive = json.loads((output/suffix/"posterior_archive.json").read_text(encoding="utf-8"))
                self.assertEqual(archive["post_exit_counts"], {"N": 13, "Ndir": 4} if ndir is not None else {"N": 13})
                self.assertEqual(len(archive["global_count_constraints"]), 2 if ndir is not None else 1)


if __name__ == "__main__":
    unittest.main()
