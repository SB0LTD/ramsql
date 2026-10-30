"""Validate and retain completed CI A/B evidence. No benchmark is run locally."""
import hashlib,json,pathlib,shutil,subprocess,sys
root=pathlib.Path.cwd();reviewer=(root/'../dbMeter').resolve()
revision='6064636e13f274af1aad0763d288971ea6855b31'
source=reviewer/'.sig-cache/typed-results-db05dcd6689c4241b1e85710fa14a231'
target=root/'../ramsql-benchmark-evidence/benchmarks/ci-typed-group-40962049999'
assert not target.exists()
sys.path.insert(0,str(reviewer/'ci'))
import paired_benchmark
for name in ('paired_benchmark.py','evidence.py','live_benchmark.py'):
    original=subprocess.check_output(['git','show',revision+':ci/'+name],cwd=reviewer)
    assert (reviewer/'ci'/name).read_bytes().replace(b'\r\n',b'\n')==original.replace(b'\r\n',b'\n')
# Validate against the recorded run's immutable pins, not a later candidate.
paired_benchmark.VERSIONS=json.loads(subprocess.check_output(['git','show',revision+':ci/versions.json'],cwd=reviewer))
folder=source/'paired-sales-subset';campaign=json.loads((folder/'campaign.json').read_text())
assert campaign['completed'] and not campaign.get('error') and not campaign['gaps']
assert campaign['ci']['run_id']=='40962049999' and campaign['ci']['run_attempt']=='1'
assert campaign['input_revision']==revision and len(campaign['runs'])==24
computed=paired_benchmark.summarize_pairs(folder,campaign)
assert computed==json.loads((folder/'paired-summary.json').read_text())
target.mkdir()
for phase in ('paired-sales-subset','live-sales-subset'):
    origin=source/phase;dest=target/phase;dest.mkdir()
    for path in sorted(origin.rglob('*')):
        if not path.is_file():continue
        if path.suffix not in ('.json','.xml','.csv') and path.name not in ('summary.md','paired-summary.md','local-summary.txt'):continue
        output=dest/path.relative_to(origin);output.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,output)
scripts=target/'scripts';scripts.mkdir()
for name in ('paired_benchmark.py','evidence.py','live_benchmark.py','runner.py','bootstrap.py','versions.json'):
    (scripts/name).write_bytes(subprocess.check_output(['git','show',revision+':ci/'+name],cwd=reviewer))
shutil.copy2(__file__,scripts/'retain_typed_ci.py')
files={p.relative_to(target).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(target.rglob('*')) if p.is_file()}
(target/'sha256.json').write_text(json.dumps(files,indent=2)+'\n')
print('Validated all 24 paired original-duration cases and original checks; retained',len(files),'hashed files.')
