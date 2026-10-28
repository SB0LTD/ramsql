"""Retain complete paired evidence without copying private SQL/data/drivers."""
import argparse, hashlib, json, pathlib, shutil, statistics, xml.etree.ElementTree as ET

p=argparse.ArgumentParser();p.add_argument('phase',choices=('factor','nested','pcores'));a=p.parse_args()
source=pathlib.Path(__file__).resolve().parent
destination=source.parent.parent/'ramsql-benchmark-evidence/benchmarks/dbmeter-factorized-20261028'
destination.mkdir(exist_ok=True)
base=source/('dbmeter-nested-pcores-paired' if a.phase=='pcores' else f'dbmeter-{a.phase}-paired')
pairs=json.loads((base/'pairs.json').read_text())
assert 'error' not in pairs and len(pairs['runs'])==6 and 'summary' in pairs
expected=[(1,'on'),(1,'off'),(2,'off'),(2,'on'),(3,'on'),(3,'off')]
assert [(r['pair'],r['mode']) for r in pairs['runs']]==expected
target=destination/a.phase
if target.exists():
    assert list(target.iterdir())==[target/'pairs.json'] and (target/'pairs.json').read_bytes()==(base/'pairs.json').read_bytes(), 'Refuse existing complete/different evidence'
else: target.mkdir()
shutil.copy2(base/'pairs.json',target/'pairs.json')
proof_phase='nested' if a.phase=='pcores' else a.phase
proofs={mode:json.loads((source/f'{proof_phase}-{mode}-build-provenance.json').read_text()) for mode in ('on','off')}
assert proofs['on']['zpm_source']==proofs['off']['zpm_source']
assert proofs['on']['zpm_archive_sha256']==proofs['off']['zpm_archive_sha256']
for mode,proof in proofs.items():
    (target/(mode+'-build-provenance.json')).write_text(json.dumps(proof,indent=2)+'\n')
cases={'q5_high_value_customers','q6_store_category_mix'}
for run in pairs['runs']:
    folder=base/run['directory'];report=json.loads((folder/'ramsql-full-results.json').read_text())
    assert report['input_sha256']=='9b0e1e556433483e015b141eb90425fbe3c96e60cb4170d9baafde9d85de2d0d'
    assert report['supported_checks_passed'] and 'after_queries_memory' in report
    if a.phase=='pcores': assert report['diagnostic_cpu_policy']['server_affinity']=='0xffff' and not report['diagnostic_cpu_policy']['global_settings_changed']
    assert run['binary_sha256']==report['binary_sha256']==proofs[run['mode']]['binary_sha256']
    full=ET.parse(folder/'ramsql-all-checks.xml').getroot();assert int(full.attrib['tests'])==7
    checked=ET.parse(folder/'ramsql-supported-checks.xml').getroot();assert int(checked.attrib['tests'])==3 and int(checked.attrib['failures'])==0 and int(checked.attrib['errors'])==0
    output=target/run['directory'];output.mkdir()
    for name in ('ramsql-full-results.json','ramsql-all-checks.xml','ramsql-supported-checks.xml'):shutil.copy2(folder/name,output/name)
    paths=list(folder.glob('*/local.json'));assert len(paths)==2
    found=set()
    for path in paths:
        raw=json.loads(path.read_text());assert len(raw['scenarios'])==1
        case,metric=next(iter(raw['scenarios'].items()));found.add(case)
        assert metric['count']>0 and metric['errors']==0 and not metric['errorMessages']
        assert metric['latencyMicros']['count']==metric['count']
        seconds=(raw['measuredEnd']-raw['measuredStart'])/1000
        assert 59<=seconds<=90 and abs(run['qps'][case]-metric['count']/seconds)<1e-9
        sub=output/path.parent.name;sub.mkdir()
        for name in ('local.json','local-summary.txt','local-timeline.csv'):shutil.copy2(path.parent/name,sub/name)
    assert found==cases
ratios={case:[next(r['qps'][case] for r in pairs['runs'] if r['pair']==pair and r['mode']=='on')/next(r['qps'][case] for r in pairs['runs'] if r['pair']==pair and r['mode']=='off') for pair in range(1,4)] for case in sorted(cases)}
(target/'ratios.json').write_text(json.dumps(dict(paired_on_over_off=ratios,paired_median={case:statistics.median(values) for case,values in ratios.items()},universal_advantage_proven=False,complete_original_suite_passed=False,merge_dbmeter_allowed=False),indent=2)+'\n')
files={str(path.relative_to(target)).replace('\\','/'):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(target.rglob('*')) if path.is_file()}
(target/'sha256.json').write_text(json.dumps(files,indent=2)+'\n')
print(target)
