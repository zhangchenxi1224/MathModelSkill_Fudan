#!/usr/bin/env bash
set -euo pipefail
task_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$task_dir/run.py" run \
  --manifest "${1:-$task_dir/results/research_ready_final/manifest.json}" \
  --partition "${2:-development}" --workers "${3:-4}" "${@:4}"
