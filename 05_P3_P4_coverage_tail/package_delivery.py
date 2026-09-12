from pathlib import Path
import json,zipfile
ROOT=Path(__file__).resolve().parent

def main():
    output=ROOT/'delivery/coverage_tail_iteration_20260912_complete.zip'
    files=[p for p in ROOT.rglob('*') if p.is_file() and 'delivery' not in p.relative_to(ROOT).parts and '__pycache__' not in p.parts and p.suffix!='.pyc']
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED,compresslevel=3) as z:
        for p in files:z.write(p,str(p.relative_to(ROOT)))
    print(json.dumps(dict(archive=str(output),files=len(files),bytes=output.stat().st_size),ensure_ascii=False))

if __name__=='__main__':main()
