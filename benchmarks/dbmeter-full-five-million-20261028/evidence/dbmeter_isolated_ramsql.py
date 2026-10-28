import pathlib,subprocess,sys
template=pathlib.Path('.sig-cache/dbmeter_full_ramsql_fixed.py').read_text()
template=template.replace('from benchmark import process_memory','from benchmark import process_memory\nfrom benchmark_pk_scaling import cpu_times')
template=template.replace('for clients in [1,8]','for clients in [8]')
template=template.replace("'load.warmup=5s'","'load.warmup=15s'").replace("'load.duration=15s'","'load.duration=60s'")
template=template.replace('   command=java+tail','   command=java+tail\n   clocks_before=cpu_times(server.process);began=time.monotonic()')
template=template.replace('dict(name=name,argv=command,returncode=code)','dict(name=name,argv=command,returncode=code,wall_seconds=time.monotonic()-began,cpu_seconds_including_warmup=sum(cpu_times(server.process)[k]-clocks_before[k] for k in clocks_before))')
template=template.replace("report['loaded']=True;","report['machine_after_load']=json.loads(subprocess.check_output([str(binary),'--machine-capacity']));report['loaded']=True;")
for workers in [4,8]:
 source=template.replace("dbmeter-full-fixed'",f"dbmeter-isolated-full-ramsql-w{workers}'").replace("'--workers','4'",f"'--workers','{workers}'")
 path=pathlib.Path(f'.sig-cache/dbmeter_isolated_ramsql_w{workers}.py');path.write_text(source)
 print('Starting isolated RAMSQL',workers,'workers; other databases stopped',flush=True)
 with pathlib.Path(f'.sig-cache/dbmeter-isolated-ramsql-w{workers}.log').open('w') as log:
  subprocess.run([sys.executable,str(path)],stdout=log,stderr=subprocess.STDOUT,check=True)
 print('Completed isolated RAMSQL',workers,'workers',flush=True)
