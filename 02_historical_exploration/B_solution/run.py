"""Run without installing the project: python run.py local --problem 4."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent/"src"))
from bsolver.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
