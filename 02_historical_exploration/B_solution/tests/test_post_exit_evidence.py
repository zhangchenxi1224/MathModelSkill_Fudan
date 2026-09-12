import importlib.util
from pathlib import Path
import unittest

P = Path(__file__).resolve().parents[1] / "scripts/audit_post_exit_evidence.py"
S = importlib.util.spec_from_file_location("public_post_exit_evidence", P)
audit = importlib.util.module_from_spec(S)
S.loader.exec_module(audit)


class PublicEvidenceTests(unittest.TestCase):
    def test_public_ui_counts_require_summary_dialog_and_metadata_agreement(self):
        names = ["测试已结束", "CASE", "practice-p4-1-CASE.jlog", "共", "12", "个， 全向", "8", "个， 定向", "4", "个",
                 "机器狗已发送 /exit 指令。本次案例含干扰源12个，其中全向8个、定向4个。"]
        snapshot = {"items": [{"name": n} for n in names]}
        metadata = {"source_total_post_exit": 12, "omnidirectional_total_post_exit": 8, "directional_total_post_exit": 4,
                    "case_code": "CASE", "original_log_filename": "practice-p4-1-CASE.jlog", "cleared": 12, "status": "complete", "clear_fraction": 1.}
        self.assertEqual(audit.verify_ui(snapshot, metadata, 4, {"case_code": "CASE"})["issues"], [])
        metadata["directional_total_post_exit"] = 5
        self.assertIn("public_ui_audit_field_mismatch:directional_total_post_exit", audit.verify_ui(snapshot, metadata, 4, {"case_code": "CASE"})["issues"])

    def test_sixteen_confirmed_sources_legally_stop_without_full_coverage(self):
        requests = [{"path": "/enter", "request": {"request_id": "enter"}, "response": {"accepted": True}},
                    {"path": "/measure", "request": {"request_id": "m", "channel": 1, "position": {"x": 0., "y": 0.}}, "response": {"accepted": True, "measure_result": "near"}}]
        for channel in range(1, 17):
            requests.append({"path": "/clear", "request": {"request_id": f"c{channel}", "channel": channel}, "response": {"accepted": True, "clear_result": "success"}})
        requests.append({"path": "/exit", "request": {"request_id": "exit"}, "response": {"accepted": True}})
        result = {"stop_evidence": {"type": "known_upper_bound", "cleared_channels": list(range(1, 17))}}
        self.assertEqual(audit.verify_stop(result, [], requests, 4)["issues"], [])
        requests.pop(-2)
        self.assertIn("known_upper_bound_stop_without_sixteen_distinct_successes", audit.verify_stop(result, [], requests, 4)["issues"])

    def test_claimed_coverage_requires_actual_negative_at_every_station(self):
        points = [[float(i), 0.] for i in range(7)]
        certificate = {"type": "per_channel_coverage", "points": points, "required_point_count": 7,
                       "cleared_channels": [], "absent_channels": list(range(1, 21)),
                       "checked_indices": {str(c): list(range(7)) for c in range(1, 21)}}
        result = audit.verify_stop({"stop_evidence": certificate}, [{"event": "start", "coverage_points": points}], [], 3)
        self.assertIn("stopping_negative_measurement_missing:1:0", result["issues"])
