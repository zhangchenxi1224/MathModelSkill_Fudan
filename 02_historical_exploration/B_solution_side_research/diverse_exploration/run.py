"""Portable local experiment CLI; never connects to the official simulator."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'vendor'))
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    from diverse.experiment import main
    main()
