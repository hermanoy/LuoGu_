"""Build a source release with a previously validated example (no host binaries)."""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
example=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT/'outputs/local-ready-example'
state=json.loads((example/'state.json').read_text(encoding='utf-8'))
if state.get('status')!='passed': raise SystemExit('Example has not passed')
current_code=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'luogu_data').glob('*.py')))).hexdigest()
if state.get('fingerprint',{}).get('pipeline')!=current_code:
    raise SystemExit('Example was validated against a different program version; regenerate before releasing.')
for files in state['stages'].values():
    for name,expected in files.items():
        actual=hashlib.sha256((example/name).read_bytes()).hexdigest()
        if actual!=expected: raise SystemExit('Artifact modified: '+name)
files=[ROOT/name for name in ('main.py','README.md','VALIDATION.md','pyproject.toml','requirements.txt','.gitignore',
                              'settings.example.toml','upload-adapter.example.json','setup.ps1','run.cmd')]
for folder,pattern in [('luogu_data','*.py'),('luogu_data','*.json'),('tests','*.py'),('examples','*.md'),('scripts','*.py'),('.github/workflows','*.yml')]:
    files+=sorted((ROOT/folder).glob(pattern))
destination=ROOT/'dist/LuoguData.zip';destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
    for path in sorted(set(files)):
        archive.write(path,path.relative_to(ROOT).as_posix())
    for name in ('standard.cpp','brute.cpp','generator.py','validator.py','wrong_1.cpp','wrong_2.cpp','testcases.zip',
                 'report.md','config.yml','scoring.json','spec.json','review.json','validation.json','cases.json'):
        archive.write(example/name,'sample_output/'+name)
    evidence=ROOT/'rules-check.json'
    if evidence.exists():archive.write(evidence,'verification/rules-check.json')
    diagnostic=ROOT/'outputs/local-doctor.json'
    if diagnostic.exists():archive.write(diagnostic,'verification/local-doctor.json')
with zipfile.ZipFile(destination) as archive:
    if archive.testzip() is not None: raise SystemExit('Release CRC failure')
print(destination)
print('bytes:',destination.stat().st_size)
