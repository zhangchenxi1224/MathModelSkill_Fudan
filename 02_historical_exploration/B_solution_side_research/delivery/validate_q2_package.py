from pathlib import Path
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from build_q2_package import GUIDE

base = Path(__file__).resolve().parent
state = json.loads((base/'q2_package_state.json').read_text(encoding='utf-8'))
package, archive, extract = (Path(state[k]) for k in ('package','archive','extract'))
(package/'README_先读我.md').write_text(GUIDE, encoding='utf-8')

def included():
    return sorted(p for p in package.rglob('*') if p.is_file()
                  and not any(x in ('__pycache__','.pytest_cache') for x in p.parts)
                  and p.suffix not in ('.pyc','.pyo'))

def manifest():
    content = {p.relative_to(package).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in included() if p.name != 'MANIFEST.sha256.json'}
    data = {'algorithm':'SHA256', 'scope':'all packaged files except this manifest itself', 'files':content}
    (package/'MANIFEST.sha256.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def compress():
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in included():
            z.write(p, (Path(package.name)/p.relative_to(package)).as_posix())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None

def unpack(destination):
    if destination.exists():
        raise RuntimeError(f'Validation extraction directory already exists: {destination}')
    with zipfile.ZipFile(archive) as z:
        for name in z.namelist():
            if Path(name).is_absolute() or '..' in Path(name).parts:
                raise RuntimeError('Unsafe archive member')
        z.extractall(destination)
    return destination/package.name

env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
checks=[]
def run(label, args, cwd):
    result=subprocess.run(args,cwd=cwd,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace')
    checks.append({'check':label,'exit_code':result.returncode,'stdout':result.stdout.strip(),'stderr':result.stderr.strip()})
    print(json.dumps({'check':label,'exit_code':result.returncode,'stdout':result.stdout.strip()},ensure_ascii=True),flush=True)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)

manifest()
compress()
unpacked=unpack(extract)
project=unpacked/'q2_certified_frontier'
run('unpacked_file_checksums',[sys.executable,'verify_delivery.py'],unpacked)
run('unpacked_test_suite',[sys.executable,'-m','pytest','-q','-p','no:cacheprovider'],project)
smoke = '''from pathlib import Path
import json
from study import select_from_first
import bsolver.geometry as geometry
root=Path.cwd()
assert Path(geometry.__file__).resolve().is_relative_to(root.resolve())
selected=select_from_first((0.,0.),0.,angle_step=5.)
expected=json.loads((root/'results/q2_precision_v2/bound_convergence.json').read_text(encoding='utf-8'))
for mode,row in selected['selections'].items():
    assert row['safety']['certified']
    assert all(abs(a-b)<1e-8 for a,b in zip(row['point'],expected[mode]['point']))
print(json.dumps({'strategies':list(selected['selections']), 'canonical_selection_matches_saved_result':True, 'local_vendor':True}))
'''
run('unpacked_canonical_selection',[sys.executable,'-c',smoke],project)
run('unpacked_cli_entrypoint',[sys.executable,'study.py','--help'],project)
receipt={'validated_at_local':dt.datetime.now().isoformat(timespec='seconds'), 'python':sys.version,
         'validated_from_extracted_zip':True,'checks':checks,
         'full_1344_row_study_rerun_during_packaging':False,
         'experiment_scope':'Existing completed v2 results are shipped unchanged; packaging verification ran tests and one first-observation selection.',
         'core_or_result_changes':False}
(package/'DELIVERY_VALIDATION.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
manifest()
compress()
final_unpacked=unpack(Path(state['build'])/'final_extracted_check')
run('final_zip_checksums',[sys.executable,'verify_delivery.py'],final_unpacked)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
archive.with_suffix('.zip.sha256.txt').write_text(digest+'  '+archive.name+'\n',encoding='utf-8')
summary={'archive':str(archive),'bytes':archive.stat().st_size,'sha256':digest,
         'packaged_files':len(included()),'unpacked_tests_passed':True,'status':'ready'}
(base/'q2_delivery_ready.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=True),flush=True)
