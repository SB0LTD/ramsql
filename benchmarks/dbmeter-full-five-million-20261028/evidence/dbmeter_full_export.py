import csv,hashlib,json,pathlib,re,subprocess,time
root=pathlib.Path('.sig-cache/dbmeter-full').resolve();data=root/'data';data.mkdir(exist_ok=True)
psql=pathlib.Path('../dbMeter/tools/postgres18/pgsql/bin/psql.exe').resolve()
schema=[
 ('regions',5,'id INT PRIMARY KEY,name TEXT NOT NULL',{1}),
 ('stores',200,'id INT PRIMARY KEY,region_id INT NOT NULL,name TEXT NOT NULL',{2}),
 ('categories',20,'id INT PRIMARY KEY,name TEXT NOT NULL',{1}),
 ('products',1000,'id INT PRIMARY KEY,category_id INT NOT NULL,unit_price_cents INT NOT NULL',set()),
 ('customers',50000,'id INT PRIMARY KEY,segment TEXT NOT NULL',{1}),
 ('sales',5000000,'id BIGINT PRIMARY KEY,product_id INT NOT NULL,store_id INT NOT NULL,customer_id INT NOT NULL,sale_date DATE NOT NULL,quantity INT NOT NULL,discount_pct INT NOT NULL,amount_cents BIGINT NOT NULL',{4})]
def digest(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
 return h.hexdigest()
report=dict(schema=1,method='Exact native PostgreSQL data export after all seven original 5M CSV checks passed; ordered by PK; unchanged values converted to bounded INSERT batches of 50. No sample data or formula reimplementation.',tables=[])
target=root/'ramsql_load.sql'
with target.open('w',encoding='utf-8',newline='\n') as output:
 for table,expected,columns,quoted in schema:
  path=data/(table+'.csv')
  if not path.exists():
   with path.open('wb') as export:
    subprocess.run([str(psql),'-h','127.0.0.1','-p','55439','-U','dbmeter','-d','postgres','-q','-v','ON_ERROR_STOP=1','-c',f'COPY (SELECT * FROM dbmeter_agg.{table} ORDER BY id) TO STDOUT WITH (FORMAT CSV,HEADER)'],stdout=export,check=True)
  output.write(f'CREATE TABLE {table}({columns});\n')
  count=0;batch=[]
  with path.open(encoding='utf-8',newline='') as raw:
   reader=csv.reader(raw);header=next(reader)
   for row in reader:
    assert len(row)==len(header) and all(value!='' for value in row)
    values=[]
    for index,value in enumerate(row):
     if index in quoted:values.append("'"+value.replace("'","''")+"'")
     else:
      assert re.fullmatch('-?[0-9]+',value),(table,index,value)
      values.append(value)
    batch.append('('+','.join(values)+')');count+=1
    if len(batch)==50:
     output.write(f'INSERT INTO {table} VALUES'+','.join(batch)+';\n');batch=[]
   if batch:output.write(f'INSERT INTO {table} VALUES'+','.join(batch)+';\n')
  assert count==expected,(table,count,expected)
  report['tables'].append(dict(table=table,rows=count,csv_sha256=digest(path),csv_bytes=path.stat().st_size))
  print(table,count,flush=True)
report.update(sql_sha256=digest(target),sql_bytes=target.stat().st_size)
(root/'transfer-provenance.json').write_text(json.dumps(report,indent=2)+'\n')
