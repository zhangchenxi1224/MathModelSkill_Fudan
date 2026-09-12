"""Verify the original package files; later user outputs are allowed."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
manifest = json.loads((root / "MANIFEST.sha256.json").read_text(encoding="utf-8"))
failed = []
for name, expected in manifest["files"].items():
    path = root / name
    if not path.is_file():
        failed.append({"path": name, "error": "missing"})
    elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        failed.append({"path": name, "error": "sha256 mismatch"})
print(json.dumps({"checked_files": len(manifest["files"]), "failures": failed}, ensure_ascii=False, indent=2))
raise SystemExit(bool(failed))
