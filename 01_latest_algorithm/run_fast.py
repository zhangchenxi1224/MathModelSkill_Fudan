"""Same policies, skipping duplicate candidate optical route evaluations."""
from run import main
from feedback import planner
from feedback.fast_cover import build_cover
planner.build_cover = build_cover
if __name__ == "__main__": main()
