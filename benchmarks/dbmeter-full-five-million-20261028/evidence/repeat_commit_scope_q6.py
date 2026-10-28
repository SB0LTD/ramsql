import pathlib,subprocess,sys
root=pathlib.Path.cwd()
for trial,labels in [(2,['commit','before']),(3,['before','commit'])]:
 for label in labels:
  template=(root/'.sig-cache/dbmeter_isolated_ramsql_w8.py').read_text()
  template=template.replace('dbmeter-isolated-full-ramsql-w8',f'dbmeter-isolated-full-ramsql-{label}-repeat{trial}')
  template=template.replace("['q5_high_value_customers','q6_store_category_mix']","['q6_store_category_mix']")
  if label=='commit':template=template.replace('dbmeter-round-stream-fast/bin','dbmeter-commit-scope-fast/bin')
  path=root/f'.sig-cache/dbmeter_commit_{label}_repeat{trial}.py';path.write_text(template)
  print('Starting',label,'trial',trial,flush=True)
  with (root/f'.sig-cache/dbmeter-commit-{label}-repeat{trial}.log').open('w') as log:subprocess.run([sys.executable,str(path)],stdout=log,stderr=subprocess.STDOUT,check=True)
  print('Finished',label,'trial',trial,flush=True)
