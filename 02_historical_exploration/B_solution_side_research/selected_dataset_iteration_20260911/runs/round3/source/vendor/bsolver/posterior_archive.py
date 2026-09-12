"""Offline set-valued archive of public observations, not a fitted posterior.

No simulator state or true positions/radii/orientations are accepted. A nonempty
convex outer hull is only a necessary position bound: it does not certify joint
feasibility of one fixed source position, radius, type and direction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from .calibration import unique_accepted_actions
from .geometry import (bearing_deg, clip_bearing, disk_outer_polygon, distance,
                       distance_to_polygon, max_distance, wrap_angle_deg)
from .knowledge import intersect_outer_disk
from .protocol import normalize_channel, normalize_position


ARCHIVE_VERSION = "public-feasible-set-v1"


def read_request_rows(path):
    rows = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"request log line {line_no} is not an object")
        rows.append(row)
    return rows


def _new_channel(channel):
    return {"channel": channel, "position_outer_polygon": disk_outer_polygon((0., 0.), 1800., 128),
            "existence": "not_confirmed", "cleared": False,
            "active_observations": [], "active_clear_constraints": [],
            "actions": [], "post_removal_actions": [], "issues": []}


def _radius_archive(hull, positives, negatives, problem):
    # min_g max_i distance(g,p_i) >= max_i min_g distance(g,p_i).
    # The lower number is a relaxed bound, not an optimizer's exact value.
    guard = 1e-5
    lower = (max([1000.] + [max(0., distance_to_polygon(hull, p)-guard)
                           for p in positives]) if hull else None)
    upper = (max([1000.] + [max_distance(hull, p)+guard for p in positives])
             if hull else None)
    return {
        "shared_parameter": "one fixed R per original source, in [1000,1500] m",
        "lower_function": "L(g)=max(1000,max_{p in positive_positions} ||g-p||); R>=L(g)",
        "positive_positions": copy.deepcopy(positives),
        "lower_function_range_over_outer_hull_m": [lower, upper],
        "range_meaning": "conservative bounds on L(g), not a fitted R interval or exact extrema",
        "necessary_global_radius_interval_m": [lower, 1500.] if lower is not None else None,
        "necessary_interval_empty": lower is not None and lower > 1500.,
        "negative_positions": copy.deepcopy(negatives),
        "negative_upper_function": ("R<min_{p in no_signal_positions} ||g-p|| (if nonempty); R<=1500"
            if problem == 3 else
            "R<=1500; each no_signal gives ||g-p||>R OR (directional AND (p-g).u<0)"),
        "strict_negative_boundary": True,
        "radius_point_estimate": None,
    }


def build_archive(request_rows, *, problem, case_id=None, epsilon_deg=1.0051,
                  post_exit_counts=None):
    """Archive one case from accepted request rows and optional public counts.

    ``post_exit_counts`` may contain only public aggregate N/Ndir. Counts remain
    case-level constraints; they never label individual channels or identify g.
    Malformed/conflicting evidence is retained as issues, never called feasible.
    """
    if problem not in (3, 4):
        raise ValueError("problem must be 3 or 4")
    if not math.isfinite(epsilon_deg) or not 1.0051 <= epsilon_deg <= 1.06:
        raise ValueError("epsilon_deg must lie in [1.0051,1.06]")
    counts = {}
    for key, value in (post_exit_counts or {}).items():
        if key not in ("N", "Ndir"):
            raise ValueError("only public post-exit aggregate N and Ndir are accepted")
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 20:
            raise ValueError("aggregate counts must be integers in [0,20]")
        counts[key] = value
    if ("N" in counts and counts.get("Ndir", 0) > counts["N"]) or (problem == 3 and counts.get("Ndir", 0)):
        raise ValueError("inconsistent public aggregate counts")
    rows = list(request_rows)
    issues = []
    channels = {channel: _new_channel(channel) for channel in range(1, 21)}
    unique = list(unique_accepted_actions(rows, issues))
    enters = sum(row.get("path") == "/enter" for _, row in unique)
    if enters > 1:
        raise ValueError("multiple accepted enter actions: do not merge different cases")
    exit_confirmed = False
    applied = 0
    action_digests = {}
    for sequence, row in unique:
        if row.get("outcome") not in (None, "accepted"):
            issues.append(f"row {sequence}: response not client-confirmed; excluded from geometry")
            continue
        path, request, response = row.get("path"), row.get("request", {}), row.get("response", {})
        request_id = request.get("request_id")
        if path == "/exit":
            exit_confirmed = True
            continue
        if path == "/enter":
            continue
        if path not in ("/measure", "/clear"):
            issues.append(f"row {sequence}: unknown accepted path")
            continue
        try:
            channel = normalize_channel(request.get("channel"))
            point = normalize_position(request.get("position"))
        except (ValueError, TypeError, KeyError) as exc:
            issues.append(f"row {sequence}: malformed accepted action: {type(exc).__name__}")
            continue
        if exit_confirmed:
            issues.append(f"row {sequence}: action after confirmed exit; possible mixed log")
        state = channels[channel]
        entry = {"row_index": sequence, "request_id": request_id, "path": path,
                 "position": point, "response": copy.deepcopy(response),
                 "active_before_action": not state["cleared"]}
        state["actions"].append(entry)
        applied += 1
        action_digests[str(request_id)] = hashlib.sha256(json.dumps(
            [path, request, response], sort_keys=True, allow_nan=False).encode()).hexdigest()
        if state["cleared"]:
            # After removal, no_signal and failed clear constrain the removed
            # state, not the original source's receive radius or orientation.
            state["post_removal_actions"].append(copy.deepcopy(entry))
            if ((path == "/measure" and response.get("measure_result") != "no_signal")
                    or (path == "/clear" and response.get("clear_result") == "success")):
                state["issues"].append("positive action after confirmed source removal")
            continue
        if path == "/measure":
            result = response.get("measure_result")
            if result not in ("direction", "near", "no_signal"):
                state["issues"].append("unknown measure_result")
                continue
            angle = response.get("svd_deg") if result == "direction" else None
            if result == "direction" and (isinstance(angle, bool) or not isinstance(angle, (int, float))
                    or not math.isfinite(angle) or not 0 <= angle < 360):
                state["issues"].append("invalid accepted direction angle")
                continue
            obs = {"position": point, "result": result, "angle": angle,
                   "request_id": request_id, "row_index": sequence}
            for old in state["active_observations"]:
                if old["position"] == point and (old["result"] != result or old["angle"] != angle):
                    state["issues"].append("fixed same-point observation changed before removal")
                    break
            state["active_observations"].append(obs)
            hull = state["position_outer_polygon"]
            if result in ("direction", "near"):
                state["existence"] = "confirmed_present"
                if result == "direction":
                    hull = clip_bearing(hull, point, angle, epsilon_deg)
                    hull = intersect_outer_disk(hull, point, 1500.)
                else:
                    hull = intersect_outer_disk(hull, point, 5.)
                state["position_outer_polygon"] = hull
        else:
            result = response.get("clear_result")
            if result not in ("success", "no_target_in_range"):
                state["issues"].append("unknown clear_result")
                continue
            constraint = {"position": point, "result": result, "request_id": request_id,
                          "row_index": sequence, "source_position_equals_clear_point": False}
            state["active_clear_constraints"].append(constraint)
            if result == "success":
                state["position_outer_polygon"] = intersect_outer_disk(
                    state["position_outer_polygon"], point, 20.)
                state["existence"] = "confirmed_present"
                state["cleared"] = True
    # The shared deduplicator checks payload identity. Also detect incompatible
    # accepted replies for the same ID, which are not additional observations.
    accepted_replies = {}
    for row in rows:
        if (row.get("response") or {}).get("accepted") is not True:
            continue
        identity = (row.get("request") or {}).get("request_id")
        if identity is None:
            continue
        reply = row["response"]
        if identity in accepted_replies and accepted_replies[identity] != reply:
            issues.append("same accepted request ID has conflicting response")
        accepted_replies.setdefault(identity, reply)
    for state in channels.values():
        obs = state["active_observations"]
        positives = [o["position"] for o in obs if o["result"] in ("direction", "near")]
        negatives = [o["position"] for o in obs if o["result"] == "no_signal"]
        state["position_constraints"] = {
            "prior": "||g||<=1800",
            "direction": [{"point": o["position"], "angle_deg": o["angle"],
                           "epsilon_deg": epsilon_deg,
                           "inequality": "5<||g-p||<=R<=1500 AND abs(wrap(bearing(p,g)-angle))<=epsilon"}
                          for o in obs if o["result"] == "direction"],
            "near": [{"point": o["position"], "inequality": "||g-p||<=5 AND visible(g,R,type,u,p)"}
                     for o in obs if o["result"] == "near"],
            "clear_success": [{"point": c["position"], "inequality": "||g-p||<=20"}
                              for c in state["active_clear_constraints"] if c["result"] == "success"],
            "clear_failure": [{"point": c["position"], "inequality": "if source exists: ||g-p||>20"}
                              for c in state["active_clear_constraints"] if c["result"] != "success"],
        }
        state["radius_constraints"] = _radius_archive(
            state["position_outer_polygon"], positives, negatives, problem)
        state["type_hypotheses"] = ["omnidirectional"] if problem == 3 else ["omnidirectional", "directional"]
        state["orientation_constraints"] = {
            "parameter": "one shared fixed u=(cos(phi),sin(phi)), ||u||=1, if directional",
            "positive_halfplanes": ([{"point": p, "normal_expression": "p-g",
                                     "inequality": "(p-g).u>=0", "applies_if": "directional"}
                                    for p in positives] if problem == 4 else []),
            "negative_disjunctions": [
                {"point": p, "inequality": ("||g-p||>R" if problem == 3 else
                 "||g-p||>R OR (directional AND (p-g).u<0)")}
                for p in negatives],
            "clear_actions_constrain_orientation": False,
            "direction_point_estimate_deg": None,
        }
        state["position_outer_empty"] = not bool(state["position_outer_polygon"])
        state["joint_feasibility"] = ("contradicted_by_empty_outer_hull" if state["position_outer_empty"]
                                      else "not_certified")
        state["position_point_estimate"] = None
        state["clear_points_are_ground_truth"] = False
        state["negative_regions_removed_from_convex_hull"] = False
        issues.extend(f"channel {state['channel']}: {issue}" for issue in state["issues"])
    constraints = []
    if "N" in counts:
        constraints.append({"expression": "sum_{f=1..20} existence_f=N", "value": counts["N"]})
    if "Ndir" in counts:
        constraints.append({"expression": "sum_{f=1..20} existence_f*directional_f=Ndir",
                            "value": counts["Ndir"]})
    return {
        "archive_version": ARCHIVE_VERSION, "case_id": case_id, "problem": problem,
        "epsilon_deg": epsilon_deg, "source": "public accepted requests only",
        "interpretation": "necessary convex position bounds plus an unrelaxed symbolic ledger; no probability model",
        "joint_feasibility_certified": False,
        "nonempty_outer_hull_implies_joint_feasible": False,
        "status": "audit_issues" if issues else "archived",
        "issues": issues, "input_row_count": len(rows),
        "unique_accepted_row_count": len(unique), "applied_measure_clear_count": applied,
        "accepted_enter_count": enters, "exit_confirmed": exit_confirmed,
        "post_exit_counts": counts, "global_count_constraints": constraints,
        "counts_identify_per_channel_types": False,
        "channels": {str(k): value for k, value in channels.items()},
        "confirmed_action_digests": action_digests,
    }


def compatible_candidate(channel_archive, g, radius, direction_deg=None, *, exists=True,
                         epsilon_deg=1.0051, tol=1e-8):
    """Evaluate one fixed hypothetical original source; for offline tests only.

    This is not an optimizer or existence proof. Global N/Ndir coupling must
    additionally hold across channels. An omitted direction means omnidirectional.
    """
    state = channel_archive
    if not exists:
        return state["existence"] != "confirmed_present"
    if not 1000 <= radius <= 1500 or distance(g, (0., 0.)) > 1800+tol:
        return False
    if direction_deg is not None and "directional" not in state["type_hypotheses"]:
        return False
    if state["issues"]:
        return False
    for clear in state["active_clear_constraints"]:
        d = distance(g, clear["position"])
        if clear["result"] == "success" and d > 20+tol:
            return False
        if clear["result"] != "success" and d <= 20:
            return False
    for obs in state["active_observations"]:
        p, result = obs["position"], obs["result"]
        d = distance(g, p)
        visible = d <= radius
        if direction_deg is not None:
            a = math.radians(direction_deg)
            visible = visible and ((p[0]-g[0])*math.cos(a)+(p[1]-g[1])*math.sin(a) >= -tol)
        if result == "no_signal":
            if visible:
                return False
        elif not visible:
            return False
        elif result == "near":
            if d > 5+tol:
                return False
        else:
            if d <= 5 or abs(wrap_angle_deg(bearing_deg(p, g)-obs["angle"])) > epsilon_deg+tol:
                return False
    return True


def archive_requests(requests_path, output_path=None, *, problem, case_id=None,
                     epsilon_deg=1.0051, post_exit_counts=None):
    path = Path(requests_path)
    if output_path is not None and Path(output_path).resolve() == path.resolve():
        raise ValueError("output must not overwrite the input request journal")
    result = build_archive(read_request_rows(path), problem=problem, case_id=case_id,
                           epsilon_deg=epsilon_deg, post_exit_counts=post_exit_counts)
    result["requests_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if output_path is not None:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)+"\n",
                          encoding="utf-8")
    return result
