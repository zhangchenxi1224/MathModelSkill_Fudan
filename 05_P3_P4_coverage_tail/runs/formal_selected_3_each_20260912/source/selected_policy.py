"""Selected next-round policy; public client and problem only."""
from pathlib import Path
import json
from next_policy import make_solver as make_candidate

ROOT=Path(__file__).resolve().parent

def selection():
    return json.loads((ROOT/"release.json").read_text(encoding="utf-8"))

def make_solver(client,problem,decision_log=None):
    choice=selection()["policies"][str(problem)]
    return make_candidate(client,problem,arm=choice['candidate'],decision_log=decision_log)
