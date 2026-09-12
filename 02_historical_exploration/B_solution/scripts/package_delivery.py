"""Create a reproducible research package; keep private official JSONL/screenshots local.

Official encrypted logs are copied unchanged under their original filenames.
This is not a completed formal-test submission until six official runs exist.
"""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'output'


def selected(path):
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if any(x in {'__pycache__', '.pytest_cache'} for x in parts):
        return False
    if len(parts) == 1:
        return path.name in {'README.md', 'pyproject.toml', 'run.py', '.gitignore'}
    if parts[0] in {'src', 'tests', 'docs', 'configs', 'scripts', 'sources'}:
        return True
    if parts[:2] == ('output', 'pdf'):
        return path.suffix.lower() == '.pdf'
    if parts[:2] == ('tmp', 'pdfs'):
        return path.name in {'paper_visual_qa.json', 'paper_build_report.json'}
    if parts[0] != 'results' or len(parts) < 3:
        return False
    if parts[1] == 'official_practice':
        if parts[2] == 'original_logs':
            return path.suffix == '.jlog'
        return path.name in {'summary.md', 'summary.csv', 'summary.json', 'post_exit_audit.json', 'result.json'}
    return parts[1] in {'tune', 'test', 'stress', 'sensitivity', 'summary', 'figures',
                        'q12', 'q12_final', 'final_regression', 'validation', 'code_snapshots'}


def main():
    OUT.mkdir(exist_ok=True)
    files = sorted(p for p in ROOT.rglob('*') if p.is_file() and selected(p))
    manifest = [{'path': p.relative_to(ROOT).as_posix(), 'bytes': p.stat().st_size,
                 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
    private_note = (
        'Research/reproduction package, 2026-09-11. Formal Q3/Q4 runs are pending user authorization.\n'
        'Original official request JSONL and account screenshots remain in the local workspace and are omitted here.\n'
        'Official encrypted .jlog files are unchanged originals with their original names.\n'
        'Self-built cases and raw logs are included. Solver receives no hidden case truth.\n'
        'Read README.md and docs/completion_checklist.md first.\n')
    destination = OUT/'B题研究与复现材料.zip'
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for p, row in zip(files, manifest):
            archive.write(p, 'B_solution/'+row['path'])
        archive.writestr('B_solution/PACKAGE_MANIFEST.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr('B_solution/PACKAGE_NOTE.txt', private_note)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        for row in manifest:
            assert hashlib.sha256(archive.read('B_solution/'+row['path'])).hexdigest() == row['sha256']
    report = {'package': destination.name, 'file_count': len(files), 'bytes': destination.stat().st_size,
              'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
              'files_verified': len(manifest), 'formal_results': 'pending authorization',
              'private_official_request_logs_and_screenshots_excluded': True}
    (OUT/'delivery_manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
