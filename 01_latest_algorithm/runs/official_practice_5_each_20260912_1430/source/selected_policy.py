"""Frozen experimental selection. The caller supplies only a RobotClient and problem."""
from pathlib import Path
import json
from run import base_specs
from feedback.solver import make_solver as make_arm_solver
from feedback import planner
from feedback.fast_cover import build_cover

ROOT=Path(__file__).resolve().parent
planner.build_cover=build_cover

def selection():
    return json.loads((ROOT/"selection.json").read_text(encoding="utf-8"))

def make_solver(client,problem,decision_log=None):
    choice=selection()["policies"][str(problem)]
    return make_arm_solver(client,problem,base_specs()[problem],choice["arm"],
                           options=choice["options"],decision_log=decision_log)
