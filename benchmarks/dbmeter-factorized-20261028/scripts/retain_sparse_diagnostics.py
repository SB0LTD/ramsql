import hashlib,json,pathlib,shutil
root=pathlib.Path.cwd();cache=root/'.sig-cache'
target=root/'../ramsql-benchmark-evidence/benchmarks/dbmeter-factorized-20261028/diagnostics'
target.mkdir(exist_ok=False)
results={}
for mode in ('on','off'):
    folder=cache/f'sparse-stats-{mode}-diagnostic'
    data=json.loads((folder/'counters.json').read_text())
    assert data['diagnostic_only'] and not data['throughput_qualified']
    assert len(data['checks'])==2 and all(check['passed'] for check in data['checks'])
    results[mode]={}
    for check in data['checks']:
        assert len(check['records'])==1
        results[mode][check['case']]={key:int(value) for key,value in (item.split('=') for item in check['records'][0].split()[1:])}
    shutil.copy2(folder/'counters.json',target/(mode+'-counters.json'))
    proof=json.loads((cache/f'sparse-stats-{mode}-build-provenance.json').read_text())
    assert proof['binary_sha256']==data['binary_sha256']
    proof['diagnostic_only']=True
    proof['server_source_sha256']=hashlib.sha256((cache/f'dbmeter-sparse-stats-{mode}-source/src/server.sig').read_bytes()).hexdigest()
    proof['instrumentation']='After detached PostgreSQL execute, emit existing stream counters to stderr. See make_sparse_stats_builder.py for complete patch. No SQL text is emitted.'
    (target/(mode+'-build-provenance.json')).write_text(json.dumps(proof,indent=2)+'\n')
for case,enabled in results['on'].items():
    disabled=results['off'][case]
    assert enabled.keys()==disabled.keys()
    assert all(value==disabled[key] for key,value in enabled.items() if key not in ('sparse_masks','sparse_rows'))
assert results['on']['q6_store_category_mix']['sparse_masks']==44328
assert results['on']['q6_store_category_mix']['sparse_rows']==47987
assert results['off']['q6_store_category_mix']['sparse_masks']==0
assert results['on']['q5_high_value_customers']['sparse_masks']==0
scripts=target.parent/'scripts'
for name in ('make_sparse_stats_builder.py','build_sparse_stats.py','diagnose_sparse_stats.py','retain_sparse_diagnostics.py'):
    shutil.copy2(cache/name,scripts/name)
print('Both original full-data checks pass; logical path counters match except sparse-mask work.')
