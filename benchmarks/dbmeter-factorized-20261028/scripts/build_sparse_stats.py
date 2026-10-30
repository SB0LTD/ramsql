from pathlib import Path
import hashlib,json,os,re,subprocess,sys,tarfile,urllib.request
root=Path.cwd();mode=sys.argv[1];assert mode in ('on','off')
name='sparse-stats-'+mode;source=root/('.sig-cache/dbmeter-'+name+'-source');assert not source.exists()
revision=subprocess.check_output(['git','rev-parse','00b4bb2'],cwd=root/'../zpm-factorized-execution',text=True).strip()
url='https://github.com/SB0LTD/zpm/archive/'+revision+'.tar.gz'
archive=root/('.sig-cache/zpm-'+name+'.tar.gz');urllib.request.urlretrieve(url,archive)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
snapshot=root/('.sig-cache/ramsql-'+name+'-source.tar')
base='0cf7784e4965cd8daba7f7ae7b15553d72ab5c36'
subprocess.run(['git','archive','--format=tar','--output',str(snapshot),base],check=True)
source.mkdir()
with tarfile.open(snapshot) as bundle:bundle.extractall(source,filter='data')
p=source/'build.sig.zon';s=p.read_text()
s=re.sub(r'https://github.com/SB0LTD/zpm/archive/[0-9a-f]{40}.tar.gz',url,s)
s=re.sub(r'\.hash = "[0-9a-f]{64}"',f'.hash = "{digest}"',s);p.write_text(s)
p=source/'src/server.sig';s=p.read_text();anchor='    .compact_integral_scans = profile.compact_integral_scans,'
assert s.count(anchor)==1
patch='    .factorized_aggregates = true,'+chr(10)+'    .block_presence_filters = true,'+chr(10)+'    .sparse_factor_predicates = '+('true' if mode=='on' else 'false')+','
p.write_text(s.replace(anchor,anchor+'\n'+patch))
env=dict(os.environ,SIG_LIB_DIR=str(root/'.sig-cache/portable-toolchain-0df/lib'))
prefix=root/('.sig-cache/dbmeter-'+name+'-fast')
command=[r'C:\Users\Shado\AppData\Local\Programs\Sig\0.6.4\bin\sig.exe','build','-Doptimize=ReleaseFast','-j2','--cache-dir',str(root/('.sig-cache/dbmeter-'+name+'-cache')),'--prefix',str(prefix)]
p=source/'src/server.sig';s=p.read_text();anchor='        const reply=try session.handle(&self.connection,input,output);';assert s.count(anchor)==1;p.write_text(s.replace(anchor,anchor+'\n        const measured = context.workspace.query.stream_stats;\n        if (candidate == .execute and (measured.factor_plans != 0 or measured.nested_factor_plans != 0)) {\n            var stats_buffer: [1024]u8 = undefined;\n            const stats_line = try sig.fmt.bufPrint(&stats_buffer,\n                "DIAGNOSTIC factor={d} keys={d} selected={d} nested={d} nested_keys={d} rejected_blocks={d} sparse_masks={d} sparse_rows={d} rows={d} groups={d} probes={d}\\n",\n                .{measured.factor_plans, measured.factor_keys, measured.factor_selected_rows,\n                  measured.nested_factor_plans, measured.nested_factor_keys,\n                  measured.presence_rejected_blocks, measured.sparse_factor_masks,\n                  measured.sparse_factor_rows, measured.rows, measured.groups, measured.probes});\n            try sig.io.File.stderr().writeStreamingAll(.{},stats_line);\n        }\n'))
record=dict(ramsql_source=base,zpm_source=revision,zpm_archive_sha256=digest,server_config_patch=patch,command=command)
path=root/('.sig-cache/'+name+'-build-provenance.json');path.write_text(json.dumps(record,indent=2)+'\n')
subprocess.run(command,cwd=source,env=env,check=True)
record['binary_sha256']=hashlib.sha256((prefix/'bin/ramsql.exe').read_bytes()).hexdigest();path.write_text(json.dumps(record,indent=2)+'\n')
