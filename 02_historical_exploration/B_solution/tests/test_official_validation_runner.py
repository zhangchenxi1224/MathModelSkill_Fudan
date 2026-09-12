"""Frozen practice dispatch tests: mocked UI/locks, only local robot transport."""
from __future__ import annotations

from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from bsolver.protocol import RobotClient
from bsolver.simulator import LocalSimulator, Source

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("official_validation_test_module", ROOT/"scripts/run_official_validation.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


class FakeLock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeCollector:
    def __init__(self, *, failure=False, set_stop=False):
        self.locks, self.calls, self.statuses = [], [], []
        self.started, self.closed = False, False
        self.failure, self.set_stop = failure, set_stop

    def collection_lock(self, path):
        lock = FakeLock()
        self.locks.append(lock)
        return lock

    def status(self, out, state, **details):
        self.statuses.append((state, details))

    def start_bridge(self):
        self.started = True

    def close_bridge(self):
        self.closed = True

    def collect(self, plan, out, robot_id, base_url, maximum, adopt=False, policy_runner=None):
        case = plan["cases"][0]
        self.calls.append(case["case_id"])
        assert maximum == 1 and adopt is False
        folder = out/"cases"/case["case_id"]
        if not (folder/"result.json").exists():
            policy_runner(case, folder, robot_id, base_url)
        write(folder/"post_exit_audit.json", {"case_id": case["case_id"], "problem": case["problem"],
              "protocol": case["arm"], "status": "incomplete" if self.failure else "complete",
              "source_total_post_exit": 13, "cleared": 12 if self.failure else 13})
        if self.set_stop:
            (out/"STOP_AFTER_CASE").touch()


class ValidationRunnerTests(unittest.TestCase):
    def fixture(self, base, *, ready=True):
        base = Path(base)
        out = base/"round2"
        original_freeze = base/"round1"/"baseline_freeze.json"
        write(original_freeze, {"source_sha256": validation.CORE_SHA256,
                               "files": {f"src/bsolver/{name}": validation.sha256(ROOT/"src/bsolver"/name)
                                         for name in validation.CORE_NAMES}})
        candidate_path = base/"candidate.json"
        candidate = {"problems": {"3": {"local_measure_limit": 2},
                      "4": {"solver_class": "nosignal", "solver_config": {"local_measure_limit": 2},
                            "nosignal_config": {"directional_prior": .4}}},
                     "dependency_hashes": {"src/bsolver/nosignal_sensing.py": validation.sha256(ROOT/"src/bsolver/nosignal_sensing.py")}}
        write(candidate_path, candidate)
        cases = []
        for within in range(40):
            for problem in (3, 4):
                arm = "baseline" if within % 4 < 2 else "candidate"
                case = {"case_id": f"r2-p{problem}-validation-{within+1:03d}", "problem": problem,
                        "protocol": arm, "arm": arm, "block": within//4,
                        "sequence_within_problem": within+1, "sequence": len(cases), "split": "validation",
                        "environment": "official_practice", "round": 2, "pilot": False,
                        "policy_spec": {"sensing": "active", "local_measure_limit": 5, "scheduling": "joint"}
                            if arm == "baseline" else candidate["problems"][str(problem)]}
                cases.append(case)
                write(out/"cases"/case["case_id"]/"assignment.json", case)
        plan = {"cases": cases, "sample_sizes": {str(p): {"planned_total": 40, "planned_per_arm": 20} for p in (3, 4)},
                "candidate_file": str(candidate_path), "candidate_sha256": validation.sha256(candidate_path),
                "practice_authorized_by_round_plan": True, "formal_authorized": False, "dispatch_ready": ready}
        write(out/"plan.json", plan)
        return out, original_freeze, candidate_path, plan

    def test_preflight_preserves_existing_plan_and_assignments(self):
        with tempfile.TemporaryDirectory() as temp:
            out, freeze, candidate, plan = self.fixture(temp)
            original = (out/"plan.json").read_bytes()
            checked = validation.preflight(out, freeze)
            self.assertEqual(checked["manifest"]["case_count"], 80)
            self.assertEqual((out/"plan.json").read_bytes(), original)
            self.assertTrue((out/"validation_freeze.json").exists())
            self.assertEqual(validation.preflight(out, freeze)["manifest"], checked["manifest"])
            self.assertEqual(validation.read_object(out/"cases"/plan["cases"][0]["case_id"]/"assignment.json"), plan["cases"][0])

    def test_design_only_preflight_cannot_start_or_lock_plan_before_readiness(self):
        with tempfile.TemporaryDirectory() as temp:
            out, freeze, _, plan = self.fixture(temp, ready=False)
            validation.preflight(out, freeze)
            self.assertFalse((out/"validation_freeze.json").exists())
            fake = FakeCollector()
            with self.assertRaisesRegex(ValueError, "design-only"):
                validation.run_frozen_plan(out, freeze, "fixture", collect_module=fake)
            self.assertFalse(fake.started)
            plan["dispatch_ready"] = True
            write(out/"plan.json", plan)
            validation.preflight(out, freeze)
            self.assertTrue((out/"validation_freeze.json").exists())

    def test_formal_or_changed_assignment_is_rejected_before_ui(self):
        for mutation in ("formal", "assignment", "baseline"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                out, freeze, _, plan = self.fixture(temp)
                if mutation == "formal":
                    plan["formal_authorized"] = True
                    write(out/"plan.json", plan)
                elif mutation == "assignment":
                    changed = dict(plan["cases"][0], arm="candidate")
                    write(out/"cases"/plan["cases"][0]["case_id"]/"assignment.json", changed)
                else:
                    plan["cases"][0]["policy_spec"]["local_measure_limit"] = 2
                    write(out/"plan.json", plan)
                    write(out/"cases"/plan["cases"][0]["case_id"]/"assignment.json", plan["cases"][0])
                fake = FakeCollector()
                with self.assertRaises(ValueError):
                    validation.run_frozen_plan(out, freeze, "fixture", collect_module=fake)
                self.assertFalse(fake.started)

    def test_changed_candidate_file_or_dependency_hash_is_rejected(self):
        for change_hash in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                out, freeze, candidate, plan = self.fixture(temp)
                content = validation.read_object(candidate)
                content["dependency_hashes"]["src/bsolver/nosignal_sensing.py"] = "0"*64
                write(candidate, content)
                if change_hash:
                    plan["candidate_sha256"] = validation.sha256(candidate)
                    write(out/"plan.json", plan)
                with self.assertRaisesRegex(ValueError, "SHA-256|dependency changed"):
                    validation.preflight(out, freeze)

    def test_resume_and_stop_flag_reuse_collector_without_reallocation(self):
        with tempfile.TemporaryDirectory() as temp:
            out, freeze, _, plan = self.fixture(temp)
            write(out/"cases"/plan["cases"][0]["case_id"]/"post_exit_audit.json", {"status": "complete"})
            fake = FakeCollector(set_stop=True)
            invoked = []
            def fake_policy(case, folder, robot_id, url):
                invoked.append(case["case_id"])
                write(folder/"result.json", {"status": "complete", "clear_successes": 13})
            result = validation.run_frozen_plan(out, freeze, "fixture", maximum=5,
                                                collect_module=fake, policy_runner=fake_policy)
            self.assertEqual(invoked, [plan["cases"][1]["case_id"]])
            self.assertEqual(result["new_cases"], 1)
            self.assertTrue(fake.started and fake.closed)
            self.assertEqual(len(fake.locks), 2)
            self.assertTrue(all(lock.closed for lock in fake.locks))

    def test_failed_attempt_is_retained_and_pauses_next_dispatch(self):
        with tempfile.TemporaryDirectory() as temp:
            out, freeze, _, plan = self.fixture(temp)
            fake = FakeCollector(failure=True)
            def fake_policy(case, folder, robot_id, url):
                write(folder/"result.json", {"status": "incomplete", "clear_successes": 12})
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                validation.run_frozen_plan(out, freeze, "fixture", collect_module=fake, policy_runner=fake_policy)
            self.assertEqual(fake.calls, [plan["cases"][0]["case_id"]])
            self.assertTrue((out/"cases"/fake.calls[0]/"post_exit_audit.json").exists())
            self.assertEqual(fake.statuses[-1][0], "needs_attention")
            self.assertTrue(fake.closed)

    def test_existing_result_is_audited_without_reexecuting_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            out, freeze, _, plan = self.fixture(temp)
            first = out/"cases"/plan["cases"][0]["case_id"]
            write(first/"result.json", {"status": "complete", "clear_successes": 13})
            fake = FakeCollector()
            invoked = []
            def fake_policy(case, folder, robot_id, url):
                invoked.append(case["case_id"])
                write(folder/"result.json", {"status": "complete", "clear_successes": 13})
            result = validation.run_frozen_plan(out, freeze, "fixture", maximum=1,
                                                collect_module=fake, policy_runner=fake_policy)
            self.assertEqual(invoked, [plan["cases"][1]["case_id"]])
            self.assertEqual(result["new_cases"], 1)
            self.assertTrue((first/"post_exit_audit.json").exists())

    def test_policy_instantiation_uses_only_local_transport_and_original_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            for arm, problem, policy in (("baseline", 3, {"local_measure_limit": 5}),
                    ("candidate", 4, {"solver_class": "nosignal", "solver_config": {"local_measure_limit": 1}})):
                sources = [Source(i+1, (0., 0.)) for i in range(10)]
                env = LocalSimulator(sources, enforce_case_size=True)
                client = RobotClient(transport=lambda path, body: env(path, body), retry_delay=0)
                case = {"case_id": arm, "arm": arm, "problem": problem, "policy_spec": policy}
                folder = Path(temp)/arm
                folder.mkdir()
                with patch("bsolver.protocol.HTTPTransport.__init__", side_effect=AssertionError("no official HTTP in tests")):
                    result = validation.run_policy(case, folder, "fixture", "http://invalid", client_factory=lambda: client)
                self.assertEqual(result["status"], "complete", result["error"])
                self.assertTrue(env.summary()["all_cleared"])
                self.assertIsNone(result["source_total"])
                if arm == "baseline":
                    self.assertEqual(result["config"], asdict(validation.original_baseline(3)))
                else:
                    self.assertEqual(result["sensing_variant"], "sampled_nosignal_v1")

    def test_independent_bootstrap_is_not_paired(self):
        result = validation.independent_bootstrap([0., 100.], [20., 80.], seed=1, repetitions=4000)
        self.assertEqual(result["difference"], 0.)
        self.assertGreater(result["ci95"][1]-result["ci95"][0], 100.,
                           "paired differences [-20,20] would incorrectly produce a narrow CI")
        constant = validation.independent_bootstrap([10., 10.], [30., 30.], repetitions=100)
        self.assertEqual(constant["ci95"], [20., 20.])

    def test_summary_keeps_failures_pending_and_component_decomposition(self):
        with tempfile.TemporaryDirectory() as temp:
            out, _, _, plan = self.fixture(temp)
            arms = {arm: [case for case in plan["cases"] if case["problem"] == 3 and case["arm"] == arm]
                    for arm in ("baseline", "candidate")}
            for arm in arms:
                for i, case in enumerate(arms[arm][:2]):
                    failed = arm == "candidate" and i == 1
                    folder = out/"cases"/case["case_id"]
                    total = 100.+i*100+(10. if arm == "candidate" else 0.)
                    write(folder/"result.json", {"case_id": case["case_id"], "problem": 3, "protocol": arm,
                        "status": "incomplete" if failed else "complete", "clear_successes": 12 if failed else 13,
                        "total_virtual_time_s": total, "walk_distance_m": 100., "switches": 3,
                        "measures": 4, "clear_attempts": 15})
                    write(folder/"post_exit_audit.json", {"case_id": case["case_id"], "problem": 3, "protocol": arm,
                        "status": "incomplete" if failed else "complete", "source_total_post_exit": 13,
                        "cleared": 12 if failed else 13, "total_virtual_time_s": total})
            report = validation.summarize(out, repetitions=100)
            p3, p4 = report["problems"]["3"], report["problems"]["4"]
            self.assertEqual(p3["arms"]["candidate"]["failure_rate"], .5)
            self.assertEqual(p3["arms"]["candidate"]["not_started"], 18)
            self.assertEqual(p3["arms"]["candidate"]["penalized_loss_mean_s"], (110.+360000.)/2)
            self.assertEqual(p3["arms"]["baseline"]["component_means_s"]["movement_s"], 20.)
            self.assertEqual(p4["arms"]["candidate"]["attempted"], 0)
            self.assertIsNone(p4["candidate_minus_baseline"]["penalized_loss_s"]["ci95"])
            self.assertTrue((out/"validation_summary.md").exists())

    def test_help_does_not_start_any_ui_or_http(self):
        with patch.object(validation.collector, "start_bridge", side_effect=AssertionError("no UI")):
            with self.assertRaises(SystemExit) as exited:
                validation.main(["--help"])
            self.assertEqual(exited.exception.code, 0)

    def test_actual_prepare_script_output_passes_offline_preflight(self):
        prepare_spec = importlib.util.spec_from_file_location("real_validation_prepare_test", ROOT/"scripts/prepare_official_validation.py")
        prepare = importlib.util.module_from_spec(prepare_spec)
        prepare_spec.loader.exec_module(prepare)
        with tempfile.TemporaryDirectory() as temp:
            _, freeze, candidate, _ = self.fixture(temp)
            collection = Path(temp)/"prior"
            for problem in (3, 4):
                for i in range(12):
                    write(collection/"cases"/f"p{problem}-{i}"/"post_exit_audit.json",
                          {"protocol": "baseline", "problem": problem, "status": "complete",
                           "total_virtual_time_s": 10000.+i*50.})
            output = Path(temp)/"actual-prepared"
            arguments = ["prepare", "--collection", str(collection), "--candidate", str(candidate), "--output", str(output)]
            with patch.object(sys, "argv", arguments):
                prepare.main()
            checked = validation.preflight(output, freeze)
            self.assertEqual(checked["manifest"]["case_count"], 80)
            self.assertFalse(checked["plan"]["dispatch_ready"])
            self.assertFalse((output/"validation_freeze.json").exists())


if __name__ == "__main__":
    unittest.main()
